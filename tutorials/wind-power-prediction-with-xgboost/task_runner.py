"""
task_runner.py — KubernetesPodOperator용 태스크 실행기

각 Airflow 태스크는 별도의 K8s Pod에서 이 스크립트를 실행한다.
--step 인자로 실행할 태스크를 지정한다.

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
import shutil

# =============================================================================
# [설정] 공유 PVC 경로 상수
# - 모든 Pod에 /mnt/shared-workspace 로 동일 PVC가 마운트됨
# - 태스크 간 아티팩트를 고정 경로로 주고받는다 (XCom 불필요)
# =============================================================================
SHARED = "/mnt/shared-workspace"

TURBINE_CSV   = f"{SHARED}/turbine_data.csv"    # load_data → train_model
MODEL_INIT    = f"{SHARED}/model_init.pkl"       # load_model → train_model
MODEL_TRAINED = f"{SHARED}/model_trained.pkl"   # train_model → evaluate_model, log_to_mlflow
TEST_DATA     = f"{SHARED}/test_data.pkl"       # train_model → evaluate_model
METRICS_JSON  = f"{SHARED}/metrics.json"        # evaluate_model → log_to_mlflow

# Docker 이미지 내 번들된 데이터셋 경로
DATA_IN_IMAGE = "/app/dataset/turbine_data.csv"

# =============================================================================
# [설정] 크레덴셜 & MLflow 설정
# - KubernetesPodOperator의 env_vars 파라미터로 Pod 시작 시 주입됨
# - 프로덕션 환경에서는 Kubernetes Secret 사용을 권장한다:
#   env_from=[k8s.V1EnvFromSource(secret_ref=k8s.V1SecretEnvSource(name="wind-power-secret"))]
# =============================================================================
RUNWAY_API_KEY       = os.getenv("RUNWAY_API_KEY", "")
AWS_ACCESS_KEY_ID    = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")

MLFLOW_S3_ENDPOINT_URL = os.getenv("MLFLOW_S3_ENDPOINT_URL", "https://s3.v2.mrxrunway.ai")
MLFLOW_TRACKING_URI    = os.getenv("MLFLOW_TRACKING_URI", "https://mlflow.v2.mrxrunway.ai")

EXPERIMENT_NAME = "tutorial-ml-workflow.wind-power-prediction"
MODEL_NAME      = "tutorial-ml-workflow.wind-power-xgboost"

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
# [Task 1] load_data
# - Docker 이미지 내 번들된 CSV를 공유 PVC로 복사
# - 이후 train_model 태스크가 공유 PVC 경로에서 데이터를 읽는다
# =============================================================================
def load_data() -> None:
    """Load turbine dataset from Docker image and copy to shared PVC."""
    import pandas as pd

    print(f"[load_data] 데이터 소스: {DATA_IN_IMAGE}")
    df = pd.read_csv(DATA_IN_IMAGE)
    print(f"[load_data] 데이터 shape: {df.shape[0]} rows, {df.shape[1]} columns")
    print(f"[load_data] 컬럼 목록: {list(df.columns)}")

    os.makedirs(SHARED, exist_ok=True)
    shutil.copy(DATA_IN_IMAGE, TURBINE_CSV)
    print(f"[load_data] 공유 PVC 저장 완료: {TURBINE_CSV}")


# =============================================================================
# [Task 2] load_model
# - XGBRegressor를 하이퍼파라미터로 초기화하여 공유 PVC에 pickle 저장
# =============================================================================
def load_model() -> None:
    """Initialize XGBRegressor model and save to shared PVC."""
    import xgboost as xgb

    print(f"[load_model] XGBRegressor 초기화 시작")
    print(f"[load_model] 하이퍼파라미터: {XGB_PARAMS}")

    model = xgb.XGBRegressor(**XGB_PARAMS)

    os.makedirs(SHARED, exist_ok=True)
    with open(MODEL_INIT, "wb") as f:
        pickle.dump(model, f)
    print(f"[load_model] 공유 PVC 저장 완료: {MODEL_INIT}")


# =============================================================================
# [Task 3] train_model
# - 공유 PVC에서 데이터와 초기 모델을 읽어 학습
# - 학습된 모델과 테스트 데이터를 공유 PVC에 저장
# =============================================================================
def train_model() -> None:
    """Train XGBoost model on turbine data using artifacts from shared PVC."""
    import pandas as pd
    from sklearn.model_selection import train_test_split

    print(f"[train_model] 데이터 로드: {TURBINE_CSV}")
    df = pd.read_csv(TURBINE_CSV)

    drop_cols = ["id", "datetime", "uuid", "index", "wtg"]
    df = df.drop(columns=drop_cols, errors="ignore")
    print(f"[train_model] 전처리 후 shape: {df.shape}")

    target = "activepower"
    features = [c for c in df.columns if c != target]
    print(f"[train_model] feature 수: {len(features)}, target: {target}")

    X = df[features]
    y = df[target]
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )
    print(f"[train_model] 학습 데이터: {len(X_train)}건, 테스트 데이터: {len(X_test)}건")

    print(f"[train_model] 모델 로드: {MODEL_INIT}")
    with open(MODEL_INIT, "rb") as f:
        model = pickle.load(f)

    print(f"[train_model] 모델 학습 시작...")
    model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)
    print(f"[train_model] 모델 학습 완료")

    with open(MODEL_TRAINED, "wb") as f:
        pickle.dump(model, f)
    print(f"[train_model] 학습 모델 저장: {MODEL_TRAINED}")

    with open(TEST_DATA, "wb") as f:
        pickle.dump({"X_test": X_test, "y_test": y_test, "dataset_rows": len(df)}, f)
    print(f"[train_model] 테스트 데이터 저장: {TEST_DATA}")


# =============================================================================
# [Task 4] evaluate_model
# - 공유 PVC의 학습 모델과 테스트 데이터로 RMSE, MAE, R² 계산
# - 결과를 metrics.json으로 공유 PVC에 저장 (log_to_mlflow 태스크가 읽음)
# =============================================================================
def evaluate_model() -> None:
    """Evaluate trained model and save metrics to shared PVC."""
    import numpy as np
    from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score

    print(f"[evaluate_model] 모델 평가 시작")

    with open(MODEL_TRAINED, "rb") as f:
        model = pickle.load(f)
    with open(TEST_DATA, "rb") as f:
        test_data = pickle.load(f)

    X_test = test_data["X_test"]
    y_test = test_data["y_test"]
    dataset_rows = test_data["dataset_rows"]
    print(f"[evaluate_model] 테스트 데이터: {len(X_test)}건")

    y_pred = model.predict(X_test)
    rmse = float(np.sqrt(mean_squared_error(y_test, y_pred)))
    mae  = float(mean_absolute_error(y_test, y_pred))
    r2   = float(r2_score(y_test, y_pred))

    print(f"[evaluate_model] RMSE: {rmse:.4f}")
    print(f"[evaluate_model] MAE:  {mae:.4f}")
    print(f"[evaluate_model] R2:   {r2:.4f}")

    result = {
        "metrics": {"rmse": rmse, "mae": mae, "r2": r2},
        "dataset_rows": dataset_rows,
    }
    with open(METRICS_JSON, "w") as f:
        json.dump(result, f, indent=2)
    print(f"[evaluate_model] 평가 결과 저장: {METRICS_JSON}")


# =============================================================================
# [Task 5] log_to_mlflow
# - 공유 PVC의 metrics.json과 학습 모델을 읽어 MLflow에 로깅
# - 파라미터, 메트릭, 모델 아티팩트를 기록하고 Model Registry에 등록
# =============================================================================
def log_to_mlflow() -> None:
    """Log experiment results and model to MLflow using artifacts from shared PVC."""
    from datetime import datetime
    import mlflow
    import mlflow.xgboost

    print(f"[log_to_mlflow] MLflow 연결 설정 시작")
    print(f"[log_to_mlflow] Experiment: {EXPERIMENT_NAME}")
    print(f"[log_to_mlflow] MLflow URI: {MLFLOW_TRACKING_URI}")

    # MLflow 인증 및 S3 설정
    # env_vars로 이미 주입된 값을 mlflow가 자동으로 읽는다
    os.environ["MLFLOW_TRACKING_TOKEN"]  = RUNWAY_API_KEY
    os.environ["MLFLOW_TRACKING_URI"]    = MLFLOW_TRACKING_URI
    os.environ["MLFLOW_S3_ENDPOINT_URL"] = MLFLOW_S3_ENDPOINT_URL
    os.environ["AWS_ACCESS_KEY_ID"]      = AWS_ACCESS_KEY_ID
    os.environ["AWS_SECRET_ACCESS_KEY"]  = AWS_SECRET_ACCESS_KEY

    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_experiment(EXPERIMENT_NAME)
    print(f"[log_to_mlflow] MLflow 연결 완료")

    # 평가 결과 로드
    with open(METRICS_JSON, "r") as f:
        eval_result = json.load(f)
    metrics      = eval_result["metrics"]
    dataset_rows = eval_result["dataset_rows"]

    # 학습 모델 로드
    with open(MODEL_TRAINED, "rb") as f:
        model = pickle.load(f)

    run_name = f"xgboost-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    print(f"[log_to_mlflow] Run 시작: {run_name}")

    with mlflow.start_run(run_name=run_name):
        # 하이퍼파라미터 로깅
        mlflow.log_params(XGB_PARAMS)
        mlflow.log_param("dataset_rows", dataset_rows)
        mlflow.log_param("test_size", 0.2)
        print(f"[log_to_mlflow] 파라미터 로깅 완료")

        # 평가 메트릭 로깅
        mlflow.log_metrics(metrics)
        print(f"[log_to_mlflow] 메트릭 로깅 완료: {metrics}")

        # 모델 아티팩트 로깅 및 Model Registry 등록
        mlflow.xgboost.log_model(
            model,
            artifact_path="model",
            registered_model_name=MODEL_NAME,
        )
        print(f"[log_to_mlflow] 모델 등록 완료: {MODEL_NAME}")


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
    parser = argparse.ArgumentParser(
        description="KubernetesPodOperator용 태스크 실행기"
    )
    parser.add_argument(
        "--step",
        required=True,
        choices=list(STEP_MAP.keys()),
        help="실행할 태스크 이름",
    )
    args = parser.parse_args()

    print(f"=== [task_runner] step: {args.step} ===")
    STEP_MAP[args.step]()
    print(f"=== [task_runner] {args.step} 완료 ===")
