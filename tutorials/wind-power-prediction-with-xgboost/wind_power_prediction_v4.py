"""
wind_power_prediction_v4.py — KubernetesPodOperator 기반 DAG

이 파일이 무엇인가?
  Airflow DAG 정의 파일이다. Gitea push → .gitea/workflows/sync-dag.yml 이 이
  파일을 airflow-dags 저장소로 복사 → git-sync 를 통해 Airflow 가 인식한다.
  즉, 이 파일 자체는 Airflow 스케줄러 Pod 안에서 실행되고, ML 학습 로직이
  들어있는 게 아니라 "어떤 Pod를 어떤 순서로 띄울지"만 정의한다.

v1(PythonOperator) 대비 핵심 변경:
  - PythonOperator(scheduler 프로세스에서 직접 실행) → KubernetesPodOperator(별도 Pod)
    → 태스크마다 CPU/메모리 격리, 필요한 파이썬 패키지도 독립 이미지에서 관리
  - 태스크 간 데이터 전달: Airflow XCom / 공유 PVC → S3(MinIO)
    → DAG_RUN_ID 별로 prefix 를 격리하여 같은 DAG 의 여러 동시 실행이 충돌하지 않음
    → '{{ run_id }}' 는 Airflow 런타임 템플릿. 실제 run_id 값으로 치환되어 Pod 에 주입됨

실제 ML 로직은 어디에?
  task_runner.py (Docker 이미지 내부). 이 DAG 는 각 태스크마다 같은 이미지를 띄우고
  `python task_runner.py --step <step>` 명령으로 어떤 단계를 실행할지 지정만 한다.

이미지 빌드는 누가?
  .gitea/workflows/build-image.yml 이 task_runner.py, Dockerfile, requirements.txt,
  dataset/** 변경 감지 시 자동으로 빌드 & Gitea Container Registry 에 :latest 태그로 푸시.
  DAG 는 매 실행마다 최신 :latest 를 pull 한다 (image_pull_policy="Always").

파이프라인:
  ensure_pull_secret → [load_data, load_model] → train_model → evaluate_model → log_to_mlflow
  │
  └─ ensure_pull_secret : imagePullSecret(K8s Secret)을 매번 생성/갱신하는 초기화 태스크
                          (Runway 환경에서 이 Secret 이 주기적으로 사라지는 현상 대응)

공통 사전 준비 (README.md 에 상세):
  1. Runway 프로젝트 네임스페이스에 RoleBinding 생성
     (runway-applications:airflow-scheduler SA 에 edit 권한 부여)
  2. OpenBao KV v2 에 시크릿 등록
     namespace=<project-id>, mount=secret, path=wind-power
     { aws_access_key_id, aws_secret_access_key, gitea_username, gitea_password }
  3. 아래 [사용자 설정] 섹션의 상수를 환경에 맞게 수정

⚠️ 보안 주의:
  이 파일에는 RUNWAY_API_KEY 와 OPENBAO_TOKEN 이 평문 하드코딩되어 있다.
  튜토리얼 편의를 위한 선택이며, 다음을 반드시 지킬 것:
    - 이 저장소를 **Private 으로 유지** (public 전환 시 즉시 토큰이 노출됨)
    - fork 하거나 다른 프로젝트로 이식할 때 상수 값을 **반드시 본인 값으로 교체**
    - 프로덕션 환경에서는 Gitea Actions Secrets / OpenBao + K8s Secret (env_from)
      로 완전 분리 권장 (README.md "보안" 참고)
"""

from datetime import timedelta

# ── Airflow SDK / K8s 라이브러리 ────────────────────────────────────────────────
# DAG, task: Airflow 의 기본 DAG 정의와 @task 데코레이터 (Airflow 3.0 SDK)
# KubernetesPodOperator: 각 태스크를 별도 K8s Pod 로 실행해주는 Operator
# k8s.models: Pod spec을 파이썬 객체로 기술할 때 사용 (V1EnvVar, V1Volume 등)
from airflow.sdk import DAG, task
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
from kubernetes.client import models as k8s

# =============================================================================
# [사용자 설정] 환경에 맞게 반드시 수정
# 이 섹션의 값들은 Runway 프로젝트/계정마다 달라진다. 다른 프로젝트로 이식할 때는
# 이 섹션과 task_runner.py 의 EXPERIMENT_NAME / MODEL_NAME 두 곳만 수정하면 된다.
# =============================================================================

# ── K8s 공통 설정 ───────────────────────────────────────────────────────────────
# NAMESPACE : Runway 프로젝트 이름(=K8s namespace, =S3 bucket 이름과 동일). ML 태스크 Pod 가 여기에 생성됨
# IMAGE     : task_runner.py 가 담긴 Docker 이미지. Gitea Actions 가 빌드/푸시. 매 실행마다 최신 :latest pull
# S3_BUCKET : 태스크 간 중간 파일 공유용 MinIO bucket. Runway 프로젝트 이름과 동일 (자동 프로비저닝됨)
# IMAGE_PULL_SECRET : Gitea Container Registry 에서 이미지 pull 시 사용할 K8s Secret 이름
#                    ensure_pull_secret 태스크가 매 DAG 실행 전에 자동으로 생성/갱신한다
NAMESPACE          = "rwyt-energy-forecasting"
IMAGE              = "gitea.v2.mrxrunway.ai/rwyt-energy-forecasting/wind-power-prediction:latest"
S3_BUCKET          = "rwyt-energy-forecasting"
IMAGE_PULL_SECRET  = "gitea-registry-pull"

# ── Runway / MLflow 크레덴셜 ───────────────────────────────────────────────────
# RUNWAY_API_KEY 는 Keycloak offline token 이다. 발급: Runway UI > 사용자 설정 > API 토큰.
# 두 곳에서 사용됨:
#   1) MLflow 인증 — MLFLOW_TRACKING_TOKEN 으로 바로 사용 (MLflow 서버가 offline token 을 직접 검증)
#   2) ──────────── (이전에는 OpenBao JWT 로그인에도 사용했으나 현재는 OPENBAO_TOKEN 로 분리됨)
# 주의: 같은 사용자가 새로 로그인해 offline token 을 재발급하면 이전 토큰은 **세션 무효화**된다.
#       "Failed to validate offline token" 에러가 나면 UI 에서 새 토큰 발급 후 이 값 교체.
# AWS 키는 여기에 없다 → OpenBao 로 분리됨 (아래 참조)
RUNWAY_API_KEY        = "eyJhbGciOiJIUzUxMiIsInR5cCIgOiAiSldUIiwia2lkIiA6ICJkZjVhOWNhNy00NmEzLTQ4YWUtODk2MS01NGEyYTdmMDgzMDAifQ.eyJpYXQiOjE3NzY0MDM1ODAsImp0aSI6ImI3OTlkNmI1LTdjZWUtZWQ2MS05MGI1LWEzNDViZGE2Yzk3OCIsImlzcyI6Imh0dHBzOi8va2V5Y2xvYWsudjIubXJ4cnVud2F5LmFpL3JlYWxtcy9ydW53YXkiLCJhdWQiOiJodHRwczovL2tleWNsb2FrLnYyLm1yeHJ1bndheS5haS9yZWFsbXMvcnVud2F5Iiwic3ViIjoiMGY5Y2QzZmYtMzdiYS00NWNlLWE3ZDItMzIzYTMyYmExNmU1IiwidHlwIjoiT2ZmbGluZSIsImF6cCI6InJ1bndheSIsInNpZCI6IjgxYTBjYTAwLTBhMDMtNGYxNi05M2NhLWRkMjc2YmUwYTgyYiIsInNjb3BlIjoib3BlbmlkIHdlYi1vcmlnaW5zIG9mZmxpbmVfYWNjZXNzIHNlcnZpY2VfYWNjb3VudCBlbWFpbCBwcm9maWxlIn0.XNT9kvg3PTHPq1vrPSEUqOnJehZ-HtpSlWo8Lzyxiv7qWZ6MdLNHw_5W0QIRIczH1kiSLkWeLfnHXAK9-BvoPQ"

MLFLOW_TRACKING_URI    = "https://mlflow.v2.mrxrunway.ai"
MLFLOW_S3_ENDPOINT_URL = "https://s3.v2.mrxrunway.ai"

# ── OpenBao 설정 ────────────────────────────────────────────────────────────────
# OpenBao (Vault 호환) 는 Runway 가 제공하는 시크릿 저장소다. AWS 키와 Gitea 자격증명을
# 여기에 저장해두고 런타임에 조회한다. 코드에 민감한 값을 직접 커밋하지 않기 위함.
#
# OPENBAO_TOKEN 은 KV 조회용 서비스 토큰. 발급 방법:
#   OpenBao 콘솔 → 우측 상단에서 namespace path 로 로그인 → 자동으로 발급되는 토큰을 복사
#   (만료되면 재로그인해서 새 토큰으로 갱신 필요)
#
# 사전 등록 필요한 시크릿 (OpenBao 콘솔 > <OPENBAO_NAMESPACE> > Secret Engines > KV v2(secret/) > wind-power):
#   aws_access_key_id        (task_runner / download_model 이 S3 접근 시 사용)
#   aws_secret_access_key
#   gitea_username           (ensure_pull_secret 이 dockerconfigjson 구성 시 사용)
#   gitea_password
OPENBAO_URL         = "https://openbao.v2.mrxrunway.ai"
OPENBAO_NAMESPACE   = "rwyt-energy-forecasting"       # Runway 프로젝트 이름과 동일 (소문자)
OPENBAO_TOKEN       = "s.F6DrHBKlEENqMQvAAoBKjpJ8.detel9"
OPENBAO_SECRET_PATH = "wind-power"                    # namespace 내부 상대 경로 (prefix 중복 불필요)
OPENBAO_KV_MOUNT    = "secret"                        # KV v2 엔진 mount path (기본 "secret")
OPENBAO_VERIFY_TLS  = "true"                          # 공식 CA 인증서 환경 기준. 자체 서명이면 "false"


# =============================================================================
# [환경 변수] 모든 ML Pod 에 공통으로 주입되는 환경변수
#
# KubernetesPodOperator 의 env_vars 파라미터로 Pod 시작 시 주입된다.
# task_runner.py 안에서 os.getenv("RUNWAY_API_KEY") 등으로 읽어 사용한다.
#
# DAG_RUN_ID : Airflow 템플릿 '{{ run_id }}' 는 DAG 실행 시점에 실제 run_id (예:
#              'manual__2026-04-21T12:00:00+00:00') 로 치환된다. 이걸 Pod env 로
#              넘겨 task_runner 가 S3 prefix 를 run 별로 격리하는 데 사용.
# AWS 키     : 여기 없음. task_runner 안에서 OpenBao 를 직접 조회해서 가져온다.
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
    k8s.V1EnvVar(name="OPENBAO_VERIFY_TLS",     value=OPENBAO_VERIFY_TLS),
]


# =============================================================================
# [초기화 태스크] imagePullSecret 자동 생성
#
# 왜 필요한가?
#   KubernetesPodOperator 가 Gitea Container Registry 에서 이미지를 pull 하려면
#   namespace 에 dockerconfigjson 타입 K8s Secret (gitea-registry-pull) 이 있어야
#   한다. 그런데 Runway 환경에서는 이 Secret 이 주기적으로 사라지는 현상이 있어
#   매 DAG 실행 전에 재생성하도록 이 태스크를 첫 단계로 추가했다.
#
# 흐름:
#   OpenBao (gitea_username, gitea_password)
#     → dockerconfigjson 구성 (base64 인코딩 필요, K8s spec 준수)
#     → K8s API 로 Secret create-or-update
#
# 왜 @task (PythonOperator) 인가? 다른 태스크처럼 KubernetesPodOperator 가 아닌 이유?
#   Pod 를 띄울 때 imagePullSecret 이 필요한데, 그 Secret 을 만드는 작업 자체는
#   Secret 이 없어도 돌아야 한다 → Airflow 스케줄러 Pod 내부(@task)에서 실행해야 함.
#   스케줄러 SA 에는 사전에 edit RoleBinding 이 되어 있어 K8s API 호출이 가능하다.
# =============================================================================
@task
def ensure_pull_secret() -> None:
    """OpenBao 에서 Gitea 자격증명을 읽어와 imagePullSecret 을 생성/갱신한다."""
    # import 를 함수 안에 두는 이유:
    #   Airflow 스케줄러가 DAG 파일을 매번 파싱할 때 불필요한 import 비용을 줄이기 위함.
    #   이 함수는 실제로 태스크가 실행될 때만 호출되므로 지연 import 가 효율적.
    import base64
    import json
    import ssl
    import urllib.request

    from kubernetes import client, config

    # ─────────────────────────────────────────────────────────────────────
    # 1. OpenBao 에서 Gitea 자격증명 조회
    #    hvac 라이브러리 대신 urllib 로 직접 HTTP 호출 (스케줄러 이미지에 hvac 없을
    #    수 있어 외부 의존 최소화). KV v2 의 조회 경로는 mount/data/path.
    # ─────────────────────────────────────────────────────────────────────
    vault_url = f"{OPENBAO_URL}/v1/{OPENBAO_KV_MOUNT}/data/{OPENBAO_SECRET_PATH}"
    req = urllib.request.Request(
        vault_url,
        headers={
            "X-Vault-Token": OPENBAO_TOKEN,              # 서비스 토큰
            "X-Vault-Namespace": OPENBAO_NAMESPACE,      # multi-tenant 시 namespace 지정
        },
    )
    # SSL 검증 정책 — task_runner.py 의 hvac verify 와 동일한 OPENBAO_VERIFY_TLS env 를 공유.
    # 기본값 "true" (공식 CA 서명 환경). 자체 서명 환경에선 DAG 상수에서 "false" 로 변경.
    import os
    verify_tls = os.getenv("OPENBAO_VERIFY_TLS", "true").lower() == "true"
    ctx = ssl.create_default_context()
    if not verify_tls:
        # 자체 서명 인증서 환경 — 검증을 끔 (MITM 방어 없음, 내부 폐쇄망 전제)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, context=ctx) as r:
        body = json.loads(r.read())
    # KV v2 응답 구조: { "data": { "data": { <실제 키-값> }, "metadata": {...} } }
    secrets = body["data"]["data"]
    gitea_username = secrets["gitea_username"]
    gitea_password = secrets["gitea_password"]
    print("[ensure_pull_secret] OpenBao 에서 Gitea 자격증명 조회 완료")

    # ─────────────────────────────────────────────────────────────────────
    # 2. dockerconfigjson 구성
    #    K8s imagePullSecrets 은 이 특정 JSON 구조를 base64 로 인코딩한 형태만 인식.
    #    Docker CLI 가 생성하는 ~/.docker/config.json 과 같은 포맷.
    #    auth 필드는 "username:password" 를 base64 로 인코딩한 값.
    # ─────────────────────────────────────────────────────────────────────
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

    # ─────────────────────────────────────────────────────────────────────
    # 3. K8s Secret create_or_update
    #    load_incluster_config() : Pod 내부에서 자동으로 in-cluster SA 토큰을 읽어와
    #                              K8s API Server 에 인증한다. 별도 설정 불필요.
    #    전략: read 시도 → 존재하면 patch(갱신), 없으면 create
    # ─────────────────────────────────────────────────────────────────────
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
# [헬퍼 함수] KubernetesPodOperator 공통 설정 팩토리
#
# 각 ML 태스크마다 비슷한 Pod 설정을 반복해서 쓰지 않도록 공통 부분을 묶는다.
# 태스크마다 다른 부분:
#   - task_id  : Airflow UI 에 표시되는 이름
#   - step     : task_runner.py 의 --step 인자 (load_data / load_model / train_model / ...)
#   - 리소스   : 학습 태스크는 크게, 나머지는 작게
#
# 주요 KubernetesPodOperator 파라미터:
#   image_pull_policy="Always"  : 매 실행마다 :latest 를 새로 pull (개발용). 안정화 후엔
#                                  IfNotPresent 로 바꿔 불필요한 pull 을 줄일 수 있음.
#   cmds                         : Pod 컨테이너의 entrypoint 명령. Dockerfile 의 CMD 무시.
#   is_delete_operator_pod=True  : 성공 실패와 무관하게 Pod 자동 삭제 (로그는 Airflow 가 이미 수집)
#   get_logs=True                : Pod stdout/stderr 를 Airflow 태스크 로그에 스트리밍
#   log_events_on_failure=True   : 실패 시 K8s 이벤트(스케줄링 실패, ImagePullBackOff 등) 기록
#   in_cluster=True              : Airflow 스케줄러가 현재 있는 클러스터의 API Server 에 접근
#                                  (별도 kubeconfig 불필요, in-cluster SA 토큰 사용)
# =============================================================================
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
        image_pull_policy="Always",
        # `python task_runner.py --step <step>` 으로 실행되어 해당 단계의 함수가 호출됨
        cmds=["python", "task_runner.py", "--step", step],
        env_vars=common_env_vars,
        # CPU 는 milli-core 단위 (1000m = 1 core), 메모리는 Mi/Gi 단위
        # requests = 스케줄링 기준치(최소 보장), limits = 최대 사용량
        container_resources=k8s.V1ResourceRequirements(
            requests={"cpu": cpu_request, "memory": memory_request},
            limits={"cpu": cpu_limit,    "memory": memory_limit},
        ),
        image_pull_secrets=pull_secrets,
        is_delete_operator_pod=True,
        get_logs=True,
        log_events_on_failure=True,
        in_cluster=True,
        startup_timeout_seconds=300,
    )


# =============================================================================
# [DAG 정의]
#
# default_args : 모든 태스크에 공통으로 적용되는 기본값
#   - owner           : Airflow UI 에 표시되는 DAG 소유자. Runway 에서는 사용자
#                       이메일로 설정해두면 owner 기반 권한/표시에 활용 가능
#   - depends_on_past : True 면 이전 run 의 같은 태스크가 성공해야 이번 run 실행
#                       본 DAG 는 매 run 이 독립적이므로 False
#   - retries         : 실패 시 자동 재시도 횟수
#   - retry_delay     : 재시도 간 대기 시간
#
# with DAG(...) as dag :
#   - dag_id          : 고유 식별자. airflow-dags 저장소에 같은 ID 가 있으면 충돌/덮어쓰기
#   - schedule=None   : 스케줄 없음 = 수동 trigger 전용 (Airflow UI 에서 Play 버튼)
#                       정기 실행은 "0 0 * * *" 같은 cron 표현식 사용
#   - catchup=False   : 과거 미실행 구간을 소급 실행하지 않음 (수동 trigger 에서는 의미 없음)
#   - tags            : Airflow UI 필터링용 라벨
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
    schedule=None,
    catchup=False,
    tags=["ml", "xgboost", "wind-power", "kubernetes"],
) as dag:

    # =========================================================================
    # Task 1: 데이터셋 로드 — Docker 이미지 내 번들된 CSV → S3 업로드
    # 가벼운 IO 작업이라 리소스 소량 할당
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
    # Task 2: 모델 초기화 — XGBRegressor 객체 생성 + pickle 로 S3 업로드
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
    # Task 3: 모델 학습 — S3 에서 데이터/초기모델 받아 XGBoost fit 실행
    # 파이프라인에서 가장 CPU/메모리 사용량이 큰 단계라 리소스 크게 할당
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
    # Task 4: 모델 평가 — RMSE, MAE, R² 계산 후 metrics.json 업로드
    # 중간 규모의 예측/수치 계산이라 중간 리소스
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
    # Task 5: MLflow 로깅 — 하이퍼파라미터/메트릭/모델 아티팩트를 MLflow 서버로
    # log_model 이 S3 에 직접 업로드하므로 메모리는 다소 여유 있게
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
    # [태스크 의존성] Airflow 의 bitshift 연산자 (>>) 로 선후 관계 선언
    #
    # ensure_pull_secret
    #    │
    #    ├──► load_data ─┐
    #    │               │
    #    └──► load_model ┴──► train_model ──► evaluate_model ──► log_to_mlflow
    #
    # [a, b] >> c  : a, b 둘 다 성공해야 c 시작 (fan-in)
    # a >> [b, c]  : a 성공 후 b, c 가 병렬 실행 (fan-out)
    # =========================================================================
    t_ensure_pull_secret = ensure_pull_secret()
    t_ensure_pull_secret >> [t_load_data, t_load_model]
    [t_load_data, t_load_model] >> t_train_model >> t_evaluate_model >> t_log_to_mlflow











