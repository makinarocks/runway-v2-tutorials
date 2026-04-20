"""
task_runner.py — KubernetesPodOperator용 태스크 실행기

각 Airflow 태스크는 별도의 K8s Pod에서 이 스크립트를 실행한다.
--step 인자로 실행할 태스크를 지정한다.

태스크 간 아티팩트 공유:
  공유 PVC 대신 S3(MinIO)를 사용한다.
  각 DAG run은 고유한 S3 prefix (wind-power/dag-runs/<DAG_RUN_ID>/)에 아티팩트를 저장한다.
  DAG_RUN_ID는 KubernetesPodOperator의 env_vars를 통해 주입된다.

사용법:
    python task_runner.py --step load_data
    python task_runner.py --step load_model
    python task_runner.py --step train_model
    python task_runner.py --step evaluate_model
    python task_runner.py --step log_to_mlflow
"""

import argparse
import json
import os
import pickle
import tempfile

# =============================================================================
# [설정] 크레덴셜 & MLflow 설정
# - RUNWAY_API_KEY: Keycloak offline token → MLflow 인증용
# - OPENBAO_TOKEN: OpenBao 웹 콘솔에서 발급받은 서비스 토큰 → 시크릿 조회용
# - AWS 키: OpenBao에서 런타임에 조회 (load_secrets() 참고)
# =============================================================================
RUNWAY_API_KEY = os.getenv("RUNWAY_API_KEY", "")

MLFLOW_S3_ENDPOINT_URL = os.getenv("MLFLOW_S3_ENDPOINT_URL", "https://s3.v2.mrxrunway.ai")
MLFLOW_TRACKING_URI    = os.getenv("MLFLOW_TRACKING_URI", "https://mlflow.v2.mrxrunway.ai")

# =============================================================================
# [설정] OpenBao
# - 시크릿 등록: OpenBao 웹 콘솔에서 KV v2로 수동 등록
#     secret/data/<OPENBAO_SECRET_PATH> 에 aws_access_key_id, aws_secret_access_key 키 저장
# - 인증: OpenBao 콘솔에서 namespace 로그인 시 자동 발급되는 서비스 토큰 사용
#   (Keycloak offline token과 별개, X-Vault-Token 헤더로 직접 호출)
# - namespace(ns path)를 사용하는 multi-tenant 구성이면 OPENBAO_NAMESPACE 지정
# =============================================================================
OPENBAO_URL         = os.getenv("OPENBAO_URL", "https://openbao.v2.mrxrunway.ai")
OPENBAO_TOKEN       = os.getenv("OPENBAO_TOKEN", "")
OPENBAO_NAMESPACE   = os.getenv("OPENBAO_NAMESPACE", "")
OPENBAO_SECRET_PATH = os.getenv("OPENBAO_SECRET_PATH", "rwyt-energy-forecasting/wind-power")
OPENBAO_KV_MOUNT    = os.getenv("OPENBAO_KV_MOUNT", "secret")


def load_secrets() -> dict:
    """OpenBao 서비스 토큰으로 KV v2 시크릿을 조회한다."""
    import hvac
    kwargs = {"url": OPENBAO_URL, "token": OPENBAO_TOKEN}
    if OPENBAO_NAMESPACE:
        kwargs["namespace"] = OPENBAO_NAMESPACE
    client = hvac.Client(**kwargs)
    resp = client.secrets.kv.v2.read_secret_version(
        path=OPENBAO_SECRET_PATH,
        mount_point=OPENBAO_KV_MOUNT,
    )
    data = resp["data"]["data"]
    print(f"[openbao] 크레덴셜 로드 완료: path={OPENBAO_KV_MOUNT}/{OPENBAO_SECRET_PATH} keys={list(data.keys())}")
    return data


_secrets = load_secrets()
AWS_ACCESS_KEY_ID     = _secrets["aws_access_key_id"]
AWS_SECRET_ACCESS_KEY = _secrets["aws_secret_access_key"]

# =============================================================================
# [설정] S3 아티팩트 경로
# - DAG_RUN_ID: KubernetesPodOperator env_vars의 {{ run_id }} 템플릿으로 주입
# - 동일 DAG run의 모든 Pod가 같은 prefix 아래 아티팩트를 공유한다
# =============================================================================
S3_BUCKET  = os.getenv("S3_BUCKET", "rwyt-energy-forecasting")
DAG_RUN_ID = os.getenv("DAG_RUN_ID", "local")
S3_PREFIX  = f"wind-power/dag-runs/{DAG_RUN_ID}"

TURBINE_CSV_KEY   = f"{S3_PREFIX}/turbine_data.csv"   # load_data → train_model
MODEL_INIT_KEY    = f"{S3_PREFIX}/model_init.pkl"      # load_model → train_model
MODEL_TRAINED_KEY = f"{S3_PREFIX}/model_trained.pkl"   # train_model → evaluate_model, log_to_mlflow
TEST_DATA_KEY     = f"{S3_PREFIX}/test_data.pkl"       # train_model → evaluate_model
METRICS_JSON_KEY  = f"{S3_PREFIX}/metrics.json"        # evaluate_model → log_to_mlflow

# Docker 이미지 내 번들된 데이터셋 경로
DATA_IN_IMAGE = "/app/dataset/turbine_data.csv"

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

EXPERIMENT_NAME = "rwyt-energy-forecasting.wind-power-prediction"
MODEL_NAME      = "rwyt-energy-forecasting.wind-power-xgboost"


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


def s3_upload(local_path: str, key: str) -> None:
    _s3().upload_file(local_path, S3_BUCKET, key)
    print(f"[S3] 업로드 완료: s3://{S3_BUCKET}/{key}")


def s3_download(key: str, local_path: str) -> None:
    _s3().download_file(S3_BUCKET, key, local_path)
    print(f"[S3] 다운로드 완료: s3://{S3_BUCKET}/{key} → {local_path}")


# =============================================================================
# [Task 1] load_data
# - Docker 이미지 내 번들된 CSV를 S3에 업로드
# - 이후 train_model 태스크가 S3에서 읽는다
# =============================================================================
def load_data() -> None:
    """Load turbine dataset from Docker image and upload to S3."""
    print(f"[load_data] 데이터 소스: {DATA_IN_IMAGE}")

    import pandas as pd
    df = pd.read_csv(DATA_IN_IMAGE)
    print(f"[load_data] 데이터 shape: {df.shape[0]} rows × {df.shape[1]} columns")
    print(f"[load_data] 컬럼: {list(df.columns)}")

    s3_upload(DATA_IN_IMAGE, TURBINE_CSV_KEY)
    print(f"[load_data] S3 저장 완료: {TURBINE_CSV_KEY}")


# =============================================================================
# [Task 2] load_model
# - XGBRegressor를 하이퍼파라미터로 초기화하여 S3에 pickle 업로드
# =============================================================================
def load_model() -> None:
    """Initialize XGBRegressor model and upload pickle to S3."""
    import xgboost as xgb

    print(f"[load_model] XGBRegressor 초기화: {XGB_PARAMS}")
    model = xgb.XGBRegressor(**XGB_PARAMS)

    with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as tmp:
        pickle.dump(model, tmp)
        tmp_path = tmp.name

    s3_upload(tmp_path, MODEL_INIT_KEY)
    os.unlink(tmp_path)
    print(f"[load_model] S3 저장 완료: {MODEL_INIT_KEY}")


# =============================================================================
# [Task 3] train_model
# - S3에서 데이터와 초기 모델을 읽어 학습
# - 학습된 모델과 테스트 데이터를 S3에 업로드
# =============================================================================
def train_model() -> None:
    """Train XGBoost model using artifacts from S3."""
    import pandas as pd
    from sklearn.model_selection import train_test_split

    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path        = os.path.join(tmpdir, "turbine_data.csv")
        model_init_path = os.path.join(tmpdir, "model_init.pkl")
        model_out_path  = os.path.join(tmpdir, "model_trained.pkl")
        test_data_path  = os.path.join(tmpdir, "test_data.pkl")

        # S3에서 아티팩트 다운로드
        s3_download(TURBINE_CSV_KEY, csv_path)
        s3_download(MODEL_INIT_KEY, model_init_path)

        # 데이터 전처리
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

        # 모델 학습
        with open(model_init_path, "rb") as f:
            model = pickle.load(f)
        model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)
        print("[train_model] 학습 완료")

        # 결과 저장
        with open(model_out_path, "wb") as f:
            pickle.dump(model, f)
        with open(test_data_path, "wb") as f:
            pickle.dump({"X_test": X_test, "y_test": y_test, "dataset_rows": len(df)}, f)

        # S3 업로드
        s3_upload(model_out_path, MODEL_TRAINED_KEY)
        s3_upload(test_data_path, TEST_DATA_KEY)

    print("[train_model] S3 업로드 완료")


# =============================================================================
# [Task 4] evaluate_model
# - S3의 학습 모델과 테스트 데이터로 RMSE, MAE, R² 계산
# - 결과를 metrics.json으로 S3에 저장
# =============================================================================
def evaluate_model() -> None:
    """Evaluate trained model and upload metrics JSON to S3."""
    import numpy as np
    from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score

    with tempfile.TemporaryDirectory() as tmpdir:
        model_path     = os.path.join(tmpdir, "model_trained.pkl")
        test_data_path = os.path.join(tmpdir, "test_data.pkl")
        metrics_path   = os.path.join(tmpdir, "metrics.json")

        s3_download(MODEL_TRAINED_KEY, model_path)
        s3_download(TEST_DATA_KEY, test_data_path)

        with open(model_path, "rb") as f:
            model = pickle.load(f)
        with open(test_data_path, "rb") as f:
            test_data = pickle.load(f)

        X_test       = test_data["X_test"]
        y_test       = test_data["y_test"]
        dataset_rows = test_data["dataset_rows"]

        y_pred = model.predict(X_test)
        rmse   = float(np.sqrt(mean_squared_error(y_test, y_pred)))
        mae    = float(mean_absolute_error(y_test, y_pred))
        r2     = float(r2_score(y_test, y_pred))

        print(f"[evaluate_model] RMSE: {rmse:.4f}")
        print(f"[evaluate_model] MAE:  {mae:.4f}")
        print(f"[evaluate_model] R2:   {r2:.4f}")

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
# - S3의 metrics.json과 학습 모델을 읽어 MLflow에 로깅
# - 파라미터, 메트릭, 모델 아티팩트를 기록하고 Model Registry에 등록
# =============================================================================
def log_to_mlflow() -> None:
    """Log experiment results and model to MLflow using artifacts from S3."""
    from datetime import datetime
    import mlflow
    import mlflow.xgboost

    # MLflow 인증 설정
    os.environ["MLFLOW_TRACKING_TOKEN"]  = RUNWAY_API_KEY
    os.environ["MLFLOW_TRACKING_URI"]    = MLFLOW_TRACKING_URI
    os.environ["MLFLOW_S3_ENDPOINT_URL"] = MLFLOW_S3_ENDPOINT_URL
    os.environ["AWS_ACCESS_KEY_ID"]      = AWS_ACCESS_KEY_ID
    os.environ["AWS_SECRET_ACCESS_KEY"]  = AWS_SECRET_ACCESS_KEY

    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
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

        run_name = f"xgboost-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        print(f"[log_to_mlflow] Run 시작: {run_name}")

        with mlflow.start_run(run_name=run_name):
            mlflow.log_params(XGB_PARAMS)
            mlflow.log_param("dataset_rows", dataset_rows)
            mlflow.log_param("test_size", 0.2)
            mlflow.log_metrics(metrics)
            print(f"[log_to_mlflow] 메트릭: {metrics}")

            mlflow.xgboost.log_model(
                model,
                artifact_path="model",
                registered_model_name=MODEL_NAME,
            )
            print(f"[log_to_mlflow] 모델 등록 완료: {MODEL_NAME}")

    print("[log_to_mlflow] MLflow 로깅 완료")


# =============================================================================
# [진입점]
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
    print(f"=== [task_runner] step={args.step}  run_id={DAG_RUN_ID} ===")
    STEP_MAP[args.step]()
    print(f"=== [task_runner] {args.step} 완료 ===")
