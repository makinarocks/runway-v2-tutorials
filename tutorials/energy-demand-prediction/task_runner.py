"""
task_runner.py — KubernetesPodOperator 용 에너지 수요 예측 태스크 실행기

Docker 이미지에 번들되어 Airflow DAG 가 각 태스크를 별도 K8s Pod 로 실행.
`python task_runner.py --step <load_data|train_model|evaluate_model|log_to_mlflow>`

데이터셋은 PVC(/mnt/data/dataset/)에서 읽고, 태스크 간 아티팩트는 S3로 공유.

데이터 구조:
  /mnt/data/dataset/
  ├── pred-demo-dataset/   # 학습용 (Q1.csv, Q2.csv, Q3.csv)
  └── pred-demo-testset/   # 평가용 (Q1.csv, Q2.csv, Q3.csv, Q4.csv)

학습 모드:
  - 기존 학습 (TRAIN_FILES=Q1.csv): Q1만으로 학습
  - 재학습 (TRAIN_FILES=Q1.csv,Q2.csv,Q3.csv): Q1+Q2+Q3로 학습
  TRAIN_FILES 환경변수로 제어. DAG trigger 시 conf 로 주입.
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
    load_secrets,
)

# =============================================================================
# [시크릿] __main__ 진입 시 OpenBao 에서 채워짐
# =============================================================================
RUNWAY_API_KEY: str = ""
AWS_ACCESS_KEY_ID: str = ""
AWS_SECRET_ACCESS_KEY: str = ""


def _initialize_secrets() -> None:
    global RUNWAY_API_KEY, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
    data = load_secrets()
    required = ["aws_access_key_id", "aws_secret_access_key", "runway_api_key"]
    missing = [k for k in required if k not in data]
    if missing:
        raise RuntimeError(
            f"OpenBao secret/energy-demand 에 필수 키가 없음: {missing}. "
            f"시크릿 등록을 확인하세요."
        )
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

# 학습에 사용할 CSV 파일명 (쉼표 구분). DAG params → env 로 주입.
# 기존 학습: "Q1.csv"   재학습: "Q1.csv,Q2.csv,Q3.csv"
TRAIN_FILES = os.getenv("TRAIN_FILES", "Q1.csv")


# =============================================================================
# [설정] XGBoost 하이퍼파라미터
# =============================================================================
XGB_PARAMS = {
    "learning_rate": 0.1,
    "max_depth": 8,
    "reg_alpha": 10,
    "n_estimators": 620,
    "objective": "reg:squarederror",
}


# =============================================================================
# [S3 헬퍼]
# =============================================================================
def _s3():
    import boto3
    return boto3.client(
        "s3",
        endpoint_url=MLFLOW_S3_ENDPOINT_URL,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    )


def s3_upload_bytes(data: bytes, key: str) -> None:
    _s3().put_object(Bucket=S3_BUCKET, Key=key, Body=data)
    print(f"[S3] 업로드 완료: s3://{S3_BUCKET}/{key}")


def s3_upload_pickle(obj, key: str) -> None:
    s3_upload_bytes(pickle.dumps(obj), key)


def s3_download_bytes(key: str) -> bytes:
    resp = _s3().get_object(Bucket=S3_BUCKET, Key=key)
    data = resp["Body"].read()
    print(f"[S3] 다운로드 완료: s3://{S3_BUCKET}/{key}")
    return data


def s3_download_pickle(key: str):
    return pickle.loads(s3_download_bytes(key))


# =============================================================================
# [태스크] load_data
# =============================================================================
def load_data() -> None:
    """PVC 에서 CSV 를 읽어 피처/타겟 분리 후 S3 업로드.

    학습 데이터: TRAIN_DIR 에서 TRAIN_FILES 에 해당하는 CSV 로드.
    평가 데이터: EVAL_DIR 의 모든 CSV 를 Q별로 개별 로드 (Q1, Q2, Q3, Q4).
    """
    # ── 학습 데이터 ──
    file_list = [f.strip() for f in TRAIN_FILES.split(",") if f.strip()]
    train_paths = [os.path.join(TRAIN_DIR, f) for f in file_list]
    missing = [p for p in train_paths if not os.path.exists(p)]
    if missing:
        raise FileNotFoundError(f"학습 CSV 파일을 찾을 수 없음: {missing}. TRAIN_DIR={TRAIN_DIR}")

    print(f"[load_data] 학습 CSV: {train_paths}")
    train_df = pd.concat([pd.read_csv(p) for p in train_paths], ignore_index=True)

    feature_cols = resolve_col_specs(train_df, FEATURE_COL_SPECS)
    target_cols  = resolve_col_specs(train_df, TARGET_COL_SPECS)

    train_features = train_df[feature_cols].copy()
    train_targets  = train_df[target_cols].copy()
    print(f"[load_data] 학습 데이터: {train_features.shape[0]}행, 피처 {train_features.shape[1]}개, 타겟 {train_targets.shape[1]}개")

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
            print(f"[load_data] 평가 데이터 {q_name}: {len(ef)}행")
    else:
        print(f"[load_data] 평가 디렉토리 없음: {EVAL_DIR} — 평가 skip")

    s3_upload_pickle(train_features, TRAIN_FEATURES_KEY)
    s3_upload_pickle(train_targets, TRAIN_TARGETS_KEY)
    s3_upload_pickle(eval_data, EVAL_DATA_KEY)
    print("[load_data] 완료")


# =============================================================================
# [태스크] train_model
# =============================================================================
def train_model() -> None:
    """S3 에서 학습 데이터 다운로드 → MultiOutputRegressor 학습 → S3 업로드."""
    from sklearn.multioutput import MultiOutputRegressor
    from xgboost import XGBRegressor

    train_features = s3_download_pickle(TRAIN_FEATURES_KEY)
    train_targets  = s3_download_pickle(TRAIN_TARGETS_KEY)

    print(f"[train_model] 학습 시작: {train_features.shape[0]}행, {train_features.shape[1]} 피처 → {train_targets.shape[1]} 타겟")
    model = MultiOutputRegressor(XGBRegressor(**XGB_PARAMS))
    model.fit(train_features.values, train_targets.values)
    print("[train_model] 학습 완료")

    s3_upload_pickle(model, MODEL_TRAINED_KEY)
    print("[train_model] 모델 S3 업로드 완료")


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
        print("[evaluate_model] 평가 데이터 없음 — skip")
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

        print(f"[evaluate_model] {q_name} → RMSE: {q_metrics['rmse']:.4f}, MAE: {q_metrics['mae']:.4f}, MAPE: {q_metrics['mape']:.2f}%")

    # 전체 통합 메트릭
    combined_true = np.concatenate(all_y_true)
    combined_pred = np.concatenate(all_y_pred)
    overall = _calc_metrics(combined_true, combined_pred)

    metrics = {
        "overall": overall,
        "per_quarter": per_quarter,
        "total_eval_rows": sum(len(feat_df) for feat_df, _ in eval_data.values()),
    }

    print(f"[evaluate_model] Overall RMSE: {overall['rmse']:.4f}, MAE: {overall['mae']:.4f}, MAPE: {overall['mape']:.2f}%")
    s3_upload_bytes(json.dumps(metrics, ensure_ascii=False).encode(), METRICS_JSON_KEY)
    print("[evaluate_model] 완료")


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

    mlflow.set_experiment(EXPERIMENT_NAME)

    run_name = f"dag-{DAG_RUN_ID[:20]}"
    with mlflow.start_run(run_name=run_name):
        mlflow.log_params(XGB_PARAMS)
        mlflow.log_param("train_files", TRAIN_FILES)
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
            "artifact_path": "model",
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
        print(f"[log_to_mlflow] 모델 등록 완료: {MODEL_NAME}")

    print("[log_to_mlflow] MLflow 로깅 완료")


# =============================================================================
# [진입점]
# =============================================================================
STEP_MAP = {
    "load_data":      load_data,
    "train_model":    train_model,
    "evaluate_model": evaluate_model,
    "log_to_mlflow":  log_to_mlflow,
}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Energy demand prediction task runner")
    parser.add_argument("--step", required=True, choices=STEP_MAP.keys())
    args = parser.parse_args()

    _initialize_secrets()
    STEP_MAP[args.step]()
