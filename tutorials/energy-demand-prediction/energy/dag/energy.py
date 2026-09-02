"""energy.py — Energy Demand Prediction 재학습 Airflow DAG

4-step 파이프라인을 KubernetesPodOperator 로 실행한다:
    load_data → train_model → evaluate_model → log_to_mlflow

환경 종속 값 (RUNWAY_PROJECT_ID / PVC_NAME / ML_IMAGE) 은 OpenBao 의
`secret/data/energy` 에서 받는다. dag-processor Pod 에 Agent Injector 가
활성화되어 있어서 이 파일이 parse 될 때 /vault/secrets/energy.env 가 이미 마운트되어 있다.

각 학습 task Pod 의 시크릿은 출처가 둘로 나뉜다 (v1.4.0 변경):

  - **OpenBao** — Pod annotation 으로 Agent Injector 를 트리거해서 RUNWAY_API_KEY /
    RUNWAY_PROJECT_ID / RUNWAY_BASE_DOMAIN 을 /vault/secrets/energy.env 에 마운트한다.
    파일 형식은 앱 템플릿 폼(`OpenBao secret name`)이 만드는 것과 같은 `key=value` 다.
  - **`s3-rw` Secret** — 프로젝트에 자동 제공되므로 envFrom 으로 붙이기만 하면
    AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY 가 환경변수로 들어온다. OpenBao 에
    S3 키를 등록할 필요가 없다.

task_runner.py 는 config.py 가 위 파일을 직접 읽어 대문자 env 로 올리므로
`source` 가 필요 없다.

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
# [환경 무관] dag-processor 의 Agent Injector 가 마운트한 env 파일을 적재
# -----------------------------------------------------------------------------
# 앱 템플릿 폼의 `OpenBao secret name` 을 쓰면 파일명이 <secretName>.env 이고 내용은
# `key=value`(소문자, export 없음)다. 1.3.x 의 손으로 쓴 템플릿은 creds.env 에
# `export KEY="value"` 였으므로 둘 다 읽는다.
#
# 직접 assignment 인 이유: setdefault 를 쓰면 dag-processor 의 첫 parse 때 값이 캐싱돼
# OpenBao 를 고쳐도 반영되지 않는다. DAG 재parse 마다 최신값을 다시 읽어야 한다.
# =============================================================================
for _path in ("/vault/secrets/energy.env", "/vault/secrets/creds.env"):
    if not os.path.exists(_path):
        continue
    with open(_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#"):
                continue
            if _line.startswith("export "):
                _line = _line[len("export "):]
            _k, _sep, _v = _line.partition("=")
            if _sep:
                os.environ[_k.strip().upper()] = _v.strip().strip('"').strip("'")

# OpenBao 에서 받는 값
RUNWAY_PROJECT_ID = os.environ["RUNWAY_PROJECT_ID"]
PVC_NAME          = os.environ["PVC_NAME"]
ML_IMAGE          = os.environ["ML_IMAGE"]

# Agent Injector 의 Kubernetes auth role. 플랫폼이 "default" 로 고정 제공하므로
# OpenBao 에 openbao_role 키를 등록하지 않는다.
OPENBAO_ROLE = os.environ.get("OPENBAO_ROLE", "default")

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
# [하위 호환] 학습 task Pod 의 셸 prelude
# -----------------------------------------------------------------------------
# Agent Injector 가 만드는 파일은 `key=value`(OpenBao 키 이름 그대로, 소문자)다.
# 1.4.0 이미지의 config.py 는 이 파일을 직접 읽어 대문자 env 로 올리지만,
# 1.3.x 이미지의 config.py 는 os.environ 만 본다. 그 조합에서도 돌아가도록
# 셸에서 대문자 env 를 먼저 채운다. 파일이 없으면 아무 것도 하지 않는다.
#
# 옛 프로젝트가 OpenBao 에 넣어 둔 aws_access_key_id 같은 키도 이 경로로 함께 실린다
# (annotation 템플릿이 `range` 라 등록된 키를 전부 내보내기 때문).
# 옛 형식(`export KEY="value"`)도 접두사와 따옴표를 벗겨 그대로 읽는다.
# =============================================================================
_ENV_PRELUDE = (
    "for f in /vault/secrets/energy.env /vault/secrets/creds.env; do "
    "[ -f \"$f\" ] || continue; "
    # `|| [ -n "$k" ]` — Agent Injector 가 만드는 파일은 **끝에 개행이 없다.**
    # 이 조건이 없으면 while read 가 마지막 한 줄을 통째로 버린다.
    "while IFS='=' read -r k v || [ -n \"$k\" ]; do "
    "k=\"${k#export }\"; [ -n \"$k\" ] || continue; "
    "v=\"${v%\\\"}\"; v=\"${v#\\\"}\"; "
    "export \"$(printf %s \"$k\" | tr 'a-z' 'A-Z')\"=\"$v\"; "
    "done < \"$f\"; "
    "done; "
)

# =============================================================================
# [사설 CA] 플랫폼 루트 인증서를 시스템 번들에 덧붙인다
# -----------------------------------------------------------------------------
# 사설 인증서(Private CA)를 쓰는 환경에서는 S3·MLflow 접속이
# `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate` 로 실패한다.
# ML 이미지는 python:3.11-slim 기반이라 사설 CA 를 신뢰하지 않기 때문이다.
#
# 플랫폼이 프로젝트마다 제공하는 `platform-root-ca` Secret 을 마운트해서
# **시스템 번들 뒤에 이어 붙인다.** 교체가 아니라 추가라서 공인 CA 로 발급된
# 엔드포인트도 그대로 검증된다. Secret 이 없는 환경에서는 파일이 없으므로
# 아무 것도 하지 않는다(볼륨은 optional).
# =============================================================================
_CA_PRELUDE = (
    'if [ -s /runway/ca/ca.crt ]; then '
    'cat /etc/ssl/certs/ca-certificates.crt /runway/ca/ca.crt > /tmp/ca-bundle.crt 2>/dev/null '
    '&& export AWS_CA_BUNDLE=/tmp/ca-bundle.crt '
    'REQUESTS_CA_BUNDLE=/tmp/ca-bundle.crt '
    'SSL_CERT_FILE=/tmp/ca-bundle.crt; '
    'fi; '
)

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
    # 파일명(energy.env)과 형식(key=value)을 **앱 템플릿 폼이 자동 생성하는 것과 동일하게** 맞춘다.
    # 그래야 Code Server(폼으로 배포)와 이 task Pod(수동 스펙)이 같은 계약을 쓴다.
    # AWS_* 는 여기 없다 — 아래 env_from 의 `s3-rw` 가 넣어준다.
    POD_ANNOTATIONS = {
        "vault.hashicorp.com/agent-inject":             "true",
        "vault.hashicorp.com/agent-pre-populate-only":  "true",
        "vault.hashicorp.com/namespace":                OPENBAO_NAMESPACE,
        "vault.hashicorp.com/role":                     OPENBAO_ROLE,
        "vault.hashicorp.com/agent-inject-secret-energy.env":   "secret/data/energy",
        "vault.hashicorp.com/agent-inject-template-energy.env": (
            '{{- with secret "secret/data/energy" -}}\n'
            '{{- range $k, $v := .Data.data }}\n'
            '{{ $k }}={{ $v }}\n'
            '{{- end }}\n'
            '{{- end }}'
        ),
    }

    COMMON_KWARGS = dict(
        namespace=RUNWAY_PROJECT_ID,
        image=ML_IMAGE,
        image_pull_policy="Always",
        image_pull_secrets=[k8s.V1LocalObjectReference(name="gitea-image-pull-secret-runway-bot-token")],
        annotations=POD_ANNOTATIONS,
        # 프로젝트에 자동 제공되는 S3 자격 증명. 이 한 줄로 AWS_ACCESS_KEY_ID /
        # AWS_SECRET_ACCESS_KEY / AWS_ENDPOINT_URL / AWS_REGION / S3_BUCKET 이 들어온다.
        # 쓰기가 필요하므로 -rw. 조회만 하는 워크로드라면 s3-ro 를 쓴다.
        # optional=True — `s3-*` 자동 발급 이전에 만들어진 프로젝트에는 이 Secret 이 없다.
        # optional 이 아니면 Pod 이 CreateContainerConfigError 로 아예 뜨지 못한다.
        # 그런 프로젝트는 OpenBao 에 aws_* 키를 직접 넣어 뒀고, 아래 prelude 가 그것을 실어 나른다.
        env_from=[k8s.V1EnvFromSource(secret_ref=k8s.V1SecretEnvSource(name="s3-rw", optional=True))],
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
            # 사설 CA 환경 대응. optional=True 라 Secret 이 없는 환경에서도 Pod 이 뜬다.
            k8s.V1Volume(
                name="runway-ca",
                secret=k8s.V1SecretVolumeSource(secret_name="platform-root-ca", optional=True),
            ),
        ],
        volume_mounts=[
            k8s.V1VolumeMount(name="data", mount_path="/mnt/data", read_only=False),
            k8s.V1VolumeMount(name="runway-ca", mount_path="/runway/ca", read_only=True),
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
            # 1.4.0 이미지는 config.py 가 파일을 직접 읽으므로 prelude 없이도 된다.
            # 그래도 두는 이유는 **옛 이미지(1.3.x) 호환**이다 — 그쪽 config.py 는 파일을 읽지
            # 않고 os.environ 만 보므로, 셸에서 미리 채워 주지 않으면 KeyError 로 죽는다.
            # 새 이미지에서는 config.py 의 setdefault 가 같은 값을 유지하므로 무해하다.
            arguments=[f"{_CA_PRELUDE}{_ENV_PRELUDE}python /app/task_runner.py --step {step}"],
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
