import os
from datetime import datetime, timedelta
from airflow.sdk import DAG, task

# =============================================================================
# [설정] 상수 & 환경 변수
# =============================================================================

# Runway 사용자 인증 토큰 (Keycloak offline token)
# - Runway UI > 사용자 설정 > API 토큰에서 발급
# - MLflow 실험 로깅 시 인증에 사용됨
RUNWAY_API_KEY = "eyJhbGciOiJIUzUxMiIsInR5cCIgOiAiSldUIiwia2lkIiA6ICJkZjVhOWNhNy00NmEzLTQ4YWUtODk2MS01NGEyYTdmMDgzMDAifQ.eyJpYXQiOjE3NzQxOTk2NzMsImp0aSI6ImMxMDdjYzY2LTdlNzctMGFhMC1iM2I0LTY2Y2ZhOGYzMGM1NCIsImlzcyI6Imh0dHBzOi8va2V5Y2xvYWsudjIubXJ4cnVud2F5LmFpL3JlYWxtcy9ydW53YXkiLCJhdWQiOiJodHRwczovL2tleWNsb2FrLnYyLm1yeHJ1bndheS5haS9yZWFsbXMvcnVud2F5IiwidHlwIjoiT2ZmbGluZSIsImF6cCI6Im1sZmxvdyIsInNpZCI6ImIzYWE4ZjM1LTAzYzUtNGVjOS1hNzI4LWUwNjI3ZjliZWM3OSIsInNjb3BlIjoib3BlbmlkIHdlYi1vcmlnaW5zIG9mZmxpbmVfYWNjZXNzIHNlcnZpY2VfYWNjb3VudCBlbWFpbCBwcm9maWxlIn0.mLzGj4-337W3ImqWZ6_DZVB80iwPXYGZTOU_-Dfsu3vWQr8gCZCH-svqEUa6uqtPqQtFmbKmc4e1FaCqpuihAQ"

# 학습 데이터셋 경로
# - DAG 파일과 같은 디렉토리에 위치한 turbine_data.csv를 자동으로 찾음
# - Gitea repo에 DAG 파일과 함께 push 해야 함
DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "turbine_data.csv")

# MLflow 서버 설정
# - MLFLOW_TRACKING_URI: 환경변수 우선, 없으면 외부 도메인 사용
MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "https://mlflow.v2.mrxrunway.ai")

# S3/MinIO 설정 (MLflow artifact store 접근용)
# - MLflow 서버의 아티팩트 저장소가 S3(MinIO) 기반이므로, 모델 업로드 시 필요
# - 파라미터/메트릭 로깅은 HTTP API라 불필요하지만, log_model()은 S3 직접 업로드
MLFLOW_S3_ENDPOINT_URL = "https://s3.v2.mrxrunway.ai"
AWS_ACCESS_KEY_ID = "0F9CD3FF-37B-47E064A6E18E37"
AWS_SECRET_ACCESS_KEY = "pPWjNwymzm4B52d3PrnHjR5NPaOnMYY_f2y1c22gNwU"

# MLflow 실험/모델 이름
# - EXPERIMENT_NAME: "{프로젝트ID}.{실험명}" 형식 (Runway naming rule)
# - MODEL_NAME: MLflow Model Registry에 등록할 모델 이름
EXPERIMENT_NAME = "tutorial-ml-workflow.wind-power-prediction"
MODEL_NAME = "tutorial-ml-workflow.wind-power-xgboost"

# XGBoost 하이퍼파라미터
XGB_PARAMS = {
    "learning_rate": 0.1,
    "max_depth": 8,
    "reg_alpha": 10,
    "n_estimators": 620,
    "objective": "reg:squarederror",
}

# PVC 마운트 경로
# - Runway IDE에서 미리 생성한 PVC를 마운트한 경로
# - 학습된 모델 아티팩트를 MLflow 형식으로 저장
MODEL_REGISTRY_PATH = "/mnt/model-registry"


# =============================================================================
# [DAG 기본 설정]
# =============================================================================
default_args = {
    "owner": "airflow",
    "depends_on_past": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}


# =============================================================================
# [DAG 정의 및 태스크 구성]
#
# 워크플로우:
#   load_data → load_model → train_model → evaluate_model → log_to_mlflow → save_model_to_pvc
#
# - 순차 실행: 동시 프로세스 fork로 인한 fd 부족 방지를 위해 직렬로 구성
# - 필수 패키지: pandas, xgboost, scikit-learn, mlflow (Airflow worker에 사전 설치 필요)
# =============================================================================
with DAG(
    dag_id="wind_power_prediction_2",
    default_args=default_args,
    description="Wind power prediction with XGBoost + MLflow tracking",
    schedule=None,   # 수동 trigger 전용
    catchup=False,
    tags=["ml", "xgboost", "wind-power"],
) as dag:

    # =========================================================================
    # Task 1: 데이터셋 로드
    # - DAG 파일과 같은 디렉토리의 turbine_data.csv를 읽어 /tmp에 복사
    # - 후속 태스크는 /tmp 경로를 XCom으로 전달받아 사용
    # =========================================================================
    @task
    def load_data():
        """Load turbine dataset from local file."""
        import pandas as pd

        print(f"[load_data] 데이터 로드 시작: {DATA_PATH}")
        df = pd.read_csv(DATA_PATH)
        print(f"[load_data] 데이터 shape: {df.shape[0]} rows, {df.shape[1]} columns")
        print(f"[load_data] 컬럼 목록: {list(df.columns)}")
        tmp_path = "/tmp/turbine_data.csv"
        df.to_csv(tmp_path, index=False)
        print(f"[load_data] 임시 파일 저장 완료: {tmp_path}")
        return tmp_path

    # =========================================================================
    # Task 2: 모델 초기화
    # - XGBRegressor를 하이퍼파라미터로 초기화하여 pickle 직렬화
    # =========================================================================
    @task
    def load_model():
        """Initialize XGBRegressor model with predefined hyperparameters."""
        import xgboost as xgb
        import pickle

        print(f"[load_model] XGBRegressor 초기화 시작")
        print(f"[load_model] 하이퍼파라미터: {XGB_PARAMS}")
        model = xgb.XGBRegressor(**XGB_PARAMS)
        model_path = "/tmp/model_init.pkl"
        with open(model_path, "wb") as f:
            pickle.dump(model, f)
        print(f"[load_model] 모델 저장 완료: {model_path}")
        return model_path

    # =========================================================================
    # Task 3: 모델 학습
    # - 전처리: 불필요 컬럼(id, datetime, uuid, index, wtg) 제거
    # - train/test split (8:2) 후 XGBoost 학습
    # - 학습된 모델과 테스트 데이터를 /tmp에 pickle로 저장
    # =========================================================================
    @task
    def train_model(data_path: str, model_path: str):
        """Train XGBoost model on turbine data."""
        import pandas as pd
        import pickle
        from sklearn.model_selection import train_test_split

        print(f"[train_model] 데이터 로드: {data_path}")
        df = pd.read_csv(data_path)
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

        print(f"[train_model] 모델 학습 시작...")
        with open(model_path, "rb") as f:
            model = pickle.load(f)
        model.fit(
            X_train, y_train, eval_set=[(X_test, y_test)], verbose=False
        )
        print(f"[train_model] 모델 학습 완료")

        trained_model_path = "/tmp/model_trained.pkl"
        with open(trained_model_path, "wb") as f:
            pickle.dump(model, f)

        test_data_path = "/tmp/test_data.pkl"
        with open(test_data_path, "wb") as f:
            pickle.dump({"X_test": X_test, "y_test": y_test}, f)

        print(f"[train_model] 아티팩트 저장 완료: {trained_model_path}, {test_data_path}")
        return {
            "trained_model_path": trained_model_path,
            "test_data_path": test_data_path,
            "dataset_rows": len(df),
        }

    # =========================================================================
    # Task 4: 모델 평가
    # - 테스트 데이터로 예측 수행
    # - RMSE, MAE, R2 메트릭 계산
    # =========================================================================
    @task
    def evaluate_model(train_result: dict):
        """Evaluate trained model and compute metrics."""
        import numpy as np
        import pickle
        from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score

        print(f"[evaluate_model] 모델 평가 시작")

        with open(train_result["trained_model_path"], "rb") as f:
            model = pickle.load(f)
        with open(train_result["test_data_path"], "rb") as f:
            test_data = pickle.load(f)

        X_test = test_data["X_test"]
        y_test = test_data["y_test"]
        print(f"[evaluate_model] 테스트 데이터: {len(X_test)}건")

        y_pred = model.predict(X_test)
        rmse = float(np.sqrt(mean_squared_error(y_test, y_pred)))
        mae = float(mean_absolute_error(y_test, y_pred))
        r2 = float(r2_score(y_test, y_pred))

        print(f"[evaluate_model] RMSE: {rmse:.4f}")
        print(f"[evaluate_model] MAE:  {mae:.4f}")
        print(f"[evaluate_model] R2:   {r2:.4f}")
        return {
            "trained_model_path": train_result["trained_model_path"],
            "dataset_rows": train_result["dataset_rows"],
            "metrics": {"rmse": rmse, "mae": mae, "r2": r2},
        }

    # =========================================================================
    # Task 5: MLflow 로깅
    # - Runway MLflow 서버에 실험 결과를 기록
    # - 인증: RUNWAY_API_KEY를 MLFLOW_TRACKING_TOKEN 환경변수로 설정
    # - URI: 환경변수 MLFLOW_TRACKING_URI 우선, 없으면 https://mlflow.v2.mrxrunway.ai 사용
    # - 로깅 항목: 하이퍼파라미터, 메트릭(RMSE/MAE/R2), 모델(XGBoost)
    # - 모델은 MLflow Model Registry에 등록됨
    # =========================================================================
    @task
    def log_to_mlflow(eval_result: dict):
        """Log experiment, metrics, and model to MLflow."""
        import mlflow
        import mlflow.xgboost
        import pickle

        print(f"[log_to_mlflow] MLflow 연결 설정 시작")
        print(f"[log_to_mlflow] Experiment: {EXPERIMENT_NAME}")
        print(f"[log_to_mlflow] MLflow URI: {MLFLOW_TRACKING_URI}")

        # MLflow 인증 토큰 설정
        os.environ["MLFLOW_TRACKING_TOKEN"] = RUNWAY_API_KEY
        os.environ["MLFLOW_TRACKING_URI"] = MLFLOW_TRACKING_URI

        # S3/MinIO 인증 설정 (모델 아티팩트 업로드용)
        os.environ["MLFLOW_S3_ENDPOINT_URL"] = MLFLOW_S3_ENDPOINT_URL
        os.environ["AWS_ACCESS_KEY_ID"] = AWS_ACCESS_KEY_ID
        os.environ["AWS_SECRET_ACCESS_KEY"] = AWS_SECRET_ACCESS_KEY

        mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
        mlflow.set_experiment(EXPERIMENT_NAME)
        print(f"[log_to_mlflow] MLflow 연결 완료")

        with open(eval_result["trained_model_path"], "rb") as f:
            model = pickle.load(f)

        run_name = f"xgboost-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        print(f"[log_to_mlflow] Run 시작: {run_name}")

        with mlflow.start_run(run_name=run_name):
            # 하이퍼파라미터 로깅
            mlflow.log_params(XGB_PARAMS)
            mlflow.log_param("dataset_rows", eval_result["dataset_rows"])
            mlflow.log_param("test_size", 0.2)
            print(f"[log_to_mlflow] 파라미터 로깅 완료")

            # 평가 메트릭 로깅
            mlflow.log_metrics(eval_result["metrics"])
            print(f"[log_to_mlflow] 메트릭 로깅 완료: {eval_result['metrics']}")

            # 모델 아티팩트 로깅 및 Model Registry 등록
            mlflow.xgboost.log_model(
                model,
                artifact_path="model",
                registered_model_name=MODEL_NAME,
            )
            print(f"[log_to_mlflow] 모델 등록 완료: {MODEL_NAME}")

        return eval_result["trained_model_path"]

    # =========================================================================
    # [태스크 의존성 정의]
    # - 순차 실행: load_data → load_model → train_model → evaluate_model → log_to_mlflow
    # - data >> model: 동시 fork에 의한 fd 부족 방지를 위해 직렬 의존성 설정
    # - train_model은 data(데이터 경로)와 model(모델 경로)을 XCom으로 전달받음
    # - 모델 아티팩트의 PVC 저장은 별도 스크립트(download_model.py)로 수행
    # =========================================================================
    data = load_data()
    model = load_model()
    data >> model
    train_result = train_model(data, model)
    eval_result = evaluate_model(train_result)
    log_to_mlflow(eval_result)
