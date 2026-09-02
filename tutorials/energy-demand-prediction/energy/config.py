"""환경값·시크릿 — OpenBao Agent Injector 가 마운트한 env 파일에서 자동 적재.

## 왜 파일을 직접 읽는가 (v1.4.0 변경)

이전에는 손으로 쓴 Vault 템플릿이 `export KEY="value"` 형식으로 /vault/secrets/creds.env 를
만들고, entrypoint 에서 `source` 해서 셸에 채웠다. v1.4.0 부터는 **앱 템플릿 폼의
`OpenBao secret engine` / `OpenBao secret name` 필드**를 쓴다. 차트가 annotation 을 자동
생성하는데, 그 템플릿은 형식이 다르다.

    /vault/secrets/<secretName>.env   (예: energy.env)
    runway_project_id=tutorial-heat-demand      <- OpenBao 키 이름 그대로(소문자), export 없음

그래서 이 모듈이 그 파일을 직접 읽어 **대문자 env 로 올린다**. 결과적으로
`source /vault/secrets/creds.env` 가 어디에서도 필요 없어진다 — DAG task 의 command,
Code Server 터미널, 추론 테스트 모두 그냥 python 을 실행하면 된다.

파일 경로는 RUNWAY_SECRET_ENV_FILE 로 덮어쓸 수 있다(로컬 실행·디버깅용).
"""
import os

# OpenBao secret name 이 `energy` 이므로 차트가 만드는 파일은 energy.env 다.
_SECRET_ENV_FILE = os.getenv("RUNWAY_SECRET_ENV_FILE", "/vault/secrets/energy.env")


def _load_secret_env(path: str = _SECRET_ENV_FILE) -> None:
    """`key=value` 파일을 읽어 대문자 이름의 환경변수로 올린다.

    - 이미 환경변수로 들어와 있으면 덮어쓰지 않는다(직접 지정한 값이 우선).
    - 옛 형식(`export KEY="value"`)도 그대로 읽히게 접두사와 따옴표를 벗긴다 —
      1.3.x 로 만든 앱을 그대로 두고 이미지만 올린 경우를 대비한 하위 호환.
    """
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            key, sep, value = line.partition("=")
            if not sep:
                continue
            os.environ.setdefault(key.strip().upper(), value.strip().strip('"').strip("'"))


_load_secret_env()
# 옛 파일명도 함께 시도한다(1.3.x 호환). 위에서 채워진 값은 setdefault 라 덮이지 않는다.
_load_secret_env("/vault/secrets/creds.env")

# OpenBao 에 등록한 2개 base 값 (0단계에서 등록)
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
    """워크로드에 주입된 시크릿 3개를 dict 로 반환.

    출처가 두 군데로 나뉜다 (v1.4.0):
      - AWS_* 2개 : 프로젝트에 자동 제공되는 `s3-rw` Secret (배포 시 envFrom 으로 연결)
      - RUNWAY_API_KEY : OpenBao `secret/energy` (Agent Injector 가 파일로 마운트 → 위 로더가 적재)

    task_runner.py / download_model.py / test_inference.py 가 이 함수를 그대로 호출한다.
    """
    return {
        "aws_access_key_id":     os.environ["AWS_ACCESS_KEY_ID"],
        "aws_secret_access_key": os.environ["AWS_SECRET_ACCESS_KEY"],
        "runway_api_key":        os.environ["RUNWAY_API_KEY"],
    }
