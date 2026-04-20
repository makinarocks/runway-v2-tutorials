"""
task_runner.py — KubernetesPodOperator 용 태스크 실행기

이 파일이 무엇인가?
  Docker 이미지(wind-power-prediction:latest)에 번들되는 파이썬 스크립트.
  Airflow DAG(wind_power_prediction_v4.py)는 단계별로 별도 K8s Pod를 띄워 각
  Pod 안에서 `python task_runner.py --step <단계>` 를 실행한다.
  즉 **실제 ML 로직(데이터 로드, 학습, 평가, 로깅)이 여기에 들어있다**.

실행 흐름:
  1) Airflow 가 KubernetesPodOperator 로 Pod 생성 (env_vars 주입)
  2) Pod 안에서 이 스크립트가 `--step load_data` 같은 인자로 실행됨
  3) 모듈 import 시점에 OpenBao → AWS 키 조회 (아래 load_secrets 참고)
  4) STEP_MAP[args.step]() 으로 해당 단계 함수 호출
  5) 함수 내에서 S3 에 아티팩트를 주고받으며 진행
  6) 완료 시 Pod 자동 삭제 (is_delete_operator_pod=True)

태스크 간 아티팩트 공유:
  Pod 는 일회용이라 로컬 파일로 데이터를 이어받을 수 없음 → S3 MinIO 사용.
  각 DAG run 은 고유 prefix `wind-power/dag-runs/<DAG_RUN_ID>/` 를 가져
  동시 실행되어도 파일이 섞이지 않는다. DAG_RUN_ID 는 DAG 에서 env var 로 주입.

로컬 개발/디버깅:
    # 환경변수 설정 후 직접 호출 가능
    export RUNWAY_API_KEY="..." OPENBAO_TOKEN="..." OPENBAO_NAMESPACE="..."
    python task_runner.py --step load_data
"""

import argparse
import json
import os
import pickle
import tempfile

# =============================================================================
# [설정] 크레덴셜 & MLflow
#
# RUNWAY_API_KEY  : Keycloak offline token. MLflow 인증용 (MLFLOW_TRACKING_TOKEN).
# OPENBAO_TOKEN   : OpenBao 서비스 토큰. AWS 키 등 시크릿 조회용.
# AWS 키          : 여기서 직접 정의하지 않고 OpenBao 에서 런타임 조회 (load_secrets)
#
# 모든 값은 Pod 시작 시 KubernetesPodOperator env_vars 로 주입된다.
# 로컬 개발 시에는 기본값(빈 문자열) 대신 export 로 환경변수를 설정한 뒤 실행.
# =============================================================================
RUNWAY_API_KEY = os.getenv("RUNWAY_API_KEY", "")

MLFLOW_S3_ENDPOINT_URL = os.getenv("MLFLOW_S3_ENDPOINT_URL", "https://s3.v2.mrxrunway.ai")
MLFLOW_TRACKING_URI    = os.getenv("MLFLOW_TRACKING_URI", "https://mlflow.v2.mrxrunway.ai")

# =============================================================================
# [설정] OpenBao
#
# OpenBao(Vault 호환) 에 저장된 시크릿을 런타임에 조회한다. 저장 구조:
#   <OPENBAO_KV_MOUNT>/data/<OPENBAO_SECRET_PATH>
#     → { aws_access_key_id, aws_secret_access_key, ... }
#
# 인증 방식:
#   JWT auth 같은 flow 를 쓰지 않고, 콘솔 로그인 시 발급된 서비스 토큰을
#   X-Vault-Token 헤더로 직접 보낸다 (hvac.Client(token=...)). 만료 시 재발급 필요.
#
# multi-tenant namespace (Runway 프로젝트별 격리) 사용 시 OPENBAO_NAMESPACE 지정.
# 이 값은 X-Vault-Namespace 헤더로 전달된다.
# =============================================================================
OPENBAO_URL         = os.getenv("OPENBAO_URL", "https://openbao.v2.mrxrunway.ai")
OPENBAO_TOKEN       = os.getenv("OPENBAO_TOKEN", "")
OPENBAO_NAMESPACE   = os.getenv("OPENBAO_NAMESPACE", "")
OPENBAO_SECRET_PATH = os.getenv("OPENBAO_SECRET_PATH", "wind-power")
OPENBAO_KV_MOUNT    = os.getenv("OPENBAO_KV_MOUNT", "secret")

# AWS 키는 step 진입 시점에 초기화 (모듈 import 만으로 OpenBao 호출이 일어나지 않도록).
# Dockerfile 빌드 시 `python task_runner.py --help` 같이 가볍게 import 해도 실패하지 않고,
# 단위 테스트 / 로컬 syntax check 시에도 credential 없이 동작한다.
AWS_ACCESS_KEY_ID: str = ""
AWS_SECRET_ACCESS_KEY: str = ""


def load_secrets() -> dict:
    """OpenBao 서비스 토큰으로 KV v2 시크릿을 조회한다.

    반환 dict 예:
        { "aws_access_key_id": "...", "aws_secret_access_key": "...",
          "gitea_username": "...", "gitea_password": "..." }
    """
    import hvac
    kwargs = {"url": OPENBAO_URL, "token": OPENBAO_TOKEN}
    if OPENBAO_NAMESPACE:
        kwargs["namespace"] = OPENBAO_NAMESPACE
    client = hvac.Client(**kwargs)
    resp = client.secrets.kv.v2.read_secret_version(
        path=OPENBAO_SECRET_PATH,
        mount_point=OPENBAO_KV_MOUNT,
    )
    # KV v2 응답 구조에서 실제 데이터는 data.data 에 중첩되어 있음
    data = resp["data"]["data"]
    print(f"[openbao] 크레덴셜 로드 완료: path={OPENBAO_KV_MOUNT}/{OPENBAO_SECRET_PATH} keys={list(data.keys())}")
    return data


def _initialize_secrets() -> None:
    """__main__ 진입 시점에 한 번 호출되어 전역 AWS 키를 채운다."""
    global AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
    data = load_secrets()
    AWS_ACCESS_KEY_ID     = data["aws_access_key_id"]
    AWS_SECRET_ACCESS_KEY = data["aws_secret_access_key"]

# =============================================================================
# [설정] S3 아티팩트 경로
#
# S3_PREFIX  : DAG_RUN_ID 를 포함하여 동시 실행되는 여러 run 의 파일이 섞이지 않게 격리.
#              예) wind-power/dag-runs/manual__2026-04-21T12:00:00+00:00/
# XXX_KEY    : 각 태스크가 생성/소비하는 S3 객체의 키 (전체 경로 = bucket + key)
#
# 같은 DAG run 의 Pod 들은 이 경로를 공유하므로, 한 태스크가 업로드한 파일을
# 다음 태스크가 다운로드할 수 있다 (로컬 파일 / XCom 없이도 공유 가능).
# =============================================================================
S3_BUCKET  = os.getenv("S3_BUCKET", "rwyt-energy-forecasting")
DAG_RUN_ID = os.getenv("DAG_RUN_ID", "local")
S3_PREFIX  = f"wind-power/dag-runs/{DAG_RUN_ID}"

TURBINE_CSV_KEY   = f"{S3_PREFIX}/turbine_data.csv"   # load_data → train_model
MODEL_INIT_KEY    = f"{S3_PREFIX}/model_init.pkl"      # load_model → train_model
MODEL_TRAINED_KEY = f"{S3_PREFIX}/model_trained.pkl"   # train_model → evaluate_model, log_to_mlflow
TEST_DATA_KEY     = f"{S3_PREFIX}/test_data.pkl"       # train_model → evaluate_model
METRICS_JSON_KEY  = f"{S3_PREFIX}/metrics.json"        # evaluate_model → log_to_mlflow

# Docker 이미지 내 번들된 데이터셋 경로 (Dockerfile: COPY dataset/turbine_data.csv ...)
# load_data 태스크가 이 로컬 파일을 S3 로 업로드하여 파이프라인 시작점이 된다
DATA_IN_IMAGE = "/app/dataset/turbine_data.csv"

# =============================================================================
# [설정] XGBoost 하이퍼파라미터 & MLflow 명명규칙
#
# XGB_PARAMS    : 학습에 사용되는 모델 파라미터. 튜토리얼이라 고정값. 실험 시에는
#                 여기를 바꾸거나 DAG param 으로 외부화해서 sweep 가능.
# EXPERIMENT_NAME / MODEL_NAME :
#   Runway 규약에 따라 "{프로젝트ID}.{실험명}" 형태로 짓는다.
#   프로젝트 ID 가 다르면 MLflow 에서 permission denied 가 나므로 이식 시 반드시 수정.
# =============================================================================
XGB_PARAMS = {
    "learning_rate": 0.1,
    "max_depth": 8,
    "reg_alpha": 10,
    "n_estimators": 620,
    "objective": "reg:squarederror",
}

EXPERIMENT_NAME = "rwyt-energy-forecasting.wind-power-prediction"
MODEL_NAME      = "rwyt-energy-forecasting.wind-power-xgboost"


# =============================================================================
# [S3 헬퍼]
# MinIO(S3 호환) 에 업로드/다운로드하기 위한 공통 boto3 클라이언트.
# endpoint_url 을 명시하는 이유: boto3 기본값은 AWS S3 이므로 내부 MinIO 엔드포인트를
# 가리키도록 해야 함. credential 은 OpenBao 에서 받아온 값 사용.
# =============================================================================
def _s3():
    import boto3
    return boto3.client(
        "s3",
        endpoint_url=MLFLOW_S3_ENDPOINT_URL,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    )


def s3_upload(local_path: str, key: str) -> None:
    _s3().upload_file(local_path, S3_BUCKET, key)
    print(f"[S3] 업로드 완료: s3://{S3_BUCKET}/{key}")


def s3_download(key: str, local_path: str) -> None:
    _s3().download_file(S3_BUCKET, key, local_path)
    print(f"[S3] 다운로드 완료: s3://{S3_BUCKET}/{key} → {local_path}")


# =============================================================================
# [Task 1] load_data
#   입력 : Docker 이미지 내 번들된 CSV (/app/dataset/turbine_data.csv)
#   출력 : s3://{S3_BUCKET}/{TURBINE_CSV_KEY}
#   역할 : 파이프라인의 시작점. 원본 데이터를 S3 에 올려 다음 태스크들이 접근 가능하게 함
# =============================================================================
def load_data() -> None:
    """Load turbine dataset from Docker image and upload to S3."""
    print(f"[load_data] 데이터 소스: {DATA_IN_IMAGE}")

    # pandas import 는 함수 안에서: import 비용을 실제 실행되는 단계에서만 치르기 위함
    # (모듈 로드 시 전체 import 하면 어떤 step 이든 pandas/xgboost/mlflow 다 로드됨)
    import pandas as pd
    df = pd.read_csv(DATA_IN_IMAGE)
    print(f"[load_data] 데이터 shape: {df.shape[0]} rows × {df.shape[1]} columns")
    print(f"[load_data] 컬럼: {list(df.columns)}")

    s3_upload(DATA_IN_IMAGE, TURBINE_CSV_KEY)
    print(f"[load_data] S3 저장 완료: {TURBINE_CSV_KEY}")


# =============================================================================
# [Task 2] load_model
#   입력 : XGB_PARAMS (하드코딩된 하이퍼파라미터)
#   출력 : s3://{S3_BUCKET}/{MODEL_INIT_KEY} (untrained XGBRegressor pickle)
#   역할 : 학습 전 모델 객체를 미리 만들어 S3 에 올린다. train_model 이 이걸 받아 fit.
#   왜 분리? 하이퍼파라미터 변경/재학습 시 load_model 만 다시 돌려서 새 초기화 가능.
# =============================================================================
def load_model() -> None:
    """Initialize XGBRegressor model and upload pickle to S3."""
    import xgboost as xgb

    print(f"[load_model] XGBRegressor 초기화: {XGB_PARAMS}")
    model = xgb.XGBRegressor(**XGB_PARAMS)

    # NamedTemporaryFile: 임시 파일로 pickle 덤프 후 S3 업로드. delete=False 이후
    # 명시적 os.unlink 로 정리 (Pod 는 어차피 종료되지만 습관적 정리)
    with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as tmp:
        pickle.dump(model, tmp)
        tmp_path = tmp.name

    s3_upload(tmp_path, MODEL_INIT_KEY)
    os.unlink(tmp_path)
    print(f"[load_model] S3 저장 완료: {MODEL_INIT_KEY}")


# =============================================================================
# [Task 3] train_model
#   입력 : TURBINE_CSV_KEY, MODEL_INIT_KEY (S3 에서 다운로드)
#   출력 : MODEL_TRAINED_KEY (학습된 모델 pickle),
#          TEST_DATA_KEY (X_test/y_test — evaluate_model 에서 재사용 위함)
#   역할 : 데이터 전처리 → train/test split → XGBoost fit → 결과물 S3 업로드
#
# 주요 판단:
#   - drop_cols : id/datetime 등 예측과 무관한 식별자 컬럼 제거 (errors="ignore" 로
#                 컬럼이 없어도 에러 내지 않음)
#   - test_size=0.2, random_state=42 : 튜토리얼이라 재현 가능한 고정 split
#   - test_data 를 별도 pickle 로 저장하는 이유: evaluate 단계에서 동일한 split 으로
#     재현 가능한 평가 (다시 split 하면 랜덤성으로 다른 결과 나올 수 있음)
# =============================================================================
def train_model() -> None:
    """Train XGBoost model using artifacts from S3."""
    import pandas as pd
    from sklearn.model_selection import train_test_split

    # TemporaryDirectory: 블록 종료 시 디렉토리 전체 자동 삭제
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path        = os.path.join(tmpdir, "turbine_data.csv")
        model_init_path = os.path.join(tmpdir, "model_init.pkl")
        model_out_path  = os.path.join(tmpdir, "model_trained.pkl")
        test_data_path  = os.path.join(tmpdir, "test_data.pkl")

        # 1. S3 → 로컬 다운로드 (load_data, load_model 이 미리 업로드한 것)
        s3_download(TURBINE_CSV_KEY, csv_path)
        s3_download(MODEL_INIT_KEY, model_init_path)

        # 2. 데이터 전처리: 식별자 컬럼 제거 후 타겟 분리
        df = pd.read_csv(csv_path)
        drop_cols = ["id", "datetime", "uuid", "index", "wtg"]
        df = df.drop(columns=drop_cols, errors="ignore")
        print(f"[train_model] 전처리 후 shape: {df.shape}")

        target   = "activepower"
        features = [c for c in df.columns if c != target]
        X = df[features]
        y = df[target]
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42
        )
        print(f"[train_model] 학습: {len(X_train)}건, 테스트: {len(X_test)}건")

        # 3. 학습: load_model 이 만든 초기 모델을 받아 fit
        #    eval_set 은 학습 중 조기 종료/과적합 판단에 활용 가능 (verbose=False 로 출력 억제)
        with open(model_init_path, "rb") as f:
            model = pickle.load(f)
        model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)
        print("[train_model] 학습 완료")

        # 4. 결과 저장: 학습된 모델 + 테스트 세트(재현 가능한 평가용)
        with open(model_out_path, "wb") as f:
            pickle.dump(model, f)
        with open(test_data_path, "wb") as f:
            pickle.dump({"X_test": X_test, "y_test": y_test, "dataset_rows": len(df)}, f)

        # 5. S3 업로드 → evaluate_model, log_to_mlflow 가 이걸 받아간다
        s3_upload(model_out_path, MODEL_TRAINED_KEY)
        s3_upload(test_data_path, TEST_DATA_KEY)

    print("[train_model] S3 업로드 완료")


# =============================================================================
# [Task 4] evaluate_model
#   입력 : MODEL_TRAINED_KEY (학습된 모델), TEST_DATA_KEY (학습 시 분리해둔 테스트셋)
#   출력 : METRICS_JSON_KEY (RMSE, MAE, R² 수치)
#   역할 : 모델 성능을 정량화. 이 수치는 log_to_mlflow 단계에서 MLflow 메트릭으로 기록됨
#
# 지표 해석:
#   - RMSE : Root Mean Squared Error. 큰 오차에 더 민감. 낮을수록 좋음
#   - MAE  : Mean Absolute Error. 평균 오차. 낮을수록 좋음
#   - R²   : 결정계수. 1.0 에 가까울수록 설명력 높음
# =============================================================================
def evaluate_model() -> None:
    """Evaluate trained model and upload metrics JSON to S3."""
    import numpy as np
    from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score

    with tempfile.TemporaryDirectory() as tmpdir:
        model_path     = os.path.join(tmpdir, "model_trained.pkl")
        test_data_path = os.path.join(tmpdir, "test_data.pkl")
        metrics_path   = os.path.join(tmpdir, "metrics.json")

        # train_model 이 업로드한 결과물 수령
        s3_download(MODEL_TRAINED_KEY, model_path)
        s3_download(TEST_DATA_KEY, test_data_path)

        with open(model_path, "rb") as f:
            model = pickle.load(f)
        with open(test_data_path, "rb") as f:
            test_data = pickle.load(f)

        X_test       = test_data["X_test"]
        y_test       = test_data["y_test"]
        dataset_rows = test_data["dataset_rows"]

        # 예측 → 지표 계산
        y_pred = model.predict(X_test)
        rmse   = float(np.sqrt(mean_squared_error(y_test, y_pred)))
        mae    = float(mean_absolute_error(y_test, y_pred))
        r2     = float(r2_score(y_test, y_pred))

        print(f"[evaluate_model] RMSE: {rmse:.4f}")
        print(f"[evaluate_model] MAE:  {mae:.4f}")
        print(f"[evaluate_model] R2:   {r2:.4f}")

        # JSON 으로 직렬화해 S3 에 저장 (log_to_mlflow 가 읽어감)
        result = {
            "metrics": {"rmse": rmse, "mae": mae, "r2": r2},
            "dataset_rows": dataset_rows,
        }
        with open(metrics_path, "w") as f:
            json.dump(result, f, indent=2)

        s3_upload(metrics_path, METRICS_JSON_KEY)

    print("[evaluate_model] S3 업로드 완료")


# =============================================================================
# [Task 5] log_to_mlflow
#   입력 : MODEL_TRAINED_KEY (모델), METRICS_JSON_KEY (메트릭)
#   출력 : MLflow 서버에 experiment run 기록 + Model Registry 에 모델 등록
#   역할 : 실험 추적 / 모델 버전 관리를 위해 모든 메타데이터를 중앙 저장소에 기록
#
# MLflow 인증 흐름:
#   MLFLOW_TRACKING_TOKEN 에 Keycloak offline token(RUNWAY_API_KEY) 을 그대로 세팅.
#   MLflow 서버가 offline token 을 직접 검증한다. 별도 토큰 교환 불필요.
#
# S3 엔드포인트 환경변수:
#   mlflow.xgboost.log_model 이 모델 아티팩트를 S3 에 업로드하는데,
#   boto3 기본값이 AWS S3 이라 내부 MinIO 를 쓰려면 MLFLOW_S3_ENDPOINT_URL +
#   AWS_ACCESS_KEY_ID/SECRET 을 **환경변수** 로 세팅해줘야 한다 (boto3 가 자동 픽업).
#
# experiment/model 명명 규칙: 반드시 "{프로젝트ID}.{실험명}" 형태여야 함.
# 다른 프로젝트 ID 로는 MLflow 에서 permission denied 반환.
# =============================================================================
def log_to_mlflow() -> None:
    """Log experiment results and model to MLflow using artifacts from S3."""
    from datetime import datetime
    import mlflow
    import mlflow.xgboost

    # MLflow 인증 + S3 credential 설정 (mlflow 내부 boto3 가 이 env 를 읽음)
    os.environ["MLFLOW_TRACKING_TOKEN"]  = RUNWAY_API_KEY
    os.environ["MLFLOW_TRACKING_URI"]    = MLFLOW_TRACKING_URI
    os.environ["MLFLOW_S3_ENDPOINT_URL"] = MLFLOW_S3_ENDPOINT_URL
    os.environ["AWS_ACCESS_KEY_ID"]      = AWS_ACCESS_KEY_ID
    os.environ["AWS_SECRET_ACCESS_KEY"]  = AWS_SECRET_ACCESS_KEY

    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    # set_experiment: 없으면 생성, 있으면 해당 experiment 를 현재 run 컨텍스트로 지정
    mlflow.set_experiment(EXPERIMENT_NAME)
    print(f"[log_to_mlflow] MLflow 연결 완료: {MLFLOW_TRACKING_URI}")

    with tempfile.TemporaryDirectory() as tmpdir:
        model_path   = os.path.join(tmpdir, "model_trained.pkl")
        metrics_path = os.path.join(tmpdir, "metrics.json")

        s3_download(MODEL_TRAINED_KEY, model_path)
        s3_download(METRICS_JSON_KEY, metrics_path)

        with open(model_path, "rb") as f:
            model = pickle.load(f)
        with open(metrics_path, "r") as f:
            eval_result = json.load(f)

        metrics      = eval_result["metrics"]
        dataset_rows = eval_result["dataset_rows"]

        # 각 run 에 고유 이름 부여 (타임스탬프 기반). UI 에서 구분 쉬움
        run_name = f"xgboost-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        print(f"[log_to_mlflow] Run 시작: {run_name}")

        # with mlflow.start_run() 블록 안의 log_* 호출들은 모두 이 run 에 귀속됨
        with mlflow.start_run(run_name=run_name):
            mlflow.log_params(XGB_PARAMS)               # 하이퍼파라미터 (dict)
            mlflow.log_param("dataset_rows", dataset_rows)  # 개별 파라미터
            mlflow.log_param("test_size", 0.2)
            mlflow.log_metrics(metrics)                 # RMSE/MAE/R² (dict)
            print(f"[log_to_mlflow] 메트릭: {metrics}")

            # 모델 아티팩트 업로드 + Model Registry 에 버전으로 등록
            # registered_model_name 이 있으면 자동으로 Registry 에 신규 버전 등록
            mlflow.xgboost.log_model(
                model,
                artifact_path="model",
                registered_model_name=MODEL_NAME,
            )
            print(f"[log_to_mlflow] 모델 등록 완료: {MODEL_NAME}")

    print("[log_to_mlflow] MLflow 로깅 완료")


# =============================================================================
# [진입점]
# Pod 안에서 `python task_runner.py --step <name>` 으로 실행되면 STEP_MAP 에서
# 해당 함수를 찾아 호출한다. --step 값이 잘못되면 argparse 의 choices 가 거부.
# =============================================================================
STEP_MAP = {
    "load_data":      load_data,
    "load_model":     load_model,
    "train_model":    train_model,
    "evaluate_model": evaluate_model,
    "log_to_mlflow":  log_to_mlflow,
}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="KubernetesPodOperator용 태스크 실행기")
    parser.add_argument(
        "--step",
        required=True,
        choices=list(STEP_MAP.keys()),
        help="실행할 태스크 이름",
    )
    args = parser.parse_args()
    # OpenBao → AWS 키 로드는 이 시점에 수행한다 (--help 등 parse_args 이전엔 미호출)
    _initialize_secrets()
    print(f"=== [task_runner] step={args.step}  run_id={DAG_RUN_ID} ===")
    STEP_MAP[args.step]()
    print(f"=== [task_runner] {args.step} 완료 ===")
