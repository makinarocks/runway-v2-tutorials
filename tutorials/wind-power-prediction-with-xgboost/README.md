# Wind Power Prediction with XGBoost

Runway v2에서 **KubernetesPodOperator 기반 Airflow DAG**로 풍력 터빈 발전량(`activepower`)을 예측하는 XGBoost 모델의 학습 → 평가 → MLflow 등록 → PVC 배포 파이프라인을 구성하는 튜토리얼입니다.

> **처음 시도하시나요?** 🚀 Runway 콘솔 UI 를 조작하며 볼륨 생성 → IDE 배포 → 모델 추론 호출까지 따라할 수 있는 단계별 가이드가 있습니다: **[WALKTHROUGH.md](./WALKTHROUGH.md)**. 본 README 는 구조/참조 문서이고, WALKTHROUGH 는 실행 가이드입니다.

## 개요

| 항목 | 내용 |
|---|---|
| **목적** | Runway v2에서 KubernetesPodOperator + OpenBao + Gitea Actions 기반 ML 파이프라인 end-to-end 구성 학습 |
| **난이도** | Intermediate |
| **주요 스택** | XGBoost, Airflow (KubernetesPodOperator), MLflow, OpenBao, Gitea Actions, MinIO(S3), Kubernetes |

### 핵심 설계

- **KubernetesPodOperator**: 각 태스크가 독립 K8s Pod로 실행 (리소스 격리 / 독립 패키지 환경)
- **S3 아티팩트 공유**: DAG_RUN_ID 별 prefix로 태스크 간 중간 파일 주고받기 (XCom/PVC 없음)
- **OpenBao 시크릿 관리**: AWS 키·Gitea 자격증명을 OpenBao KV에 저장, 런타임 조회
- **ensure_pull_secret**: 매 DAG 실행 전 `gitea-registry-pull` K8s Secret 자동 재생성
- **Gitea Actions CI/CD**: `task_runner.py` / `Dockerfile` / `requirements.txt` / `dataset/**` 변경 → 자동 이미지 빌드, DAG 파일 변경 → airflow-dags 저장소에 자동 sync

### 파이프라인

```
ensure_pull_secret ─┐
                    ├→ load_data ─┐
                    │             ├→ train_model → evaluate_model → log_to_mlflow
                    └→ load_model ┘
```

---

## 디렉토리 구성

```
wind-power-prediction-with-xgboost/
├── README.md                      # 본 가이드
├── Dockerfile                     # task_runner.py 용 이미지
├── requirements.txt               # Python 의존성
├── wind_power_prediction_v4.py    # KubernetesPodOperator 기반 DAG
├── task_runner.py                 # Docker 이미지 내 태스크 실행 로직
├── config.py                      # 전역 설정/파생값 중앙 모듈 (task_runner/IDE 공용)
├── .env.example                   # 사용자 편집용 환경 템플릿 → cp 하여 .env 생성
├── download_model.py              # S3 → PVC 모델 아티팩트 복사 (IDE 실행용)
├── test_inference.py              # 배포된 모델 추론 엔드포인트 호출 테스트 (IDE 실행용)
├── run_dag.sh                     # Airflow REST API로 DAG trigger
├── .gitea/workflows/
│   ├── build-image.yml            # task_runner 변경 시 이미지 자동 빌드
│   └── sync-dag.yml               # DAG 파일 변경 시 airflow-dags 저장소로 sync
└── dataset/
    └── turbine_data.csv           # 풍력 터빈 센서 데이터 (~10,000행)
```

---

## 사전 준비

### 1. Runway 프로젝트 네임스페이스 준비

프로젝트를 생성하면 같은 이름의 K8s namespace와 S3 bucket이 자동 생성됩니다. 본 가이드에서는 `<your-project-id>` 를 본인 프로젝트 ID 로 치환해 읽으세요.

### 2. Gitea 저장소 준비

프로젝트 하위에 두 개의 저장소를 생성:

- `<your-project-id>/wind-power-prediction` — 본 튜토리얼 소스
- `<your-project-id>/airflow-dags` — Airflow git-sync 대상 (DAG 저장)

`wind-power-prediction` 저장소 **Settings → Secrets and Variables → Actions**에 아래 3개 등록:

| 이름 | 값 |
|---|---|
| `GIT_USERNAME` | Gitea 로그인 사용자명 |
| `GIT_TOKEN` | Gitea 접근 토큰 (패키지 write + airflow-dags write 권한 포함) |
| `IMAGE_TAG` | `gitea.<your-runway-domain>/<your-project-id>/wind-power-prediction:latest` |

### 3. Airflow scheduler SA에 RoleBinding 생성

KubernetesPodOperator가 프로젝트 namespace에 Pod를 생성할 수 있도록 권한 부여:

```bash
kubectl create rolebinding airflow-scheduler-pod-runner \
  --clusterrole=edit \
  --serviceaccount=runway-applications:airflow-scheduler \
  -n <your-project-id>
```

> `edit` ClusterRole은 Pod/Secret 관리 권한을 포함하여 `ensure_pull_secret` 태스크의 Secret create/patch 에도 필요합니다.

### 4. OpenBao 시크릿 등록

OpenBao 콘솔에서 프로젝트 namespace(`<your-project-id>`)로 로그인 후:

1. **Secret Engines → Enable new engine → KV (v2)**
   - Path: `secret`

2. **Create secret**
   - Path: `wind-power`
   - Data (5개 키):

     | Key | Value |
     |---|---|
     | `aws_access_key_id` | Runway에서 발급받은 S3 Access Key ID |
     | `aws_secret_access_key` | 위 Access Key의 Secret |
     | `gitea_username` | `GIT_USERNAME`과 동일 |
     | `gitea_password` | `GIT_TOKEN`과 동일 |
     | `runway_api_key` | Runway UI > 사용자 설정 > API 토큰 (Keycloak offline token) |

3. **서비스 토큰 복사**
   - 상단 우측 프로필 → **Copy token** — 이 값을 `OPENBAO_TOKEN`에 입력 (만료되면 재발급)

### 5. 사용자 설정

튜토리얼 사용자가 수정할 값은 **3곳** 뿐입니다 (`RUNWAY_PROJECT_ID`, `RUNWAY_BASE_DOMAIN`, `OPENBAO_TOKEN`). 나머지 (NAMESPACE/IMAGE/EXPERIMENT_NAME/모든 서비스 URL 등) 는 이 3개 값에서 자동 파생됩니다 (`config.py` + DAG 상단 규약).

#### 5-1. IDE 스크립트용 `.env`

저장소 루트에서:

```bash
cp .env.example .env
# 편집기로 .env 열어서 아래 3값 설정
#   RUNWAY_PROJECT_ID=<본인 프로젝트 ID>
#   RUNWAY_BASE_DOMAIN=<Runway 베이스 도메인 — 예: runway.example.com>
#   OPENBAO_TOKEN=<위 4단계에서 복사한 서비스 토큰>
```

`download_model.py` / `test_inference.py` 는 `config.py` 를 통해 이 `.env` 를 자동 로드합니다.

#### 5-2. DAG 파일 상수 (Airflow 가 파싱)

`wind_power_prediction_v4.py` 상단 [사용자 설정] 섹션의 **3줄만** 수정:

```python
RUNWAY_PROJECT_ID  = "your-project-id"              # 본인 프로젝트 ID
RUNWAY_BASE_DOMAIN = "your-runway-domain.com"       # Runway 베이스 도메인
OPENBAO_TOKEN      = "s.<your-openbao-token>"       # OpenBao 서비스 토큰
```

나머지 (`NAMESPACE`, `IMAGE`, `OPENBAO_URL` 등) 는 위 3개 값에서 f-string 으로 파생되어 자동 설정됩니다. `RUNWAY_API_KEY` 는 **더 이상 코드에 없습니다** — task_runner 가 OpenBao 의 `runway_api_key` 를 런타임에 조회합니다.

---

## 배포 & 실행

### 초기 배포

1. `wind_power_prediction_v4.py` 의 상수 수정 후 Gitea `wind-power-prediction` 저장소에 push
2. Gitea Actions 동작 확인:
   - **Build and Push to Gitea CR** — 이미지 빌드 & CR 푸시
   - **Sync DAG to airflow-dags** — `airflow-dags/wind_power_prediction/v4/wind_power_prediction.py` 생성
3. Airflow UI에서 `wind_power_prediction_v4` DAG 확인 후 trigger

### 이후 업데이트

두 Gitea Actions workflow 는 **서로 독립**적으로 동작합니다 (어느 한쪽이 다른 쪽을 트리거하지 않음). 변경 파일의 성격에 따라 해당 경로만 갱신됩니다:

| 변경 파일 | 자동 동작 | 반영 경로 |
|---|---|---|
| `task_runner.py`, `config.py`, `Dockerfile`, `requirements.txt`, `dataset/**` | 이미지 재빌드 (`build-image.yml`) | Gitea CR `:latest` 태그 갱신 → 다음 DAG 실행 시 새 이미지 pull (`image_pull_policy="Always"`) |
| `wind_power_prediction_v4.py` | DAG 파일 동기화 (`sync-dag.yml`) | `airflow-dags/wind_power_prediction/v4/wind_power_prediction.py` 업데이트 → Airflow 가 git-sync 후 재파싱 |

> `task_runner.py` 와 DAG 파일을 **동시에** 수정한 경우엔 두 워크플로우가 **동시에 트리거**됩니다. Gitea Actions 러너 수가 제한된 환경에서는 순차 실행될 수 있습니다.

### DAG trigger 스크립트

```bash
bash run_dag.sh   # Airflow REST API로 DAG 실행
```

---

## 모델 다운로드 & 배포

학습 완료 후 MLflow S3에 저장된 아티팩트를 PVC로 복사해 Runway 모델 배포 UI로 서빙합니다.

### 1. IDE 배포 (PVC 마운트)

Runway 콘솔에서 IDE(VS Code Server / JupyterLab) 배포 시 PVC를 `/mnt/models` 경로에 마운트.

### 2. IDE 내에서 다운로드 스크립트 실행

```bash
export OPENBAO_TOKEN="s.xxx..."         # OpenBao 서비스 토큰

# 사용 가능한 모델 목록
python download_model.py --list

# 최신 모델 다운로드
python download_model.py

# 특정 모델 ID 지정
python download_model.py --model-id m-aa64f3852e0845838624882dfc40794b
```

결과: `/mnt/models/m-{model-id}/` 아래에 `MLmodel`, `model.ubj`, `conda.yaml`, 등의 아티팩트 복사.

### 3. Runway 모델 배포 UI

- **Model Deployment → Create** → PVC 선택 → `/mnt/models/{model-id}/` 경로 지정 → 배포

---

## 아키텍처 상세

### 인증 / 자격증명 흐름

```
┌─ OpenBao (KV v2) ────────────────────────────┐
│  namespace=<project-id>                      │
│  secret/data/wind-power                      │
│    aws_access_key_id                         │
│    aws_secret_access_key                     │
│    gitea_username                            │
│    gitea_password                            │
└──────────────────────────────────────────────┘
         ▲
         │  hvac / HTTP (X-Vault-Token)
         │
  ┌──────┴───────────────────────────────────┐
  │                                          │
  │  Airflow scheduler (@task)               │
  │  ensure_pull_secret                      │
  │    → dockerconfigjson 구성               │
  │    → K8s Secret create/update            │
  │       (gitea-registry-pull)              │
  │                                          │
  │  KubernetesPodOperator Pod               │
  │  task_runner.py                          │
  │    → load_secrets()                      │
  │    → boto3 S3 client                     │
  └──────────────────────────────────────────┘
```

### MLflow

- **Tracking URI**: `https://mlflow.<your-runway-domain>` (config.py 가 RUNWAY_BASE_DOMAIN 에서 파생)
- **인증**: `MLFLOW_TRACKING_TOKEN=<RUNWAY_API_KEY>` (Keycloak offline token 직접 사용)
- **Experiment naming rule**: `{프로젝트ID}.{실험명}` (Runway 규약) — 예: `<your-project-id>.wind-power-prediction`
- **Artifact location**: `s3://{프로젝트ID}/mlflow/experiments/{실험명}/models/m-{id}/artifacts/`

### 태스크 간 아티팩트 공유 (S3)

DAG_RUN_ID별 고유 prefix:

```
s3://{S3_BUCKET}/wind-power/dag-runs/{DAG_RUN_ID}/
  ├── turbine_data.csv   # load_data → train_model
  ├── model_init.pkl     # load_model → train_model
  ├── model_trained.pkl  # train_model → evaluate_model, log_to_mlflow
  ├── test_data.pkl      # train_model → evaluate_model
  └── metrics.json       # evaluate_model → log_to_mlflow
```

---

## 트러블슈팅

### `permission denied` (K8s pod 생성)

```
User "system:serviceaccount:runway-applications:airflow-scheduler"
cannot list pods in the namespace "<your-project-id>"
```

→ **사전 준비 3** RoleBinding 누락. 위 `kubectl create rolebinding` 재실행.

### `FailedToRetrieveImagePullSecret (gitea-registry-pull)`

→ `ensure_pull_secret` 태스크가 실패했거나, namespace의 Secret이 사라진 상태. DAG 재실행하면 첫 태스크가 자동 복구. 계속 실패하면 **OpenBao의 gitea_username/gitea_password 누락** 확인.

### MLflow `permission denied` / `Failed to validate offline token`

- `RUNWAY_API_KEY`가 다른 프로젝트에서 발급된 토큰이거나 세션이 만료됨.
- Runway UI에서 **현재 프로젝트의 새 토큰 재발급** 후 DAG 파일 업데이트.

### MLflow experiment 생성 `permission denied`

- Experiment naming rule 위반. `task_runner.py`의 `EXPERIMENT_NAME` / `MODEL_NAME`이 `{프로젝트ID}.{실험명}` 형식인지 확인.

### OpenBao `permission denied` / `no handler for route`

- `OPENBAO_TOKEN`이 만료되었거나 잘못된 namespace 진입. 콘솔에서 해당 namespace로 재로그인 후 토큰 재발급.
- `no handler for route`는 KV 엔진이 enable되지 않은 상태 → **사전 준비 4** 참고.

### 이미지 pull은 되는데 `FailedToRetrieveImagePullSecret` 경고

노드 이미지 캐시 hit로 임시 성공한 상태. 다른 노드 스케줄링 / 새 이미지 태그 push 시 즉시 실패. `ensure_pull_secret` 태스크 로그 확인하여 근본 해결.

### `download_model.py` 에서 아티팩트를 못 찾음 (`아티팩트를 찾을 수 없습니다`)

실험 이름 변경 시 S3 경로가 어긋난 것. `task_runner.py` 의 `EXPERIMENT_NAME` 을 기본값에서 바꿨다면 **`download_model.py` 의 `S3_ARTIFACT_PREFIX` 도 같이 수정**해야 한다.

- `EXPERIMENT_NAME = "<your-project-id>.my-new-exp"` 로 바꿨다면 →
- `S3_ARTIFACT_PREFIX = "mlflow/experiments/my-new-exp/models/"` 로 업데이트

(`{프로젝트ID}.{실험명}` 중 **`.` 뒤 부분**이 S3 prefix 의 경로 세그먼트와 일치해야 함)

---

## 데이터셋

- **위치**: `dataset/turbine_data.csv` (Docker 이미지에 번들)
- **크기**: ~10,000 행
- **타겟**: `activepower` (풍력 발전 출력)
- **특성**: 풍속, 풍향, 블레이드 각도 등 터빈 센서 값

---

## 참고

- 현재 DAG 는 KubernetesPodOperator + S3 아티팩트 공유 + OpenBao + ensure_pull_secret 구조입니다 (`wind_power_prediction_v4.py`).
- 파일명에 `_v4` 접미사가 붙은 이유: 내부 이터레이션을 거치면서 v1 (PythonOperator + XCom/PVC) → v4 로 단계적 발전했고, dag_id 고정성을 위해 버전 접미사를 유지합니다.

## 보안

이 튜토리얼은 단순화를 위해 `RUNWAY_API_KEY` 와 `OPENBAO_TOKEN` 을 DAG 파일에 하드코딩합니다. 다음 원칙을 지키세요:

- **저장소는 반드시 Private**. public 전환 시 즉시 토큰 노출 → revoke 필요.
- 다른 프로젝트로 **fork / 이식** 시 모든 토큰/키 값을 **본인 환경 값으로 교체**.
- **프로덕션** 환경에선 아래로 완전 분리 권장:
  - Airflow Pod env: K8s Secret `env_from` (dockerconfigjson 제외)
  - CI/CD 토큰: Gitea Actions Secrets
  - 런타임 조회 대상: OpenBao + hvac (현재 AWS 키는 이미 이 방식)
- **토큰 유출 의심** 시: Runway UI 에서 API 토큰 revoke 후 새로 발급 → OpenBao namespace 에서 서비스 토큰 재발급 → 저장소의 값 일괄 갱신.
