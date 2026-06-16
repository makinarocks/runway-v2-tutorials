"""
task_runner.py — 에너지 수요 예측 4-step 파이프라인 실행기

실행 방식 두 가지 (둘 다 같은 코드):
  - Code Server 수동: `python task_runner.py --step <step>` (Step 4 본문)
  - Airflow KubernetesPodOperator: Pod 안에서 같은 명령 실행 (Step 7 자동화)

데이터셋은 PVC(/mnt/data/dataset/) 에서 읽고, 태스크 간 아티팩트는 S3 로 공유한다.

데이터 구조:
  /mnt/data/dataset/
  ├── pred-demo-dataset/   # 학습용 (Q1.csv, Q2.csv, Q3.csv)
  └── pred-demo-testset/   # 평가용 (Q1.csv, Q2.csv, Q3.csv, Q4.csv)

학습 데이터:
  - PVC `/mnt/data/dataset/pred-demo-dataset/` 안의 모든 *.csv 자동 학습
  - 사용자가 새 분기 데이터를 추가 업로드하면 다음 DAG run 에 자동 포함
"""

import argparse
import json
import os
import pickle
import re
import tempfile
import warnings

import mlflow
import mlflow.pyfunc
from typing import List

import numpy as np
import pandas as pd

from config import (
    MLFLOW_TRACKING_URI,
    MLFLOW_S3_ENDPOINT_URL,
    S3_BUCKET,
    EXPERIMENT_NAME,
    MODEL_NAME,
    MODEL_REGISTRY_PATH,
    S3_ARTIFACT_PREFIX,
    load_secrets,
)

# =============================================================================
# [시크릿] __main__ 진입 시 셸 환경변수에서 채워짐 (Agent Injector 가 주입)
# =============================================================================
RUNWAY_API_KEY: str = ""
AWS_ACCESS_KEY_ID: str = ""
AWS_SECRET_ACCESS_KEY: str = ""


def _initialize_secrets() -> None:
    """Agent Injector 가 /vault/secrets/creds.env 로 주입한 시크릿을 module-level 변수로 받는다.
    셸에서 `source /vault/secrets/creds.env` 가 선행돼야 한다.
    """
    global RUNWAY_API_KEY, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
    data = load_secrets()
    AWS_ACCESS_KEY_ID     = data["aws_access_key_id"]
    AWS_SECRET_ACCESS_KEY = data["aws_secret_access_key"]
    RUNWAY_API_KEY        = data["runway_api_key"]


# =============================================================================
# [유틸] 범위 패턴 컬럼 해석 — 노트북의 get_from_col_name 로직 이식
#
# "열수요실적_{-24:0}" → ["열수요실적_-24", ..., "열수요실적_0"]
# "기상청예측_{1:72}"  → ["기상청예측_1", ..., "기상청예측_72"]
# "시간" 같은 단순 이름은 그대로 통과.
# =============================================================================
_RANGE_RE = re.compile(r"^(?P<prefix>.+)_\{\s*(?P<start>-?\d+)\s*:\s*(?P<end>-?\d+)\s*\}$")


def resolve_col_specs(df: pd.DataFrame, specs: List[str]) -> List[str]:
    """컬럼 스펙 목록을 실제 컬럼명 리스트로 해석. 없는 컬럼은 경고 후 skip."""
    resolved = []
    for spec in specs:
        spec = spec.strip()
        m = _RANGE_RE.match(spec)
        if m:
            prefix = m.group("prefix").strip()
            start, end = int(m.group("start")), int(m.group("end"))
            step = 1 if start <= end else -1
            for idx in range(start, end + step, step):
                col = f"{prefix}_{idx}"
                if col in df.columns:
                    resolved.append(col)
                else:
                    warnings.warn(f"컬럼 '{col}' 이 CSV 에 없음 — skip")
        else:
            if spec in df.columns:
                resolved.append(spec)
            else:
                warnings.warn(f"컬럼 '{spec}' 이 CSV 에 없음 — skip")
    if not resolved:
        raise KeyError(f"유효한 컬럼을 하나도 찾지 못했습니다: {specs}")
    return resolved


# =============================================================================
# [설정] 피처/타겟 컬럼 — 범위 패턴 그대로 보관 (CSV 로드 시 해석)
# Runway 1.0 Pipeline Parameters 와 동일한 형식
# =============================================================================
FEATURE_COL_SPECS = [
    "시간", "요일", "연중일수비율", "공휴일",
    "열수요실적_{-24:0}",
    "기상청실적_{-24:0}",
    "기상청예측_{1:72}",
]
TARGET_COL_SPECS = ["열수요실적_pred_{1:72}"]


# =============================================================================
# [설정] 데이터 경로 + 학습 파일 제어
# =============================================================================
DAG_RUN_ID = os.getenv("DAG_RUN_ID", "local")
S3_PREFIX  = f"energy-demand/dag-runs/{DAG_RUN_ID}"

TRAIN_FEATURES_KEY = f"{S3_PREFIX}/train_features.pkl"
TRAIN_TARGETS_KEY  = f"{S3_PREFIX}/train_targets.pkl"
EVAL_DATA_KEY      = f"{S3_PREFIX}/eval_data.pkl"  # Q별 개별 평가 데이터
MODEL_TRAINED_KEY  = f"{S3_PREFIX}/model_trained.pkl"
METRICS_JSON_KEY   = f"{S3_PREFIX}/metrics.json"

# PVC 마운트 경로
DATA_BASE      = "/mnt/data/dataset"
TRAIN_DIR      = os.path.join(DATA_BASE, "pred-demo-dataset")
EVAL_DIR       = os.path.join(DATA_BASE, "pred-demo-testset")

# 학습 데이터는 TRAIN_DIR 안의 모든 *.csv. 사용자가 추가 업로드하면 다음 DAG run 에 자동 포함.


# =============================================================================
# [설정] XGBoost 하이퍼파라미터
# =============================================================================
USE_GPU = os.getenv("USE_GPU", "false").lower() in ("1", "true", "yes")

XGB_PARAMS = {
    "learning_rate": 0.1,
    "max_depth": 8,
    "reg_alpha": 10,
    "n_estimators": 620,
    "objective": "reg:squarederror",
    "n_jobs": 1,
}
if USE_GPU:
    # HAMi vGPU 사용. DAG 의 train_model task 가 `nvidia.com/gpu=1` + `nvidia.com/gpumem=4000` 요청.
    XGB_PARAMS["device"]      = "cuda"
    XGB_PARAMS["tree_method"] = "hist"


# =============================================================================
# [S3 헬퍼]
# =============================================================================
def _s3():
    import boto3
    from botocore.config import Config
    # 무한 retry / hang 방지: connect 10s, read 60s, 총 3회 재시도
    cfg = Config(
        connect_timeout=10,
        read_timeout=60,
        retries={"max_attempts": 3, "mode": "standard"},
    )
    return boto3.client(
        "s3",
        endpoint_url=MLFLOW_S3_ENDPOINT_URL,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        config=cfg,
    )


def s3_upload_bytes(data: bytes, key: str) -> None:
    _s3().put_object(Bucket=S3_BUCKET, Key=key, Body=data)
    print(f"[S3] 업로드 완료: s3://{S3_BUCKET}/{key}", flush=True)


def s3_upload_pickle(obj, key: str) -> None:
    s3_upload_bytes(pickle.dumps(obj), key)


def s3_download_bytes(key: str) -> bytes:
    resp = _s3().get_object(Bucket=S3_BUCKET, Key=key)
    data = resp["Body"].read()
    print(f"[S3] 다운로드 완료: s3://{S3_BUCKET}/{key}", flush=True)
    return data


def s3_download_pickle(key: str):
    return pickle.loads(s3_download_bytes(key))


# =============================================================================
# [태스크] load_data
# =============================================================================
def load_data() -> None:
    """PVC 에서 CSV 를 읽어 피처/타겟 분리 후 S3 업로드.

    학습 데이터: TRAIN_DIR 안의 모든 *.csv 자동 로드 (사용자가 추가 업로드한 파일 포함).
    평가 데이터: EVAL_DIR 의 모든 CSV 를 Q별로 개별 로드 (Q1, Q2, Q3, Q4).
    """
    print(f"[load_data] 시작 — TRAIN_DIR={TRAIN_DIR}, S3_BUCKET={S3_BUCKET}, S3_ENDPOINT={MLFLOW_S3_ENDPOINT_URL}", flush=True)

    # ── 학습 데이터 ── (TRAIN_DIR 안 모든 *.csv)
    if not os.path.isdir(TRAIN_DIR):
        raise FileNotFoundError(f"학습 디렉토리 없음: {TRAIN_DIR}")
    train_paths = sorted(
        os.path.join(TRAIN_DIR, f)
        for f in os.listdir(TRAIN_DIR)
        if f.endswith(".csv")
    )
    if not train_paths:
        raise FileNotFoundError(f"학습 CSV 가 하나도 없음: {TRAIN_DIR}")

    print(f"[load_data] 학습 CSV 검출: {[os.path.basename(p) for p in train_paths]}", flush=True)
    print(f"[load_data] CSV 읽는 중...", flush=True)
    train_df = pd.concat([pd.read_csv(p) for p in train_paths], ignore_index=True)
    print(f"[load_data] CSV 로드 완료: {train_df.shape[0]}행", flush=True)

    feature_cols = resolve_col_specs(train_df, FEATURE_COL_SPECS)
    target_cols  = resolve_col_specs(train_df, TARGET_COL_SPECS)

    train_features = train_df[feature_cols].copy()
    train_targets  = train_df[target_cols].copy()
    print(f"[load_data] 학습 데이터: {train_features.shape[0]}행, 피처 {train_features.shape[1]}개, 타겟 {train_targets.shape[1]}개", flush=True)

    # ── 평가 데이터 (Q별 개별) ──
    eval_data = {}  # {"Q1": (features_df, targets_df), "Q2": ...}
    if os.path.isdir(EVAL_DIR):
        for fname in sorted(os.listdir(EVAL_DIR)):
            if not fname.endswith(".csv"):
                continue
            q_name = fname.replace(".csv", "")  # "Q1", "Q2", ...
            eval_df = pd.read_csv(os.path.join(EVAL_DIR, fname))
            ef = eval_df[resolve_col_specs(eval_df, FEATURE_COL_SPECS)].copy()
            et = eval_df[resolve_col_specs(eval_df, TARGET_COL_SPECS)].copy()
            eval_data[q_name] = (ef, et)
            print(f"[load_data] 평가 데이터 {q_name}: {len(ef)}행", flush=True)
    else:
        print(f"[load_data] 평가 디렉토리 없음: {EVAL_DIR} — 평가 skip", flush=True)

    print(f"[load_data] S3 업로드 시작 (3개 객체)...", flush=True)
    s3_upload_pickle(train_features, TRAIN_FEATURES_KEY)
    s3_upload_pickle(train_targets, TRAIN_TARGETS_KEY)
    s3_upload_pickle(eval_data, EVAL_DATA_KEY)
    print("[load_data] 완료", flush=True)


# =============================================================================
# [태스크] train_model
# =============================================================================
def train_model() -> None:
    """S3 에서 학습 데이터 다운로드 → 타겟별 XGBoost 모델 병렬 학습 → S3 업로드.

    `MultiOutputRegressor` 의 내부 joblib 출력 (`Done X tasks`) 은 총 개수가 안 보이고
    n_jobs=-1 이 그대로 찍혀서 가독성이 낮음. 대신 직접 ProcessPoolExecutor 로 풀고
    "X/72 완료" 형식의 진행률을 print 함.
    """
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from sklearn.multioutput import MultiOutputRegressor
    from xgboost import XGBRegressor

    train_features = s3_download_pickle(TRAIN_FEATURES_KEY)
    train_targets  = s3_download_pickle(TRAIN_TARGETS_KEY)

    X = train_features.values
    Y = train_targets.values
    n_targets = Y.shape[1]

    # n_jobs 실제 개수 결정.
    # - GPU 모드: 1 (단일 vGPU 메모리 contention 회피)
    # - CPU 모드: DAG 가 downward API 로 주입한 POD_CPU_LIMIT 우선. K8s CFS quota 기반 limit 을
    #   유일하게 신뢰할 수 있는 경로 (sched_getaffinity / loky.cpu_count 는 노드 전체 코어 반환).
    if USE_GPU:
        n_jobs = 1
    else:
        pod_cpu = os.getenv("POD_CPU_LIMIT")
        if pod_cpu and pod_cpu.isdigit() and int(pod_cpu) > 0:
            n_jobs = int(pod_cpu)
        else:
            # Fallback (예: 로컬 venv 직접 실행 시)
            candidates = []
            if hasattr(os, "sched_getaffinity"):
                candidates.append(len(os.sched_getaffinity(0)))
            try:
                from loky import cpu_count
                candidates.append(cpu_count())
            except Exception:
                pass
            candidates.append(os.cpu_count() or 4)
            n_jobs = min(candidates)

    print(f"[train_model] 학습 시작: {X.shape[0]}행, {X.shape[1]} 피처 → {n_targets} 타겟 (mode={'GPU' if USE_GPU else 'CPU'}, n_jobs={n_jobs})", flush=True)

    estimators = [None] * n_targets
    t_start = time.time()
    PROGRESS_EVERY = 5

    def _fit_one(idx: int):
        # 스레드에서 호출. XGBoost 가 학습 중 GIL 을 release 하므로 thread 로도 멀티코어 활용 가능.
        # ProcessPoolExecutor 와 달리 closure 도 그대로 사용 가능 (pickle 불필요).
        est = XGBRegressor(**XGB_PARAMS)
        est.fit(X, Y[:, idx])
        return idx, est

    def _print_progress(done: int):
        elapsed = time.time() - t_start
        print(f"[train_model] 진행: {done}/{n_targets} 완료 (elapsed {elapsed:.1f}s)", flush=True)

    if n_jobs == 1:
        # 단일 워커 — 순차 학습
        for idx in range(n_targets):
            _, est = _fit_one(idx)
            estimators[idx] = est
            done = idx + 1
            if done % PROGRESS_EVERY == 0 or done == n_targets:
                _print_progress(done)
    else:
        # CPU 다중 워커 — ThreadPoolExecutor. XGBoost C++ 코드가 GIL release.
        with ThreadPoolExecutor(max_workers=n_jobs) as ex:
            futures = [ex.submit(_fit_one, i) for i in range(n_targets)]
            done = 0
            for fut in as_completed(futures):
                idx, est = fut.result()
                estimators[idx] = est
                done += 1
                if done % PROGRESS_EVERY == 0 or done == n_targets:
                    _print_progress(done)

    total_elapsed = time.time() - t_start
    print(f"[train_model] 학습 완료: {n_targets} 타겟 (총 {total_elapsed:.1f}s)", flush=True)

    # MultiOutputRegressor 인터페이스 호환 — predict 시 동일 동작 (estimators_ 만 채우면 됨).
    model = MultiOutputRegressor(XGBRegressor(**XGB_PARAMS))
    model.estimators_     = estimators
    model.n_features_in_  = X.shape[1]

    s3_upload_pickle(model, MODEL_TRAINED_KEY)
    print("[train_model] 모델 S3 업로드 완료", flush=True)


# =============================================================================
# [태스크] evaluate_model — Q별 개별 평가 (노트북과 동일)
# =============================================================================
def _calc_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    from sklearn.metrics import mean_absolute_error, mean_squared_error
    eps = 1e-8
    rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))
    mae  = float(mean_absolute_error(y_true, y_pred))
    mask = np.abs(y_true) > eps
    mape = float(np.mean(np.abs((y_true[mask] - y_pred[mask]) / y_true[mask])) * 100.0) if np.any(mask) else float("nan")
    return {"rmse": rmse, "mae": mae, "mape": mape}


def evaluate_model() -> None:
    """S3 에서 모델 + Q별 평가 데이터 다운로드 → Q별 메트릭 계산 → S3 업로드."""
    model     = s3_download_pickle(MODEL_TRAINED_KEY)
    eval_data = s3_download_pickle(EVAL_DATA_KEY)  # {"Q1": (feat_df, targ_df), ...}

    if not eval_data:
        print("[evaluate_model] 평가 데이터 없음 — skip", flush=True)
        s3_upload_bytes(json.dumps({"per_quarter": {}, "overall": {}}).encode(), METRICS_JSON_KEY)
        return

    per_quarter = {}
    all_y_true = []
    all_y_pred = []

    for q_name, (feat_df, targ_df) in sorted(eval_data.items()):
        preds = model.predict(feat_df.values)
        target_cols = list(targ_df.columns)
        preds_df = pd.DataFrame(preds, columns=target_cols, index=feat_df.index)

        y_true = targ_df.values.ravel()
        y_pred = preds_df.values.ravel()
        q_metrics = _calc_metrics(y_true, y_pred)
        per_quarter[q_name] = q_metrics

        all_y_true.append(y_true)
        all_y_pred.append(y_pred)

        print(f"[evaluate_model] {q_name} → RMSE: {q_metrics['rmse']:.4f}, MAE: {q_metrics['mae']:.4f}, MAPE: {q_metrics['mape']:.2f}%", flush=True)

    # 전체 통합 메트릭
    combined_true = np.concatenate(all_y_true)
    combined_pred = np.concatenate(all_y_pred)
    overall = _calc_metrics(combined_true, combined_pred)

    metrics = {
        "overall": overall,
        "per_quarter": per_quarter,
        "total_eval_rows": sum(len(feat_df) for feat_df, _ in eval_data.values()),
    }

    print(f"[evaluate_model] Overall RMSE: {overall['rmse']:.4f}, MAE: {overall['mae']:.4f}, MAPE: {overall['mape']:.2f}%", flush=True)
    s3_upload_bytes(json.dumps(metrics, ensure_ascii=False).encode(), METRICS_JSON_KEY)
    print("[evaluate_model] 완료", flush=True)


# =============================================================================
# [태스크] log_to_mlflow
# =============================================================================
class RunwayModel(mlflow.pyfunc.PythonModel):
    """pyfunc 래퍼 — MultiOutputRegressor 를 MLflow Model Registry 에 등록하기 위한 어댑터.

    predict() 출력 컬럼명을 열수요실적_pred_{i} 로 설정하여
    GUI 가 동일 네이밍으로 미래 실측과 매칭할 수 있게 한다.
    """

    def __init__(self, model):
        self._model = model

    def predict(self, context, model_input):
        if isinstance(model_input, pd.DataFrame):
            X = model_input.values
        else:
            X = np.asarray(model_input)
        preds = self._model.predict(X)
        return pd.DataFrame(
            preds,
            columns=[f"열수요실적_pred_{i}" for i in range(1, 73)],
            index=getattr(model_input, "index", None),
        )


def log_to_mlflow() -> None:
    """S3 에서 모델 + 메트릭 다운로드 → MLflow 에 pyfunc 모델 등록."""
    import mlflow

    model     = s3_download_pickle(MODEL_TRAINED_KEY)
    metrics   = json.loads(s3_download_bytes(METRICS_JSON_KEY))
    eval_data = s3_download_pickle(EVAL_DATA_KEY)

    # input_example 용: 평가 데이터 중 첫 번째 Q 의 첫 행
    input_example = None
    if eval_data:
        first_feat, _ = next(iter(eval_data.values()))
        input_example = first_feat.iloc[:1]

    os.environ["MLFLOW_TRACKING_URI"]    = MLFLOW_TRACKING_URI
    os.environ["MLFLOW_TRACKING_TOKEN"]  = RUNWAY_API_KEY
    os.environ["MLFLOW_S3_ENDPOINT_URL"] = MLFLOW_S3_ENDPOINT_URL
    os.environ["AWS_ACCESS_KEY_ID"]      = AWS_ACCESS_KEY_ID
    os.environ["AWS_SECRET_ACCESS_KEY"]  = AWS_SECRET_ACCESS_KEY
    # Runway 의 S3 backend (SeaweedFS) 가 `application/json` ContentType 헤더를 거부 (400 Bad Request).
    # MLflow 가 자동으로 추가하는 ContentType 을 override 해서 우회.
    os.environ["MLFLOW_S3_UPLOAD_EXTRA_ARGS"] = '{"ContentType": "application/octet-stream"}'

    mlflow.set_experiment(EXPERIMENT_NAME)

    run_name = f"dag-{DAG_RUN_ID[:20]}"
    with mlflow.start_run(run_name=run_name):
        mlflow.log_params(XGB_PARAMS)
        mlflow.log_param("n_targets", 72)
        mlflow.log_param("total_eval_rows", metrics.get("total_eval_rows", 0))

        # Overall 메트릭
        overall = metrics.get("overall", {})
        if overall:
            mlflow.log_metrics({
                "overall_rmse": overall["rmse"],
                "overall_mae":  overall["mae"],
                "overall_mape": overall["mape"],
            })

        # Q별 메트릭
        for q_name, q_m in metrics.get("per_quarter", {}).items():
            mlflow.log_metrics({
                f"{q_name}_rmse": q_m["rmse"],
                f"{q_name}_mae":  q_m["mae"],
                f"{q_name}_mape": q_m["mape"],
            })

        runway_model = RunwayModel(model)
        log_kwargs = {
            "name": "model",
            "python_model": runway_model,
            "pip_requirements": [
                "xgboost>=2.0",
                "scikit-learn>=1.3",
                "pandas>=2.0",
                "numpy>=1.24",
            ],
            "registered_model_name": MODEL_NAME,
        }
        if input_example is not None:
            log_kwargs["input_example"] = input_example

        mlflow.pyfunc.log_model(**log_kwargs)
        print(f"[log_to_mlflow] 모델 등록 완료: {MODEL_NAME}", flush=True)

    print("[log_to_mlflow] MLflow 로깅 완료", flush=True)


# =============================================================================
# [태스크] copy_model_to_pvc — MLflow S3 → PVC `/mnt/data/m-<id>/`
# =============================================================================
def copy_model_to_pvc() -> None:
    """`log_to_mlflow` 가 등록한 모델 아티팩트를 MLflow S3 backend → PVC 로 복사.

    Runway 추론 엔드포인트는 PVC 의 `/mnt/data/m-<id>/` 에서 모델 파일을 읽으므로,
    이 task 가 그 매핑을 자동화한다. Code Server 에서 `download_model.py` 를 수동으로
    돌릴 필요 없음 (수동 도구는 디버깅 / 본 task 가 실패한 경우의 fallback 으로 유지).

    log_to_mlflow 직후 실행되므로 'S3 의 가장 최근 모델' = 'log_to_mlflow 가 막 등록한 모델'.
    """
    s3 = _s3()
    resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=S3_ARTIFACT_PREFIX)
    objects = resp.get("Contents", [])
    if not objects:
        raise RuntimeError(f"S3 에 모델 아티팩트 없음: s3://{S3_BUCKET}/{S3_ARTIFACT_PREFIX}")

    latest = max(objects, key=lambda o: o["LastModified"])
    parts = latest["Key"].split("/")
    models_idx = parts.index("models")
    model_id = parts[models_idx + 1]   # m-<hex>
    print(f"[copy_model_to_pvc] 최신 모델 ID: {model_id} (LastModified={latest['LastModified'].isoformat()})", flush=True)

    prefix = f"{S3_ARTIFACT_PREFIX}{model_id}/artifacts/"
    artifacts = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=prefix).get("Contents", [])
    if not artifacts:
        raise RuntimeError(f"아티팩트 디렉토리 비어있음: {prefix}")

    save_dir = os.path.join(MODEL_REGISTRY_PATH, model_id)
    os.makedirs(save_dir, exist_ok=True)
    print(f"[copy_model_to_pvc] 저장 경로: {save_dir} (파일 {len(artifacts)}개)", flush=True)

    for obj in artifacts:
        key = obj["Key"]
        filename = os.path.basename(key)
        if not filename:
            continue
        local_path = os.path.join(save_dir, filename)
        print(f"  다운로드: {filename} ({obj['Size']} bytes)", flush=True)
        s3.download_file(S3_BUCKET, key, local_path)

    print(f"[copy_model_to_pvc] 완료: {save_dir}", flush=True)
    print(f"[copy_model_to_pvc] Runway 콘솔 모델 경로 입력값: {model_id}", flush=True)

    # 다음 단계 (사람 또는 GUI) 를 위해 model_id 를 S3 의 이번 dag-run 폴더에 마커로 저장.
    s3_upload_bytes(model_id.encode(), f"{S3_PREFIX}/model_id.txt")


# =============================================================================
# [진입점]
# =============================================================================
STEP_MAP = {
    "load_data":         load_data,
    "train_model":       train_model,
    "evaluate_model":    evaluate_model,
    "log_to_mlflow":     log_to_mlflow,
    "copy_model_to_pvc": copy_model_to_pvc,
}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Energy demand prediction task runner")
    parser.add_argument("--step", required=True, choices=STEP_MAP.keys())
    args = parser.parse_args()

    _initialize_secrets()
    STEP_MAP[args.step]()
