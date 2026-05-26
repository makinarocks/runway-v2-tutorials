"""
energy_demand_prediction.py — KubernetesPodOperator 기반 Airflow DAG

에너지 수요량 72시간 예측 모델의 학습 파이프라인.
wind-power-prediction DAG 와 동일한 패턴이나 두 가지 차이:
  1. PVC volume mount — 데이터셋을 Docker 이미지가 아닌 PVC 에서 읽음
  2. MultiOutputRegressor(72 타겟) — pyfunc 래퍼로 MLflow 등록

파이프라인:
  ensure_pull_secret → load_data → train_model → evaluate_model → log_to_mlflow

사용자 설정: 아래 3줄만 본인 값으로 수정.
"""

from datetime import timedelta

from airflow.sdk import DAG, task
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
from kubernetes.client import models as k8s

# =============================================================================
# [사용자 설정] 3줄만 수정
# =============================================================================
RUNWAY_PROJECT_ID  = "<your-project-id>"
RUNWAY_BASE_DOMAIN = "<your-runway-domain>"
OPENBAO_TOKEN      = "<your-openbao-token>"


# =============================================================================
# [파생값]
# =============================================================================
NAMESPACE            = RUNWAY_PROJECT_ID
IMAGE                = f"gitea.{RUNWAY_BASE_DOMAIN}/{RUNWAY_PROJECT_ID}/energy-demand-prediction:latest"
IMAGE_PULL_SECRET    = "gitea-registry-pull"

OPENBAO_URL          = f"https://openbao.{RUNWAY_BASE_DOMAIN}"
OPENBAO_NAMESPACE    = RUNWAY_PROJECT_ID
OPENBAO_SECRET_PATH  = "energy-demand"
OPENBAO_KV_MOUNT     = "secret"
OPENBAO_VERIFY_TLS   = "true"

# PVC 이름 — Runway 콘솔에서 생성한 볼륨 ID
PVC_NAME = "<your-pvc-name>"


# =============================================================================
# [환경 변수] 모든 ML Pod 에 주입
# RUNWAY_BASE_DOMAIN 추가 — config.py 가 서비스 URL 파생에 필요
# TRAIN_FILES — DAG trigger 시 conf 로 주입 가능 (기본: Q1.csv, 재학습: Q1.csv,Q2.csv,Q3.csv)
# =============================================================================
TRAIN_FILES_DEFAULT = "Q1.csv"

common_env_vars = [
    k8s.V1EnvVar(name="RUNWAY_PROJECT_ID",  value=RUNWAY_PROJECT_ID),
    k8s.V1EnvVar(name="RUNWAY_BASE_DOMAIN", value=RUNWAY_BASE_DOMAIN),
    k8s.V1EnvVar(name="OPENBAO_TOKEN",      value=OPENBAO_TOKEN),
    k8s.V1EnvVar(name="OPENBAO_VERIFY_TLS", value=OPENBAO_VERIFY_TLS),
    k8s.V1EnvVar(name="DAG_RUN_ID",         value="{{ run_id }}"),
    k8s.V1EnvVar(name="TRAIN_FILES",        value="{{ dag_run.conf.get('train_files', '" + TRAIN_FILES_DEFAULT + "') }}"),
]


# =============================================================================
# [PVC Volume] 데이터셋용 — wind-power 와 다른 점
# =============================================================================
data_volume = k8s.V1Volume(
    name="data",
    persistent_volume_claim=k8s.V1PersistentVolumeClaimVolumeSource(
        claim_name=PVC_NAME,
    ),
)
data_volume_mount = k8s.V1VolumeMount(
    name="data",
    mount_path="/mnt/data",
    read_only=True,
)


# =============================================================================
# [초기화 태스크] imagePullSecret 자동 생성
# =============================================================================
@task
def ensure_pull_secret() -> None:
    """OpenBao 에서 Gitea 자격증명을 읽어와 imagePullSecret 을 생성/갱신."""
    import base64
    import json
    import ssl
    import urllib.request
    from kubernetes import client, config

    vault_url = f"{OPENBAO_URL}/v1/{OPENBAO_KV_MOUNT}/data/{OPENBAO_SECRET_PATH}"
    req = urllib.request.Request(
        vault_url,
        headers={
            "X-Vault-Token": OPENBAO_TOKEN,
            "X-Vault-Namespace": OPENBAO_NAMESPACE,
        },
    )
    verify_tls = OPENBAO_VERIFY_TLS.lower() == "true"
    ctx = ssl.create_default_context()
    if not verify_tls:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, context=ctx) as r:
        body = json.loads(r.read())
    secrets = body["data"]["data"]
    gitea_username = secrets["gitea_username"]
    gitea_password = secrets["gitea_password"]
    print("[ensure_pull_secret] OpenBao 에서 Gitea 자격증명 조회 완료")

    auth_b64 = base64.b64encode(f"{gitea_username}:{gitea_password}".encode()).decode()
    gitea_registry_host = IMAGE.split("/", 1)[0]
    docker_config = {
        "auths": {
            gitea_registry_host: {
                "username": gitea_username,
                "password": gitea_password,
                "auth": auth_b64,
            }
        }
    }
    docker_config_b64 = base64.b64encode(json.dumps(docker_config).encode()).decode()

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


# =============================================================================
# [헬퍼] KubernetesPodOperator 팩토리
# =============================================================================
def make_pod_operator(
    task_id: str,
    step: str,
    cpu_request: str = "500m",
    cpu_limit: str = "1",
    memory_request: str = "512Mi",
    memory_limit: str = "1Gi",
) -> KubernetesPodOperator:
    pull_secrets = (
        [k8s.V1LocalObjectReference(name=IMAGE_PULL_SECRET)]
        if IMAGE_PULL_SECRET else []
    )
    return KubernetesPodOperator(
        task_id=task_id,
        namespace=NAMESPACE,
        image=IMAGE,
        image_pull_policy="Always",
        cmds=["python", "task_runner.py", "--step", step],
        env_vars=common_env_vars,
        container_resources=k8s.V1ResourceRequirements(
            requests={"cpu": cpu_request, "memory": memory_request},
            limits={"cpu": cpu_limit, "memory": memory_limit},
        ),
        volumes=[data_volume],
        volume_mounts=[data_volume_mount],
        image_pull_secrets=pull_secrets,
        is_delete_operator_pod=True,
        get_logs=True,
        log_events_on_failure=True,
        in_cluster=True,
        startup_timeout_seconds=300,
    )


# =============================================================================
# [DAG 정의]
# =============================================================================
default_args = {
    "owner": "your-email@example.com",
    "depends_on_past": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id=f"energy_demand_prediction_{RUNWAY_PROJECT_ID}",
    default_args=default_args,
    description="Energy demand 72h prediction with XGBoost MultiOutput + MLflow (KubernetesPodOperator)",
    schedule=None,
    catchup=False,
    tags=["ml", "xgboost", "energy-demand", "kubernetes", f"project:{RUNWAY_PROJECT_ID}"],
    params={
        "train_files": TRAIN_FILES_DEFAULT,  # GUI 재학습 시 "Q1.csv,Q2.csv,Q3.csv" 전달
    },
) as dag:

    t_load_data = make_pod_operator(
        task_id="load_data",
        step="load_data",
        cpu_request="500m",
        cpu_limit="1",
        memory_request="512Mi",
        memory_limit="1Gi",
    )

    t_train_model = make_pod_operator(
        task_id="train_model",
        step="train_model",
        cpu_request="2",
        cpu_limit="4",
        memory_request="2Gi",
        memory_limit="4Gi",
    )

    t_evaluate_model = make_pod_operator(
        task_id="evaluate_model",
        step="evaluate_model",
        cpu_request="1",
        cpu_limit="2",
        memory_request="4Gi",
        memory_limit="8Gi",
    )

    t_log_to_mlflow = make_pod_operator(
        task_id="log_to_mlflow",
        step="log_to_mlflow",
        cpu_request="1",
        cpu_limit="2",
        memory_request="4Gi",
        memory_limit="8Gi",
    )

    t_ensure_pull_secret = ensure_pull_secret()
    t_ensure_pull_secret >> t_load_data >> t_train_model >> t_evaluate_model >> t_log_to_mlflow
