"""환경값·시크릿 — /vault/secrets/creds.env 에서 자동 주입.

OpenBao Agent Injector 가 Pod 시작 시 /vault/secrets/creds.env 파일을 만들어두므로
entrypoint 에서 `source /vault/secrets/creds.env` 한 줄로 5개 env 변수가 셸에 채워진다.
이 모듈은 그 env 만 읽어 모든 파생값을 계산한다.
"""
import os

# /vault/secrets/creds.env 의 2개 base 값 (Step 1 에서 OpenBao 에 등록)
RUNWAY_PROJECT_ID  = os.environ["RUNWAY_PROJECT_ID"]
RUNWAY_BASE_DOMAIN = os.environ["RUNWAY_BASE_DOMAIN"]

# 파생값 — 모든 서비스 URL · 이름
MLFLOW_TRACKING_URI    = f"https://mlflow.{RUNWAY_BASE_DOMAIN}"
MLFLOW_S3_ENDPOINT_URL = f"https://s3.{RUNWAY_BASE_DOMAIN}"
NAMESPACE              = RUNWAY_PROJECT_ID
S3_BUCKET              = RUNWAY_PROJECT_ID
EXPERIMENT_NAME        = f"{RUNWAY_PROJECT_ID}.energy"
MODEL_NAME             = f"{RUNWAY_PROJECT_ID}.energy-xgboost"

# 모델·데이터 경로 상수
# - 데이터: PVC `dataset/` 서브폴더 (`/mnt/data/dataset/`) — Code Server 가 보는 경로
# - 모델: PVC 루트에 m-<id>/ — Runway 추론 Pod 가 PVC 루트를 `/mnt/models` 에 마운트
#   하므로, 사용자가 UI 에 입력하는 모델 경로는 `m-<id>` (PVC 루트 기준 sub path).
DATA_BASE           = "/mnt/data/dataset"
MODEL_REGISTRY_PATH = "/mnt/data"
S3_ARTIFACT_PREFIX  = "mlflow/experiments/energy/models/"

# 추론 엔드포인트 (Step 5 부터 사용) — 풀 URL 한 줄
# 예: https://inference.<domain>/api/<proj>/<ep>/<deploy>/v2/models/default/infer
INFERENCE_ENDPOINT   = os.getenv("INFERENCE_ENDPOINT", "")
INFERENCE_VERIFY_TLS = os.getenv("INFERENCE_VERIFY_TLS", "true").lower() == "true"


def load_secrets() -> dict:
    """Agent Injector 가 환경변수로 주입한 시크릿 3개를 dict 로 반환.

    task_runner.py / download_model.py / test_inference.py 가 이 함수를 그대로 호출한다.
    """
    return {
        "aws_access_key_id":     os.environ["AWS_ACCESS_KEY_ID"],
        "aws_secret_access_key": os.environ["AWS_SECRET_ACCESS_KEY"],
        "runway_api_key":        os.environ["RUNWAY_API_KEY"],
    }
