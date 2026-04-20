"""
wind_power_prediction_v4.py — KubernetesPodOperator 기반 DAG

v1(PythonOperator) 대비 핵심 변경:
- @task 데코레이터(PythonOperator) → KubernetesPodOperator
- 각 태스크가 별도 K8s Pod에서 실행 (리소스 격리, 독립적 패키지 환경)
- 태스크 간 아티팩트 공유: XCom/PVC 대신 S3(MinIO) prefix 사용
  (DAG_RUN_ID 별로 격리된 경로, {{ run_id }} 템플릿으로 주입)

이미지 빌드:
  Gitea Actions CI/CD (.gitea/workflows/build-image.yml) 가 task_runner.py,
  Dockerfile, requirements.txt, dataset/** 변경 시 자동으로 이미지를 빌드하여
  Gitea Container Registry에 푸시한다. DAG는 :latest 태그를 pull 해서 실행.

파이프라인:
  ensure_pull_secret → [load_data, load_model] → train_model → evaluate_model → log_to_mlflow

공통 사전 준비:
  1. Runway 프로젝트 네임스페이스에 RoleBinding 생성 (airflow scheduler SA → edit)
  2. OpenBao KV v2 에 시크릿 등록:
       namespace=<project-id>, mount=secret, path=wind-power
       { aws_access_key_id, aws_secret_access_key, gitea_username, gitea_password }
  3. 아래 [사용자 설정] 섹션의 상수를 환경에 맞게 수정
"""

from datetime import timedelta

from airflow.sdk import DAG, task
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
from kubernetes.client import models as k8s

# =============================================================================
# [사용자 설정] 환경에 맞게 반드시 수정
# =============================================================================

# ── K8s 공통 설정 ───────────────────────────────────────────────────────────────
NAMESPACE          = "rwyt-energy-forecasting"                                  # Airflow 태스크 Pod가 생성될 namespace
IMAGE              = "gitea.v2.mrxrunway.ai/rwyt-energy-forecasting/wind-power-prediction:latest"  # Gitea CR 이미지 URL
S3_BUCKET          = "rwyt-energy-forecasting"                                  # 태스크 간 아티팩트 공유용 S3 bucket
IMAGE_PULL_SECRET  = "gitea-registry-pull"                                      # ensure_pull_secret 태스크가 자동 생성/갱신

# ── Runway / MLflow 크레덴셜 ───────────────────────────────────────────────────
# - RUNWAY_API_KEY: Keycloak offline token (Runway UI > 사용자 설정 > API 토큰에서 발급)
#   MLflow 인증 토큰으로 사용된다.
# - AWS 키: OpenBao에 등록 (코드 내 하드코딩 없음)
#   task_runner.py / download_model.py 가 런타임에 OpenBao에서 조회
RUNWAY_API_KEY        = "eyJhbGciOiJIUzUxMiIsInR5cCIgOiAiSldUIiwia2lkIiA6ICJkZjVhOWNhNy00NmEzLTQ4YWUtODk2MS01NGEyYTdmMDgzMDAifQ.eyJpYXQiOjE3NzY0MDM1ODAsImp0aSI6ImI3OTlkNmI1LTdjZWUtZWQ2MS05MGI1LWEzNDViZGE2Yzk3OCIsImlzcyI6Imh0dHBzOi8va2V5Y2xvYWsudjIubXJ4cnVud2F5LmFpL3JlYWxtcy9ydW53YXkiLCJhdWQiOiJodHRwczovL2tleWNsb2FrLnYyLm1yeHJ1bndheS5haS9yZWFsbXMvcnVud2F5Iiwic3ViIjoiMGY5Y2QzZmYtMzdiYS00NWNlLWE3ZDItMzIzYTMyYmExNmU1IiwidHlwIjoiT2ZmbGluZSIsImF6cCI6InJ1bndheSIsInNpZCI6IjgxYTBjYTAwLTBhMDMtNGYxNi05M2NhLWRkMjc2YmUwYTgyYiIsInNjb3BlIjoib3BlbmlkIHdlYi1vcmlnaW5zIG9mZmxpbmVfYWNjZXNzIHNlcnZpY2VfYWNjb3VudCBlbWFpbCBwcm9maWxlIn0.XNT9kvg3PTHPq1vrPSEUqOnJehZ-HtpSlWo8Lzyxiv7qWZ6MdLNHw_5W0QIRIczH1kiSLkWeLfnHXAK9-BvoPQ"

MLFLOW_TRACKING_URI    = "https://mlflow.v2.mrxrunway.ai"
MLFLOW_S3_ENDPOINT_URL = "https://s3.v2.mrxrunway.ai"

# ── OpenBao 설정 ────────────────────────────────────────────────────────────────
# 1) OpenBao 웹 콘솔에서 KV v2로 시크릿 등록:
#      secret/data/<OPENBAO_SECRET_PATH> → { aws_access_key_id, aws_secret_access_key }
# 2) OpenBao 콘솔에 namespace path로 로그인 → 자동 발급되는 서비스 토큰을 복사하여
#    OPENBAO_TOKEN 에 입력 (만료 시 갱신 필요)
OPENBAO_URL         = "https://openbao.v2.mrxrunway.ai"
OPENBAO_NAMESPACE   = "rwyt-energy-forecasting"
OPENBAO_TOKEN       = "s.F6DrHBKlEENqMQvAAoBKjpJ8.detel9"
OPENBAO_SECRET_PATH = "wind-power"
OPENBAO_KV_MOUNT    = "secret"


# =============================================================================
# [환경 변수] 모든 ML Pod에 공통으로 주입되는 환경변수
# - task_runner.py가 os.getenv()로 읽어 사용
# - DAG_RUN_ID: Airflow 템플릿 {{ run_id }}로 각 DAG run마다 고유한 S3 prefix 생성
# - AWS 키는 Pod 내부에서 OpenBao 조회로 대체 (env로 주입하지 않음)
# =============================================================================
common_env_vars = [
    k8s.V1EnvVar(name="RUNWAY_API_KEY",        value=RUNWAY_API_KEY),
    k8s.V1EnvVar(name="MLFLOW_TRACKING_URI",    value=MLFLOW_TRACKING_URI),
    k8s.V1EnvVar(name="MLFLOW_S3_ENDPOINT_URL", value=MLFLOW_S3_ENDPOINT_URL),
    k8s.V1EnvVar(name="S3_BUCKET",              value=S3_BUCKET),
    k8s.V1EnvVar(name="DAG_RUN_ID",             value="{{ run_id }}"),
    k8s.V1EnvVar(name="OPENBAO_URL",            value=OPENBAO_URL),
    k8s.V1EnvVar(name="OPENBAO_NAMESPACE",      value=OPENBAO_NAMESPACE),
    k8s.V1EnvVar(name="OPENBAO_TOKEN",          value=OPENBAO_TOKEN),
    k8s.V1EnvVar(name="OPENBAO_SECRET_PATH",    value=OPENBAO_SECRET_PATH),
    k8s.V1EnvVar(name="OPENBAO_KV_MOUNT",       value=OPENBAO_KV_MOUNT),
]


# =============================================================================
# [초기화 태스크] imagePullSecret 자동 생성
# - Runway 환경에서 namespace 내 시크릿이 주기적으로 사라지는 현상이 관측됨
# - 매 DAG 실행 전에 OpenBao의 gitea_username/gitea_password 로 dockerconfigjson
#   타입 K8s Secret(`gitea-registry-pull`)을 create_or_update 한다
# - Airflow scheduler SA 의 edit 권한(runway-applications:airflow-scheduler →
#   rwyt-energy-forecasting) 으로 K8s API 호출
# - 사전 준비: OpenBao KV 에 gitea_username, gitea_password 추가 등록
# =============================================================================
@task
def ensure_pull_secret() -> None:
    """OpenBao 에서 Gitea 자격증명을 읽어와 imagePullSecret 을 생성/갱신한다."""
    import base64
    import json
    import ssl
    import urllib.request

    from kubernetes import client, config

    # 1. OpenBao 에서 Gitea 자격증명 조회
    vault_url = f"{OPENBAO_URL}/v1/{OPENBAO_KV_MOUNT}/data/{OPENBAO_SECRET_PATH}"
    req = urllib.request.Request(
        vault_url,
        headers={
            "X-Vault-Token": OPENBAO_TOKEN,
            "X-Vault-Namespace": OPENBAO_NAMESPACE,
        },
    )
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, context=ctx) as r:
        body = json.loads(r.read())
    secrets = body["data"]["data"]
    gitea_username = secrets["gitea_username"]
    gitea_password = secrets["gitea_password"]
    print("[ensure_pull_secret] OpenBao 에서 Gitea 자격증명 조회 완료")

    # 2. dockerconfigjson 구성
    auth_b64 = base64.b64encode(f"{gitea_username}:{gitea_password}".encode()).decode()
    docker_config = {
        "auths": {
            "gitea.v2.mrxrunway.ai": {
                "username": gitea_username,
                "password": gitea_password,
                "auth": auth_b64,
            }
        }
    }
    docker_config_b64 = base64.b64encode(json.dumps(docker_config).encode()).decode()

    # 3. K8s Secret create_or_update (Airflow scheduler in-cluster SA 사용)
    config.load_incluster_config()
    v1 = client.CoreV1Api()
    secret_body = client.V1Secret(
        metadata=client.V1ObjectMeta(name=IMAGE_PULL_SECRET, namespace=NAMESPACE),
        type="kubernetes.io/dockerconfigjson",
        data={".dockerconfigjson": docker_config_b64},
    )
    try:
        v1.read_namespaced_secret(name=IMAGE_PULL_SECRET, namespace=NAMESPACE)
        v1.patch_namespaced_secret(name=IMAGE_PULL_SECRET, namespace=NAMESPACE, body=secret_body)
        print(f"[ensure_pull_secret] 기존 Secret 갱신: {NAMESPACE}/{IMAGE_PULL_SECRET}")
    except client.exceptions.ApiException as e:
        if e.status == 404:
            v1.create_namespaced_secret(namespace=NAMESPACE, body=secret_body)
            print(f"[ensure_pull_secret] 신규 Secret 생성: {NAMESPACE}/{IMAGE_PULL_SECRET}")
        else:
            raise


def make_pod_operator(
    task_id: str,
    step: str,
    cpu_request: str = "200m",
    cpu_limit: str = "500m",
    memory_request: str = "256Mi",
    memory_limit: str = "512Mi",
) -> KubernetesPodOperator:
    """공통 설정을 가진 KubernetesPodOperator를 생성한다."""
    pull_secrets = (
        [k8s.V1LocalObjectReference(name=IMAGE_PULL_SECRET)]
        if IMAGE_PULL_SECRET else []
    )
    return KubernetesPodOperator(
        task_id=task_id,
        namespace=NAMESPACE,
        image=IMAGE,
        image_pull_policy="Always",  # 개발 중 항상 최신 이미지 사용. 안정화 후 IfNotPresent로 변경
        cmds=["python", "task_runner.py", "--step", step],
        env_vars=common_env_vars,
        container_resources=k8s.V1ResourceRequirements(
            requests={"cpu": cpu_request, "memory": memory_request},
            limits={"cpu": cpu_limit,    "memory": memory_limit},
        ),
        image_pull_secrets=pull_secrets,
        is_delete_operator_pod=True,  # Pod 완료 후 자동 삭제 (로그는 Airflow UI에서 확인)
        get_logs=True,                # Pod stdout/stderr를 Airflow 태스크 로그로 스트리밍
        log_events_on_failure=True,  # 실패 시 K8s 이벤트 로그 출력
        in_cluster=True,
        startup_timeout_seconds=300,
    )


# =============================================================================
# [DAG 정의]
# =============================================================================
default_args = {
    "owner": "gyuseon.han@makinarocks.ai",
    "depends_on_past": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="wind_power_prediction_v4",
    default_args=default_args,
    description="Wind power prediction with XGBoost + MLflow tracking (KubernetesPodOperator)",
    schedule=None,   # 수동 trigger 전용
    catchup=False,
    tags=["ml", "xgboost", "wind-power", "kubernetes"],
) as dag:

    # =========================================================================
    # Task 1: 데이터셋 로드
    # =========================================================================
    t_load_data = make_pod_operator(
        task_id="load_data",
        step="load_data",
        cpu_request="200m",
        cpu_limit="500m",
        memory_request="256Mi",
        memory_limit="512Mi",
    )

    # =========================================================================
    # Task 2: 모델 초기화
    # =========================================================================
    t_load_model = make_pod_operator(
        task_id="load_model",
        step="load_model",
        cpu_request="200m",
        cpu_limit="500m",
        memory_request="256Mi",
        memory_limit="512Mi",
    )

    # =========================================================================
    # Task 3: 모델 학습
    # =========================================================================
    t_train_model = make_pod_operator(
        task_id="train_model",
        step="train_model",
        cpu_request="1",
        cpu_limit="2",
        memory_request="1Gi",
        memory_limit="2Gi",
    )

    # =========================================================================
    # Task 4: 모델 평가
    # =========================================================================
    t_evaluate_model = make_pod_operator(
        task_id="evaluate_model",
        step="evaluate_model",
        cpu_request="500m",
        cpu_limit="1",
        memory_request="512Mi",
        memory_limit="1Gi",
    )

    # =========================================================================
    # Task 5: MLflow 로깅
    # =========================================================================
    t_log_to_mlflow = make_pod_operator(
        task_id="log_to_mlflow",
        step="log_to_mlflow",
        cpu_request="200m",
        cpu_limit="500m",
        memory_request="512Mi",
        memory_limit="1Gi",
    )

    # =========================================================================
    # [태스크 의존성]
    #   ensure_pull_secret → [load_data, load_model] → train_model → evaluate_model → log_to_mlflow
    # 이미지 빌드는 Gitea Actions(.gitea/workflows/build-image.yml)가 처리
    # =========================================================================
    t_ensure_pull_secret = ensure_pull_secret()
    t_ensure_pull_secret >> [t_load_data, t_load_model]
    [t_load_data, t_load_model] >> t_train_model >> t_evaluate_model >> t_log_to_mlflow











