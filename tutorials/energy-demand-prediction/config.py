"""
config.py — 에너지 수요 예측 튜토리얼 전역 설정 모듈

wind-power-prediction 의 config.py 와 동일한 구조.
RUNWAY_PROJECT_ID + RUNWAY_BASE_DOMAIN 에서 모든 서비스 URL 자동 파생.

DAG(energy_demand_prediction.py)는 이 파일을 import 하지 않는다.
DAG 는 airflow-dags 로 sync 되어 스케줄러에서 파싱되므로 config.py 가 없음.
"""

import os

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass


# =============================================================================
# [필수] 사용자마다 다른 값
# =============================================================================
RUNWAY_PROJECT_ID  = os.getenv("RUNWAY_PROJECT_ID", "")
RUNWAY_BASE_DOMAIN = os.getenv("RUNWAY_BASE_DOMAIN", "")


# =============================================================================
# [Runway 인프라] 베이스 도메인에서 파생
# =============================================================================
MLFLOW_TRACKING_URI    = os.getenv("MLFLOW_TRACKING_URI",    f"https://mlflow.{RUNWAY_BASE_DOMAIN}")
MLFLOW_S3_ENDPOINT_URL = os.getenv("MLFLOW_S3_ENDPOINT_URL", f"https://s3.{RUNWAY_BASE_DOMAIN}")
OPENBAO_URL            = os.getenv("OPENBAO_URL",            f"https://openbao.{RUNWAY_BASE_DOMAIN}")
GITEA_REGISTRY_HOST    = os.getenv("GITEA_REGISTRY_HOST",    f"gitea.{RUNWAY_BASE_DOMAIN}")


# =============================================================================
# [파생값] RUNWAY_PROJECT_ID 에서 자동 계산
# =============================================================================
NAMESPACE           = os.getenv("NAMESPACE",         RUNWAY_PROJECT_ID)
S3_BUCKET           = os.getenv("S3_BUCKET",         RUNWAY_PROJECT_ID)
OPENBAO_NAMESPACE   = os.getenv("OPENBAO_NAMESPACE", RUNWAY_PROJECT_ID)

IMAGE_NAME = os.getenv("IMAGE_NAME", "energy-demand-prediction")
IMAGE_TAG  = os.getenv("IMAGE_TAG",  "latest")
IMAGE      = os.getenv(
    "IMAGE",
    f"{GITEA_REGISTRY_HOST}/{RUNWAY_PROJECT_ID}/{IMAGE_NAME}:{IMAGE_TAG}",
)

EXPERIMENT_SHORT_NAME = os.getenv("EXPERIMENT_SHORT_NAME", "energy-demand-prediction")
EXPERIMENT_NAME       = os.getenv(
    "EXPERIMENT_NAME",
    f"{RUNWAY_PROJECT_ID}.{EXPERIMENT_SHORT_NAME}",
)
MODEL_SHORT_NAME      = os.getenv("MODEL_SHORT_NAME", "energy-demand-xgboost")
MODEL_NAME            = os.getenv(
    "MODEL_NAME",
    f"{RUNWAY_PROJECT_ID}.{MODEL_SHORT_NAME}",
)


# =============================================================================
# [OpenBao]
# =============================================================================
OPENBAO_TOKEN       = os.getenv("OPENBAO_TOKEN", "")
OPENBAO_SECRET_PATH = os.getenv("OPENBAO_SECRET_PATH", "energy-demand")
OPENBAO_KV_MOUNT    = os.getenv("OPENBAO_KV_MOUNT", "secret")
OPENBAO_VERIFY_TLS  = os.getenv("OPENBAO_VERIFY_TLS", "true").lower() == "true"


# =============================================================================
# [K8s / 경로 상수]
# =============================================================================
IMAGE_PULL_SECRET   = os.getenv("IMAGE_PULL_SECRET", "gitea-registry-pull")
S3_ARTIFACT_PREFIX  = os.getenv(
    "S3_ARTIFACT_PREFIX",
    f"mlflow/experiments/{EXPERIMENT_SHORT_NAME}/models/",
)
MODEL_REGISTRY_PATH = os.getenv("MODEL_REGISTRY_PATH", "/mnt/data/models")


# =============================================================================
# [추론] test_inference.py 전용
# =============================================================================
INFERENCE_ENDPOINT   = os.getenv("INFERENCE_ENDPOINT", "")
DEPLOYMENT_ID        = os.getenv("DEPLOYMENT_ID", "default")
INFERENCE_VERIFY_TLS = os.getenv("INFERENCE_VERIFY_TLS", "true").lower() == "true"


# =============================================================================
# [헬퍼] OpenBao 시크릿 조회
# =============================================================================
def load_secrets() -> dict:
    """OpenBao KV v2 에서 energy-demand 시크릿 전체를 dict 로 반환."""
    import hvac
    if not OPENBAO_TOKEN:
        raise RuntimeError(
            "OPENBAO_TOKEN 이 비어 있습니다. "
            ".env 또는 환경변수에 설정하세요."
        )
    if not RUNWAY_PROJECT_ID:
        raise RuntimeError(
            "RUNWAY_PROJECT_ID 가 비어 있습니다. .env 또는 환경변수에 설정하세요."
        )
    if not RUNWAY_BASE_DOMAIN:
        raise RuntimeError(
            "RUNWAY_BASE_DOMAIN 이 비어 있습니다. .env 또는 환경변수에 설정하세요. "
            "(예: RUNWAY_BASE_DOMAIN=runway.example.com)"
        )
    kwargs = {"url": OPENBAO_URL, "token": OPENBAO_TOKEN, "verify": OPENBAO_VERIFY_TLS}
    if OPENBAO_NAMESPACE:
        kwargs["namespace"] = OPENBAO_NAMESPACE
    client = hvac.Client(**kwargs)
    try:
        resp = client.secrets.kv.v2.read_secret_version(
            path=OPENBAO_SECRET_PATH,
            mount_point=OPENBAO_KV_MOUNT,
        )
    except hvac.exceptions.Forbidden as e:
        raise RuntimeError(
            "OpenBao 403 Forbidden — OPENBAO_TOKEN 이 만료되었거나 무효합니다.\n"
            f"  OpenBao 콘솔({OPENBAO_URL}) 재로그인 후 토큰 갱신 필요."
        ) from e
    except hvac.exceptions.InvalidPath as e:
        raise RuntimeError(
            f"OpenBao KV 경로 없음: {OPENBAO_KV_MOUNT}/{OPENBAO_SECRET_PATH}. "
            "시크릿을 등록했는지 확인."
        ) from e
    data = resp["data"]["data"]
    print(f"[config] OpenBao 로드: path={OPENBAO_KV_MOUNT}/{OPENBAO_SECRET_PATH} "
          f"keys={list(data.keys())}")
    return data
