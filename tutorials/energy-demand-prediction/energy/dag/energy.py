"""energy.py — Energy Demand Prediction 재학습 Airflow DAG

4-step 파이프라인을 KubernetesPodOperator 로 실행한다:
    load_data → train_model → evaluate_model → log_to_mlflow

환경 종속 값 (RUNWAY_PROJECT_ID / PVC_NAME / ML_IMAGE) 은 OpenBao 의
`secret/data/energy` 에서 받는다. dag-processor Pod 에 Agent Injector 가
활성화되어 있어서 (Step 4-2 의 values override 참고) 이 파일이 parse 될 때
/vault/secrets/creds.env 가 이미 마운트되어 있다.

각 학습 task Pod 은 Pod annotation 으로 자체 Agent Injector 를 트리거 →
sidecar 가 시크릿 5개 (RUNWAY_API_KEY / RUNWAY_PROJECT_ID / RUNWAY_BASE_DOMAIN /
AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY) 를 /vault/secrets/creds.env 에 마운트한다.

학습 데이터는 PVC `/mnt/data/dataset/pred-demo-dataset/` 안의 모든 *.csv. 사용자가
새 분기 데이터를 PVC 에 추가 업로드한 후 DAG 를 트리거하면 다음 run 에 자동 포함된다.
"""

import os
from datetime import datetime

from airflow import DAG
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
from airflow.providers.cncf.kubernetes.utils import pod_manager as _pm
from kubernetes.client import models as k8s


# =============================================================================
# [환경 무관] PodManager.await_pod_completion 의 polling 주기 1초 → 10초 (best-effort)
# -----------------------------------------------------------------------------
# Airflow 3.x KPO 는 Pod 가 Running 된 후 1초마다 "Pod ... has phase Running"
# 을 task log 에 도배한다. KPO 자체에는 이 값을 노출하는 인자가 없음.
# pod_manager 버전마다 polling 인자명이 다르므로 (polling_time / polling_period_seconds /
# poll_interval) 시그니처에서 자동 탐지. 인자 자체가 없는 버전이면 no-op (1초 유지).
# =============================================================================
import inspect as _inspect

_POD_PHASE_POLL_SECONDS = 10
_orig_await_pod_completion = _pm.PodManager.await_pod_completion
_poll_param_name = next(
    (n for n in ("polling_time", "polling_period_seconds", "poll_interval", "interval")
     if n in _inspect.signature(_orig_await_pod_completion).parameters),
    None,
)
if _poll_param_name:
    def _await_pod_completion_slow(self, *args, **kwargs):
        if _poll_param_name not in kwargs:
            kwargs[_poll_param_name] = _POD_PHASE_POLL_SECONDS
        return _orig_await_pod_completion(self, *args, **kwargs)
    _pm.PodManager.await_pod_completion = _await_pod_completion_slow


class _KPO(KubernetesPodOperator):
    """KubernetesPodOperator subclass.

    Airflow 3.x 의 KPO 는 `annotations` 필드를 Jinja2 template 으로 렌더링하는데,
    본 튜토리얼은 그 값에 OpenBao Agent Injector 의 template syntax
    (`{{- with secret ... -}}`) 를 박는다. Jinja2 가 그 OpenBao syntax 를 자기 것으로
    오해해서 TemplateSyntaxError 가 나므로, `annotations` 만 template_fields 에서 제외한다.
    """

    template_fields = tuple(
        f for f in KubernetesPodOperator.template_fields if f != "annotations"
    )

# =============================================================================
# [환경 무관] dag-processor 의 Agent Injector 가 마운트한 creds.env 를 적재
# =============================================================================
_CREDS_PATH = "/vault/secrets/creds.env"
if os.path.exists(_CREDS_PATH):
    with open(_CREDS_PATH) as _f:
        for _line in _f:
            if _line.startswith("export "):
                _k, _, _v = _line[len("export "):].strip().partition("=")
                # 직접 assignment — DAG 재parse 시마다 vault-agent 가 갱신한 creds.env 의 최신값 반영.
                # setdefault 를 쓰면 dag-processor 의 첫 parse 때 캐싱돼 OpenBao 변경이 안 보임.
                os.environ[_k] = _v.strip().strip('"').strip("'")

# OpenBao 에서 받는 값
RUNWAY_PROJECT_ID = os.environ["RUNWAY_PROJECT_ID"]
PVC_NAME          = os.environ["PVC_NAME"]
ML_IMAGE          = os.environ["ML_IMAGE"]
OPENBAO_ROLE      = os.environ["OPENBAO_ROLE"]

# OpenBao namespace 는 본 튜토리얼 약속에 의해 K8s namespace 와 같음
OPENBAO_NAMESPACE = RUNWAY_PROJECT_ID

# =============================================================================
# [학습 모드] CPU (기본) / GPU (HAMi vGPU) 토글
# -----------------------------------------------------------------------------
# True 로 바꿔서 push 하면 train_model task 가 HAMi vGPU 4GB 요청 + XGBoost device='cuda'.
# 클러스터의 GPU 자원이 충분할 때만 사용. CardComputeUnitsExhausted 가 자주 뜨면 False 유지.
# =============================================================================
USE_GPU = False

# =============================================================================
# [DAG 정의]
# =============================================================================
DAG_ID = f"energy_demand_prediction_{RUNWAY_PROJECT_ID}"

with DAG(
    dag_id=DAG_ID,
    description="Energy demand 4-step training pipeline",
    schedule=None,                          # 수동 트리거만 (GUI 또는 Airflow UI)
    start_date=datetime(2024, 1, 1),
    catchup=False,
    default_args={"owner": "energy-team", "retries": 0},
    tags=["energy"],
) as dag:

    # ── task Pod 공통 설정 ──
    # `agent-pre-populate-only: true` — init container 만 시크릿 fetch 하고 sidecar 안 띄움.
    # Batch task (10~30분) 라 token 갱신 불필요. sidecar 가 있으면 base 종료 후에도
    # Pod 가 "complete" 상태로 못 가서 KPO 가 task 종료 인식 실패 → 무한 hang.
    POD_ANNOTATIONS = {
        "vault.hashicorp.com/agent-inject":             "true",
        "vault.hashicorp.com/agent-pre-populate-only":  "true",
        "vault.hashicorp.com/namespace":                OPENBAO_NAMESPACE,
        "vault.hashicorp.com/role":                     OPENBAO_ROLE,
        "vault.hashicorp.com/agent-inject-secret-creds.env":   "secret/data/energy",
        "vault.hashicorp.com/agent-inject-template-creds.env": (
            '{{- with secret "secret/data/energy" -}}\n'
            'export RUNWAY_API_KEY="{{ .Data.data.runway_api_key }}"\n'
            'export RUNWAY_PROJECT_ID="{{ .Data.data.runway_project_id }}"\n'
            'export RUNWAY_BASE_DOMAIN="{{ .Data.data.runway_base_domain }}"\n'
            'export AWS_ACCESS_KEY_ID="{{ .Data.data.aws_access_key_id }}"\n'
            'export AWS_SECRET_ACCESS_KEY="{{ .Data.data.aws_secret_access_key }}"\n'
            '{{- end }}'
        ),
    }

    COMMON_KWARGS = dict(
        namespace=RUNWAY_PROJECT_ID,
        image=ML_IMAGE,
        image_pull_policy="Always",
        image_pull_secrets=[k8s.V1LocalObjectReference(name="gitea-image-pull-secret-runway-bot-token")],
        annotations=POD_ANNOTATIONS,
        env_vars=[
            k8s.V1EnvVar(name="DAG_RUN_ID", value="{{ run_id }}"),
            k8s.V1EnvVar(name="USE_GPU",    value=str(USE_GPU).lower()),
            # K8s downward API — Pod 의 cpu limit 을 정수로 받음 (반올림 up).
            # `sched_getaffinity` 나 `loky.cpu_count()` 는 노드 전체 코어 수를 반환할 수 있어서
            # CFS quota 기반 K8s limit 을 못 읽음. downward API 가 유일한 신뢰 경로.
            k8s.V1EnvVar(
                name="POD_CPU_LIMIT",
                value_from=k8s.V1EnvVarSource(
                    resource_field_ref=k8s.V1ResourceFieldSelector(
                        container_name="base",
                        resource="limits.cpu",
                    )
                ),
            ),
        ],
        volumes=[
            k8s.V1Volume(
                name="data",
                persistent_volume_claim=k8s.V1PersistentVolumeClaimVolumeSource(claim_name=PVC_NAME),
            ),
        ],
        volume_mounts=[
            k8s.V1VolumeMount(name="data", mount_path="/mnt/data", read_only=False),
        ],
        cmds=["/bin/bash", "-c"],
        get_logs=True,
        is_delete_operator_pod=True,
    )

    def make_task(step: str, mem_req: str, mem_lim: str, cpu_lim: str = "4", gpu_mem_mb: int | None = None):
        """KPO task 생성. gpu_mem_mb 가 주어지면 HAMi vGPU resource 추가."""
        requests = {"cpu": "1", "memory": mem_req}
        limits   = {"cpu": cpu_lim, "memory": mem_lim}
        if gpu_mem_mb is not None:
            # HAMi: 단일 물리 GPU 를 여러 Pod 가 메모리/연산을 가상화해서 공유.
            # - nvidia.com/gpu=1     → vGPU 1개 할당
            # - nvidia.com/gpumem=N  → 해당 vGPU 의 VRAM 한도 (MB 단위)
            # (선택) nvidia.com/gpucores=N → compute 비율 (%) 제한
            requests["nvidia.com/gpu"]    = "1"
            requests["nvidia.com/gpumem"] = str(gpu_mem_mb)
            limits["nvidia.com/gpu"]      = "1"
            limits["nvidia.com/gpumem"]   = str(gpu_mem_mb)
        return _KPO(
            task_id=step,
            name=f"energy-{step.replace('_', '-')}",
            arguments=[
                f"source /vault/secrets/creds.env && "
                f"python /app/task_runner.py --step {step}"
            ],
            container_resources=k8s.V1ResourceRequirements(
                requests=requests, limits=limits,
            ),
            **COMMON_KWARGS,
        )

    t_load     = make_task("load_data",      "1Gi", "2Gi")
    # train_model 만 CPU 8 까지 burst — 72 타겟을 joblib n_jobs=-1 로 병렬 학습할 때 코어 수만큼 빨라짐.
    # 클러스터에 여유 CPU 가 부족하면 (Pod Pending 또는 노드 압박) cpu_lim 을 "4" 또는 그 이하로 줄임.
    # USE_GPU=True 면 train_model 에 HAMi vGPU 4GB 추가. False (기본) 면 CPU 학습.
    t_train    = make_task("train_model",    "2Gi", "4Gi", cpu_lim="8", gpu_mem_mb=4000 if USE_GPU else None)
    t_evaluate = make_task("evaluate_model", "2Gi", "4Gi")
    # log_to_mlflow: MLflow 가 72개 XGBoost regressor 의 MultiOutputRegressor 를 cloudpickle 직렬화 +
    # S3 업로드 시 메모리 피크가 커서 2Gi 는 OOMKilled. 4Gi 로 여유 확보.
    t_log      = make_task("log_to_mlflow",  "2Gi", "4Gi")
    # copy_model_to_pvc: MLflow S3 backend → PVC `/mnt/data/m-<id>/` 자동 복사. 사용자가 Code Server
    # 에서 `download_model.py` 를 수동으로 돌리지 않아도 됨. 재학습 시 PVC 자동 갱신.
    t_copy     = make_task("copy_model_to_pvc", "1Gi", "2Gi")

    t_load >> t_train >> t_evaluate >> t_log >> t_copy
