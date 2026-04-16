"""
wind_power_prediction_v2.py — KubernetesPodOperator 기반 DAG

v1과의 차이점:
- @task 데코레이터(PythonOperator) → KubernetesPodOperator
- 각 태스크가 별도 K8s Pod에서 실행 (리소스 격리, 독립적 패키지 환경)
- 태스크 간 중간 아티팩트: /tmp 대신 공유 PVC(/mnt/shared-workspace) 사용
- XCom 불필요: 모든 아티팩트를 공유 PVC 고정 경로로 주고받음
- load_data / load_model 진짜 병렬 실행 가능 (K8s Pod는 fd fork 문제 없음)

이미지 빌드 방식 (BUILD_MODE):
  "kaniko" [Option A] DAG 실행 시작 시 Kaniko Pod가 자동으로 이미지를 빌드 & 푸시
           파이프라인:
             build_image → [load_data, load_model] → train_model → evaluate_model → log_to_mlflow
           사전 준비:
             1. 레지스트리 인증 Secret 생성:
                  kubectl create secret generic registry-credentials \\
                    --from-file=.dockerconfigjson=$HOME/.docker/config.json \\
                    --type=kubernetes.io/dockerconfigjson \\
                    -n <NAMESPACE>
             2. GITEA_REPO_URL, GITEA_TOKEN 설정

  "manual" [Option B] 이미지를 직접 빌드하거나 Gitea Actions CI/CD로 자동 빌드
           - 직접 빌드:  docker build -t <IMAGE> . && docker push <IMAGE>
           - CI/CD 자동: .gitea/workflows/build-image.yml 이 git push 시 자동 실행
           파이프라인:
             [load_data, load_model] → train_model → evaluate_model → log_to_mlflow

공통 사전 준비:
  1. 공유 볼륨 생성:
       Runway 플랫폼 UI에서 볼륨을 생성하고 볼륨 ID를 아래 SHARED_VOLUME_ID 에 입력
       (여러 Pod가 동시에 마운트하므로 ReadWriteMany 모드 지원 볼륨 사용)

  2. 아래 [사용자 설정] 섹션의 상수를 환경에 맞게 수정
"""

from datetime import timedelta

from airflow.sdk import DAG, task
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
from kubernetes.client import models as k8s

# =============================================================================
# [사용자 설정] 환경에 맞게 반드시 수정
# =============================================================================

# ── 빌드 옵션 ──────────────────────────────────────────────────────────────────
# 아래 두 줄 중 하나를 선택해서 주석 해제하세요.
# BUILD_MODE = "kaniko"  # Option A: DAG 실행 시 Kaniko Pod가 자동으로 이미지를 빌드 & 푸시
BUILD_MODE = "manual"    # Option B: 직접 빌드하거나 Gitea Actions CI/CD (.gitea/workflows/build-image.yml) 사용

# ── K8s 공통 설정 ───────────────────────────────────────────────────────────────
NAMESPACE          = "rwyt-energy-forecasting"                                  # Airflow 태스크 Pod가 생성될 namespace
IMAGE              = "gitea.v2.mrxrunway.ai/rwyt-energy-forecasting/wind-power-prediction:latest"  # [수정] Gitea CR 이미지 URL
S3_BUCKET          = "rwyt-energy-forecasting"                                  # 태스크 간 아티팩트 공유용 S3 bucket
IMAGE_PULL_SECRET  = "gitea-registry-pull"                                      # Gitea CR pull secret (namespace에 존재해야 함)

# Gitea CR Secret (두 가지 Secret 사용)
#
# [1] gitea-registry (Opaque) — Kaniko git clone 인증용
#     username / password 키를 secretKeyRef로 참조
#     생성 명령어:
#       kubectl create secret generic gitea-registry \
#         --from-literal=username=<gitea-username> \
#         --from-literal=password=<gitea-token> \
#         -n <NAMESPACE>
#
# [2] gitea-registry-pull (kubernetes.io/dockerconfigjson) — 이미지 pull + Kaniko push용
#     K8s imagePullSecrets는 dockerconfigjson 타입만 허용하므로 별도 Secret 필요
#     생성 명령어:
#       kubectl create secret docker-registry gitea-registry-pull \
#         --docker-server=gitea.v2.mrxrunway.ai \
#         --docker-username=<gitea-username> \
#         --docker-password=<gitea-token> \
#         -n <NAMESPACE>
GIT_SECRET      = "gitea-registry"       # Kaniko git clone 인증 (Opaque, username/password)
REGISTRY_SECRET = "gitea-registry-pull"  # Kaniko docker config 마운트용 (IMAGE_PULL_SECRET과 동일)

# ── Kaniko 설정 (BUILD_MODE = "kaniko" 일 때만 사용) ────────────────────────────
GITEA_REPO_URL     = "https://gitea.v2.mrxrunway.ai/rwyt-energy-forecasting/wind-power-prediction"  # Gitea 저장소 URL
GIT_BRANCH         = "main"
DOCKERFILE_SUBPATH = ""  # Dockerfile이 저장소 루트에 위치하므로 빈 문자열

# ── Runway / MLflow 크레덴셜 ───────────────────────────────────────────────────
# Runway UI > 사용자 설정 > API 토큰에서 발급
# 프로덕션 환경에서는 Kubernetes Secret 사용 권장:
#   kubectl create secret generic wind-power-secret \
#     --from-literal=RUNWAY_API_KEY=<token> \
#     --from-literal=AWS_ACCESS_KEY_ID=<key> \
#     --from-literal=AWS_SECRET_ACCESS_KEY=<secret>
#   그 후 아래 common_env_vars 대신:
#     env_from=[k8s.V1EnvFromSource(secret_ref=k8s.V1SecretEnvSource(name="wind-power-secret"))]
RUNWAY_API_KEY        = "eyJhbGciOiJIUzUxMiIsInR5cCIgOiAiSldUIiwia2lkIiA6ICJkZjVhOWNhNy00NmEzLTQ4YWUtODk2MS01NGEyYTdmMDgzMDAifQ.eyJpYXQiOjE3NzQxOTk2NzMsImp0aSI6ImMxMDdjYzY2LTdlNzctMGFhMC1iM2I0LTY2Y2ZhOGYzMGM1NCIsImlzcyI6Imh0dHBzOi8va2V5Y2xvYWsudjIubXJ4cnVud2F5LmFpL3JlYWxtcy9ydW53YXkiLCJhdWQiOiJodHRwczovL2tleWNsb2FrLnYyLm1yeHJ1bndheS5haS9yZWFsbXMvcnVud2F5IiwidHlwIjoiT2ZmbGluZSIsImF6cCI6Im1sZmxvdyIsInNpZCI6ImIzYWE4ZjM1LTAzYzUtNGVjOS1hNzI4LWUwNjI3ZjliZWM3OSIsInNjb3BlIjoib3BlbmlkIHdlYi1vcmlnaW5zIG9mZmxpbmVfYWNjZXNzIHNlcnZpY2VfYWNjb3VudCBlbWFpbCBwcm9maWxlIn0.mLzGj4-337W3ImqWZ6_DZVB80iwPXYGZTOU_-Dfsu3vWQr8gCZCH-svqEUa6uqtPqQtFmbKmc4e1FaCqpuihAQ"
AWS_ACCESS_KEY_ID     = "0F9CD3FF-37B-47E064A6E18E37"
AWS_SECRET_ACCESS_KEY = "pPWjNwymzm4B52d3PrnHjR5NPaOnMYY_f2y1c22gNwU"

MLFLOW_TRACKING_URI    = "https://mlflow.v2.mrxrunway.ai"
MLFLOW_S3_ENDPOINT_URL = "https://s3.v2.mrxrunway.ai"


# =============================================================================
# [환경 변수] 모든 ML Pod에 공통으로 주입되는 환경변수
# - task_runner.py가 os.getenv()로 읽어 사용
# - DAG_RUN_ID: Airflow 템플릿 {{ run_id }}로 각 DAG run마다 고유한 S3 prefix 생성
# =============================================================================
common_env_vars = [
    k8s.V1EnvVar(name="RUNWAY_API_KEY",        value=RUNWAY_API_KEY),
    k8s.V1EnvVar(name="AWS_ACCESS_KEY_ID",      value=AWS_ACCESS_KEY_ID),
    k8s.V1EnvVar(name="AWS_SECRET_ACCESS_KEY",  value=AWS_SECRET_ACCESS_KEY),
    k8s.V1EnvVar(name="MLFLOW_TRACKING_URI",    value=MLFLOW_TRACKING_URI),
    k8s.V1EnvVar(name="MLFLOW_S3_ENDPOINT_URL", value=MLFLOW_S3_ENDPOINT_URL),
    k8s.V1EnvVar(name="S3_BUCKET",              value=S3_BUCKET),
    k8s.V1EnvVar(name="DAG_RUN_ID",             value="{{ run_id }}"),
]




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
# [Option A] Kaniko 이미지 빌드 태스크
# - BUILD_MODE = "kaniko" 일 때 DAG 첫 번째 태스크로 실행됨
# - Gitea 저장소를 빌드 컨텍스트로 사용하여 Dockerfile을 빌드 & 푸시
# - 레지스트리 인증: registry-credentials Secret을 /kaniko/.docker/config.json 으로 마운트
# =============================================================================
def make_build_image_task() -> KubernetesPodOperator:
    """Kaniko를 사용하여 Docker 이미지를 빌드하고 레지스트리에 푸시한다."""
    registry_volume = k8s.V1Volume(
        name="docker-config",
        secret=k8s.V1SecretVolumeSource(
            secret_name=REGISTRY_SECRET,
            items=[k8s.V1KeyToPath(key=".dockerconfigjson", path="config.json")],
        ),
    )
    registry_volume_mount = k8s.V1VolumeMount(
        name="docker-config",
        mount_path="/kaniko/.docker",
        read_only=True,
    )

    # Gitea 인증: gitea-registry Secret에서 username/password를 읽어 Kaniko git clone에 주입
    kaniko_env_vars = [
        k8s.V1EnvVar(
            name="GIT_USERNAME",
            value_from=k8s.V1EnvVarSource(
                secret_key_ref=k8s.V1SecretKeySelector(name=GIT_SECRET, key="username")
            ),
        ),
        k8s.V1EnvVar(
            name="GIT_PASSWORD",
            value_from=k8s.V1EnvVarSource(
                secret_key_ref=k8s.V1SecretKeySelector(name=GIT_SECRET, key="password")
            ),
        ),
    ]

    # 빌드 컨텍스트: Gitea git URL
    # Kaniko가 git clone 후 Dockerfile로 빌드 (DOCKERFILE_SUBPATH가 빈 문자열이면 루트 사용)
    git_context = f"{GITEA_REPO_URL}#{GIT_BRANCH}"
    kaniko_args = [
        f"--context={git_context}",
        "--dockerfile=Dockerfile",
        f"--destination={IMAGE}",
        "--cache=false",  # 레이어 캐시 비활성화. 레지스트리가 지원하면 --cache=true 로 변경
    ]
    if DOCKERFILE_SUBPATH:
        kaniko_args.insert(1, f"--context-sub-path={DOCKERFILE_SUBPATH}")

    return KubernetesPodOperator(
        task_id="build_image",
        namespace=NAMESPACE,
        image="gcr.io/kaniko-project/executor:latest",
        image_pull_policy="Always",
        # Kaniko executor는 CMD가 아닌 arguments로 제어
        cmds=[],
        arguments=kaniko_args,
        env_vars=kaniko_env_vars,
        volumes=[registry_volume],
        volume_mounts=[registry_volume_mount],
        container_resources=k8s.V1ResourceRequirements(
            requests={"cpu": "500m", "memory": "1Gi"},
            limits={"cpu": "1",     "memory": "2Gi"},
        ),
        is_delete_operator_pod=True,
        get_logs=True,
        log_events_on_failure=True,
        in_cluster=True,
        startup_timeout_seconds=600,  # 이미지 빌드는 시간이 걸리므로 여유 있게 설정
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
    # [태스크 의존성] BUILD_MODE에 따라 파이프라인 구조가 달라짐
    #
    # Option A (kaniko):
    #   build_image → [load_data, load_model] → train_model → evaluate_model → log_to_mlflow
    #
    # Option B (manual):
    #   [load_data, load_model] → train_model → evaluate_model → log_to_mlflow
    # =========================================================================
    if BUILD_MODE == "kaniko":
        t_build_image = make_build_image_task()
        t_build_image >> [t_load_data, t_load_model]
    else:
        pass  # manual: 이미지가 이미 빌드되어 있다고 가정

    [t_load_data, t_load_model] >> t_train_model >> t_evaluate_model >> t_log_to_mlflow











