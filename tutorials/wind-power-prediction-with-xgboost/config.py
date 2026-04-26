"""
config.py — 튜토리얼 전역 설정 모듈

이 파일이 무엇인가?
  사용자/프로젝트별로 달라지는 값들을 한 곳에 모으고, "Runway 프로젝트 규약"
  (namespace = S3 bucket = OpenBao namespace = Gitea 조직) 에서 자동 파생되는
  값들을 계산해주는 모듈. task_runner.py / download_model.py / test_inference.py
  가 공유.

  **DAG(wind_power_prediction_v4.py) 는 이 파일을 import 하지 않는다.** DAG 는
  airflow-dags 저장소로 sync 되어 스케줄러 Pod 에서 실행되므로 config.py 가
  거기 없다. 대신 DAG 상단에 `RUNWAY_PROJECT_ID` 와 `OPENBAO_TOKEN` 두 줄만
  하드코딩하고 파생값은 DAG 안에서 f-string 으로 직접 계산한다.

값의 공급 경로:
  - IDE 스크립트 (download_model, test_inference) : 저장소 루트의 `.env` 파일
    → `load_dotenv()` 가 `os.environ` 에 로드 → config.py 가 `os.getenv()` 로 해석
  - task_runner.py (KPO Pod) : DAG 의 `common_env_vars` 로 Pod env 에 주입
    → `.env` 파일은 없지만 `load_dotenv()` 는 조용히 no-op → 동일하게 동작

사용자가 실제로 편집할 값:
  - `.env` 의 `RUNWAY_PROJECT_ID`, `OPENBAO_TOKEN` (그 외 다 자동)
  - 필요 시 `INFERENCE_ENDPOINT`, `DEPLOYMENT_ID` (test_inference.py 용)
"""

import os

# ─── .env 자동 로드 ──────────────────────────────────────────────────────────
# 스크립트를 어디서 실행하든 저장소 루트(.env 위치)를 찾도록 이 파일 위치 기준으로 로드.
# 컨테이너 안에서는 .env 가 없지만 load_dotenv 는 조용히 지나감.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    # python-dotenv 가 없는 환경에서도 동작해야 함 — env var 가 직접 주입된 경우
    pass


# =============================================================================
# [필수] 사용자마다 다른 값
# =============================================================================
# RUNWAY_PROJECT_ID : Runway 프로젝트 식별자. Runway 규약상 아래와 동일:
#   - K8s namespace
#   - S3 bucket 이름
#   - OpenBao namespace
#   - Gitea 조직명
#   이 한 값으로 NAMESPACE / S3_BUCKET / OPENBAO_NAMESPACE / IMAGE 등이 자동 파생.
RUNWAY_PROJECT_ID = os.getenv("RUNWAY_PROJECT_ID", "")

# RUNWAY_BASE_DOMAIN : Runway 가 배포된 베이스 도메인 (예: v2.example.com).
#   이 값으로 mlflow./s3./openbao./gitea./inference./airflow./runway. 등의 서비스
#   엔드포인트가 모두 자동 파생된다. 본인 환경의 베이스 도메인으로 교체 필수.
RUNWAY_BASE_DOMAIN = os.getenv("RUNWAY_BASE_DOMAIN", "")


# =============================================================================
# [Runway 인프라] 베이스 도메인에서 파생되는 서비스 엔드포인트
# 각 값은 env 로 override 가능 — 비표준 호스트명을 쓰는 경우.
# =============================================================================
MLFLOW_TRACKING_URI    = os.getenv("MLFLOW_TRACKING_URI",    f"https://mlflow.{RUNWAY_BASE_DOMAIN}")
MLFLOW_S3_ENDPOINT_URL = os.getenv("MLFLOW_S3_ENDPOINT_URL", f"https://s3.{RUNWAY_BASE_DOMAIN}")
OPENBAO_URL            = os.getenv("OPENBAO_URL",            f"https://openbao.{RUNWAY_BASE_DOMAIN}")
GITEA_REGISTRY_HOST    = os.getenv("GITEA_REGISTRY_HOST",    f"gitea.{RUNWAY_BASE_DOMAIN}")


# =============================================================================
# [파생값] RUNWAY_PROJECT_ID 에서 자동 계산
# =============================================================================
# 각 값은 env 로 override 가능 — 특수한 프로젝트 규약을 쓰는 경우.
NAMESPACE           = os.getenv("NAMESPACE",         RUNWAY_PROJECT_ID)
S3_BUCKET           = os.getenv("S3_BUCKET",         RUNWAY_PROJECT_ID)
OPENBAO_NAMESPACE   = os.getenv("OPENBAO_NAMESPACE", RUNWAY_PROJECT_ID)

# 이미지 태그: Gitea Actions 가 :latest 로 푸시하므로 기본값 그대로 쓰면 됨.
IMAGE_NAME = os.getenv("IMAGE_NAME", "wind-power-prediction")
IMAGE_TAG  = os.getenv("IMAGE_TAG",  "latest")
IMAGE      = os.getenv(
    "IMAGE",
    f"{GITEA_REGISTRY_HOST}/{RUNWAY_PROJECT_ID}/{IMAGE_NAME}:{IMAGE_TAG}",
)

# MLflow experiment/model 명명 규칙: "{프로젝트ID}.{짧은이름}"
# → 프로젝트 ID 가 prefix 로 들어가야 Runway MLflow 권한 체크 통과.
EXPERIMENT_SHORT_NAME = os.getenv("EXPERIMENT_SHORT_NAME", "wind-power-prediction")
EXPERIMENT_NAME       = os.getenv(
    "EXPERIMENT_NAME",
    f"{RUNWAY_PROJECT_ID}.{EXPERIMENT_SHORT_NAME}",
)
MODEL_SHORT_NAME      = os.getenv("MODEL_SHORT_NAME", "wind-power-xgboost")
MODEL_NAME            = os.getenv(
    "MODEL_NAME",
    f"{RUNWAY_PROJECT_ID}.{MODEL_SHORT_NAME}",
)


# =============================================================================
# [OpenBao] 시크릿 저장소 설정 및 토큰
# =============================================================================
# OPENBAO_TOKEN : 콘솔 namespace 로그인 시 자동 발급되는 서비스 토큰.
#                 이 값이 비어 있으면 load_secrets() 호출 시 예외.
# SECRET_PATH / KV_MOUNT : 이 튜토리얼은 secret/wind-power 경로를 사용.
# VERIFY_TLS : Runway OpenBao 는 공식 CA 서명 → true 가 기본. 자체 서명만 false.
OPENBAO_TOKEN       = os.getenv("OPENBAO_TOKEN", "")
OPENBAO_SECRET_PATH = os.getenv("OPENBAO_SECRET_PATH", "wind-power")
OPENBAO_KV_MOUNT    = os.getenv("OPENBAO_KV_MOUNT", "secret")
OPENBAO_VERIFY_TLS  = os.getenv("OPENBAO_VERIFY_TLS", "true").lower() == "true"


# =============================================================================
# [K8s / 경로 상수]
# =============================================================================
IMAGE_PULL_SECRET   = os.getenv("IMAGE_PULL_SECRET", "gitea-registry-pull")
# MLflow 가 모델 아티팩트를 올려두는 S3 경로 prefix.
# EXPERIMENT_SHORT_NAME 과 연동 — 실험명 바꿨다면 여기도 함께 갱신됨.
S3_ARTIFACT_PREFIX  = os.getenv(
    "S3_ARTIFACT_PREFIX",
    f"mlflow/experiments/{EXPERIMENT_SHORT_NAME}/models/",
)
# 모델 배포 UI 가 인식하는 PVC 마운트 경로
MODEL_REGISTRY_PATH = os.getenv("MODEL_REGISTRY_PATH", "/mnt/models")


# =============================================================================
# [추론] test_inference.py 전용
# =============================================================================
# INFERENCE_ENDPOINT : 9단계(모델 배포) 완료 후 엔드포인트 상세 페이지에서 복사한 추론 URL.
#                      형식: https://inference.<runway-base-domain>/api/<project>/<endpoint>/<deployment>
#                      (이 URL 에 이미 프로젝트/엔드포인트/배포 경로가 모두 포함됨)
# DEPLOYMENT_ID      : KServe V2 경로의 models/<name>/infer 에서 <name> 에 들어가는 값.
#                      Runway MLServer 는 내부 모델명을 "default" 로 고정하므로 기본값 default.
#                      (UI 의 "배포 ID" 와 다른 개념 — 그 값은 INFERENCE_ENDPOINT URL 경로에 이미 포함)
INFERENCE_ENDPOINT   = os.getenv("INFERENCE_ENDPOINT", "")
DEPLOYMENT_ID        = os.getenv("DEPLOYMENT_ID", "default")
INFERENCE_VERIFY_TLS = os.getenv("INFERENCE_VERIFY_TLS", "true").lower() == "true"


# =============================================================================
# [헬퍼] OpenBao 시크릿 조회
# =============================================================================
def load_secrets() -> dict:
    """OpenBao KV v2 에서 wind-power 시크릿 전체를 dict 로 반환.

    기대되는 키:
      - aws_access_key_id, aws_secret_access_key
        (task_runner / download_model 이 S3(MinIO) 접근 시 사용)
      - gitea_username, gitea_password
        (ensure_pull_secret 이 imagePullSecret dockerconfigjson 구성 시 사용)
      - runway_api_key
        (Keycloak offline token. MLflow 인증 및 추론 엔드포인트 Bearer 토큰)

    사전 준비:
      OpenBao 콘솔 > <namespace> > Secret Engines > secret/ > wind-power 에
      위 5개 키를 등록. 값이 빠져있으면 호출부에서 KeyError.

    TLS:
      OPENBAO_VERIFY_TLS 정책 (기본 true) 적용.

    Raises:
      RuntimeError: OPENBAO_TOKEN / RUNWAY_PROJECT_ID 미설정, 토큰 만료(403),
                    또는 KV 경로 없음(404). 각 케이스별로 구체적인 해결 안내 포함.
    """
    import hvac
    if not OPENBAO_TOKEN:
        raise RuntimeError(
            "OPENBAO_TOKEN 이 비어 있습니다. "
            ".env / 환경변수 / DAG 상단 상수 중 하나에 설정하세요."
        )
    if not RUNWAY_PROJECT_ID:
        # Runway multi-tenant 에선 namespace 없이는 KV 조회가 실패 → 명확한 에러
        raise RuntimeError(
            "RUNWAY_PROJECT_ID 가 비어 있습니다. .env 또는 환경변수에 설정하세요. "
            "(.env.example 참고)"
        )
    if not RUNWAY_BASE_DOMAIN:
        # OPENBAO_URL 이 'https://openbao.' 형태로 잘못 만들어진 채 호출되면 DNS 에러가 남
        raise RuntimeError(
            "RUNWAY_BASE_DOMAIN 이 비어 있습니다. .env 또는 환경변수에 설정하세요. "
            "(예: RUNWAY_BASE_DOMAIN=v2.example.com — 본인 환경의 Runway 베이스 도메인)"
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
        # 403 — 가장 흔한 원인은 Runway 세션 재로그인으로 토큰 무효화됨
        raise RuntimeError(
            "OpenBao 403 Forbidden — OPENBAO_TOKEN 이 만료되었거나 무효합니다.\n"
            f"  1) OpenBao 콘솔({OPENBAO_URL}) 접속\n"
            "  2) 우측 상단 프로필 → Copy token 으로 새 토큰 복사\n"
            "  3) 아래 두 곳 모두 갱신 필요:\n"
            "     - .env 파일의 OPENBAO_TOKEN\n"
            "     - wind_power_prediction_v4.py 상단 OPENBAO_TOKEN (DAG 실행용)\n"
            "  4) git push 후 Sync DAG 워크플로우 완료 확인 → Airflow 재파싱"
        ) from e
    except hvac.exceptions.InvalidPath as e:
        raise RuntimeError(
            f"OpenBao KV 경로 없음: {OPENBAO_KV_MOUNT}/{OPENBAO_SECRET_PATH}. "
            "README 4단계 / WALKTHROUGH 5-4 참조하여 시크릿을 등록했는지 확인."
        ) from e
    data = resp["data"]["data"]
    # ℹ️ 값은 로그에 남기지 않고 키 이름만 노출 (디버깅용)
    print(f"[config] OpenBao 로드: path={OPENBAO_KV_MOUNT}/{OPENBAO_SECRET_PATH} "
          f"keys={list(data.keys())}")
    return data
