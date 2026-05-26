# Energy Demand Prediction — Runway 2.0 배포 절차

## 완료된 단계

- [x] Gitea 레포 생성 (`<your-project-id>/energy-demand-prediction`)
- [x] 소스 코드 push (7개 커밋)

---

## Step 1. Gitea Actions Secrets 등록

레포 **Settings > Secrets and Variables > Actions**에 4개 등록:

| Secret | 값 | 비고 |
|--------|-----|------|
| `GIT_USERNAME` | Gitea 로그인명 | 예: `<your-gitea-username>` |
| `GIT_TOKEN` | 개인 액세스 토큰 | Repository + Package write 권한. Gitea > Settings > Applications 에서 발급 |
| `IMAGE_TAG` | `gitea.<your-runway-domain>/<your-project-id>/energy-demand-prediction:latest` | ML 이미지 |
| `GUI_IMAGE_TAG` | `gitea.<your-runway-domain>/<your-project-id>/energy-demand-gui:latest` | GUI 이미지 |

## Step 2. CI/CD 워크플로우 실행 확인

Secrets 등록 후, 워크플로우를 트리거하려면 코드를 한 번 더 push 하거나 Gitea Actions 탭에서 Re-run:

```bash
# 빈 커밋으로 워크플로우 트리거 (필요 시)
git commit --allow-empty -m "chore: trigger CI/CD"
git push origin main
```

Actions 탭에서 3개 워크플로우 확인:
- **Build ML Image** — ✅ 녹색 확인 (5~10분)
- **Build GUI Image** — ✅ 녹색 확인
- **Sync DAG** — ✅ 녹색 확인

## Step 3. PVC 생성

**Runway 콘솔 > 프로젝트 > 스토리지 > + 생성**:

| 필드 | 값 |
|------|-----|
| 볼륨 ID | `energy-demand-data` |
| 스토리지 클래스 | `ceph-filesystem` |
| 접근 모드 | `ReadWriteMany` |
| 크기 | `5` GiB |

생성 후 목록에서 `Bound` 상태 확인.

## Step 4. Code Server 에서 PVC 에 데이터셋 업로드

### 4-1. Code Server 가 PVC 를 마운트하도록 설정

기존 Code Server 의 values.yaml 에서 `persistence.existingClaim` 을 `energy-demand-data` 로 변경하거나,
새 Code Server 를 배포하여 해당 PVC 를 마운트.

```yaml
persistence:
  enabled: true
  mountPath: /mnt/data
  existingClaim: energy-demand-data
```

### 4-2. 데이터셋 파일 복사

Code Server 터미널에서:

```bash
mkdir -p /mnt/data/dataset

# 로컬에서 데이터셋을 Code Server 로 업로드 (드래그앤드롭 또는 git)
# 필요한 파일:
#   Q1.csv        — 학습 데이터 (1298행)
#   Q3.csv        — 학습 데이터 (1217행)
#   Q1_eval.csv   — 평가 데이터 (1행)
#   Q3_eval.csv   — 평가 데이터 (1행)
```

확인:
```bash
ls -la /mnt/data/dataset/
# Q1.csv  Q3.csv  Q1_eval.csv  Q3_eval.csv 가 있어야 함
```

## Step 5. OpenBao 시크릿 등록

1. `https://openbao.<your-runway-domain>` 접속 → 프로젝트 네임스페이스 로그인
2. **Secret Engines** > `secret/` > **Create secret**
3. **Path**: `energy-demand`
4. 5개 key-value 입력:

| Key | 값 | 용도 |
|-----|-----|------|
| `aws_access_key_id` | S3 Access Key | task_runner S3 접근 |
| `aws_secret_access_key` | S3 Secret Key | task_runner S3 접근 |
| `gitea_username` | Gitea 로그인명 | ensure_pull_secret 이미지 pull 인증 |
| `gitea_password` | Gitea 액세스 토큰 | ensure_pull_secret 이미지 pull 인증 |
| `runway_api_key` | Runway API 토큰 | MLflow 인증 + 추론 엔드포인트 Bearer |

> S3 자격증명: Runway 콘솔 > Keys 메뉴에서 발급
> Runway API 토큰: 콘솔 > 계정 설정 > 액세스 키 > API 키

## Step 6. DAG 파일에 본인 값 설정

`energy_demand_prediction.py` 상단 3줄을 본인 값으로 수정:

```python
RUNWAY_PROJECT_ID  = "<your-project-id>"          # ← 본인 프로젝트 ID
RUNWAY_BASE_DOMAIN = "<your-runway-domain>"       # ← 본인 베이스 도메인
OPENBAO_TOKEN      = "s.<본인-OpenBao-토큰>"   # ← Step 5 에서 로그인 후 Copy token
```

```bash
git add energy_demand_prediction.py
git commit -m "fix: set user-specific DAG constants"
git push origin main
```

> push 하면 sync-dag 워크플로우가 자동 실행되어 airflow-dags 레포에 반영됨.

## Step 7. Airflow RoleBinding 확인

```bash
kubectl get rolebinding airflow-scheduler-pod-runner -n <your-project-id>
```

없으면 생성:
```bash
kubectl create rolebinding airflow-scheduler-pod-runner \
  --clusterrole=edit \
  --serviceaccount=runway-applications:airflow-scheduler \
  -n <your-project-id>
```

> wind-power-prediction 에서 이미 생성했다면 동일 프로젝트이므로 재생성 불필요.

## Step 8. (옵션 A) Code Server 에서 수동 실행

Airflow DAG 를 사용하지 않고 Code Server 에서 직접 파이프라인을 실행할 수 있습니다.
디버깅, 빠른 테스트, 또는 Airflow 에 문제가 있을 때 유용합니다.

### 8-A-1. 시스템 Python 설치 + venv 초기화

```bash
cd ~/workspace/energy-demand-prediction
bash setup.sh
```

> `setup.sh` 가 python3 미설치 시 자동으로 `apt install` 합니다.

### 8-A-2. venv 활성화 + .env 설정

```bash
source venv/bin/activate

# .env 파일 생성 (최초 1회)
cp .env.example .env
```

`.env` 파일 편집:
```dotenv
RUNWAY_PROJECT_ID=<your-project-id>
RUNWAY_BASE_DOMAIN=<your-runway-domain>
OPENBAO_TOKEN=s.<OpenBao 콘솔에서 Copy token>
```

### 8-A-3. 파이프라인 순차 실행

```bash
# 1) 데이터 로드 — PVC 에서 CSV 읽어 S3 업로드
python task_runner.py --step load_data

# 2) 모델 학습 — S3 에서 데이터 받아 MultiOutputRegressor 학습 (수 분 소요)
python task_runner.py --step train_model

# 3) 모델 평가 — Q별 RMSE/MAE/MAPE 계산
python task_runner.py --step evaluate_model

# 4) MLflow 등록 — pyfunc 모델 + 메트릭 로깅
python task_runner.py --step log_to_mlflow
```

### 8-A-4. 확인

각 step 이 `[완료]` 메시지를 출력하면 성공.
실패 시 에러 메시지를 확인하세요:
- `OPENBAO_TOKEN 이 비어 있습니다` → `.env` 확인
- `RUNWAY_BASE_DOMAIN 이 비어 있습니다` → `.env` 확인
- `OpenBao 403 Forbidden` → 토큰 만료 → OpenBao 재로그인 후 `.env` 갱신
- `FileNotFoundError` → PVC 에 데이터셋이 없음 → Step 4 확인

> Code Server 에서 실행한 결과도 동일하게 S3 와 MLflow 에 저장되므로,
> 이후 Step 9 (MLflow 확인) → Step 10 (추론 엔드포인트) 으로 바로 이어갈 수 있습니다.

---

## Step 8. (옵션 B) Airflow DAG 실행

1. `https://airflow.<your-runway-domain>` 접속
2. DAG 목록에서 `energy_demand_prediction_<your-project-id>` 찾기
3. DAG 토글 **활성화**
4. **Trigger** 클릭
5. 5개 태스크 순차 실행 확인:

```
ensure_pull_secret → load_data → train_model → evaluate_model → log_to_mlflow
```

각 태스크 클릭 > **Logs** 탭에서 진행 상황 확인.

### 트러블슈팅

| 증상 | 원인 / 대응 |
|------|------------|
| `ensure_pull_secret` 403 Forbidden | OpenBao 토큰 만료 → Step 5 재로그인 후 토큰 갱신 → Step 6 재push |
| `load_data` FileNotFoundError | PVC 에 CSV 미업로드 → Step 4 확인 |
| `load_data` KeyError 컬럼 없음 | CSV 파일 형식 불일치 → 컬럼명 확인 |
| `train_model` OOMKilled | 메모리 부족 → DAG 의 memory_limit 증가 (2Gi → 4Gi) |
| `log_to_mlflow` permission denied | `EXPERIMENT_NAME` 규약 위반 → `{프로젝트ID}.{실험명}` 형식 확인 |
| `RUNWAY_BASE_DOMAIN 이 비어 있습니다` | DAG common_env_vars 에 RUNWAY_BASE_DOMAIN 누락 → 코드 확인 |

## Step 9. MLflow 에서 모델 확인

1. `https://mlflow.<your-runway-domain>` 접속
2. **Experiments** > `<your-project-id>.energy-demand-prediction` 확인
3. 최신 run 클릭 → 파라미터/메트릭/아티팩트 확인
4. **Registered Models** > `<your-project-id>.energy-demand-xgboost` 버전 존재 확인

## Step 10. 추론 엔드포인트 생성 + 모델 배포

### 10-1. 엔드포인트 생성

**Runway 콘솔 > 추론 엔드포인트 > + 생성**:

| 필드 | 값 |
|------|-----|
| 엔드포인트 이름 | `Energy Demand Prediction` |
| 엔드포인트 ID | `energy-demand-prediction` |
| 서빙 런타임 | `MLServer` |

### 10-2. 모델 배포 추가

엔드포인트 상세 > **모델 배포** 클릭:

| 필드 | 값 |
|------|-----|
| 이름 | `Energy Demand v1` |
| ID | `energy-demand-v1` |
| 모델 소스 | MLflow (Step 9 에서 확인한 모델) |
| CPU | `500` millicores |
| Memory | `1024` MiB |
| 복제본 | `1` |

엔드포인트가 **Healthy** 가 되면 준비 완료 (1~3분 소요).

### 10-3. 추론 테스트 (curl)

엔드포인트 상세 페이지에서 **요청 URL** 복사 후:

```bash
export RUNWAY_API_KEY="<runway_api_key>"
export ENDPOINT="https://inference.<your-runway-domain>/api/<your-project-id>/energy-demand-prediction/energy-demand-v1"

# 간단 테스트 (피처 4개만 — 실제로는 126개 전부 필요)
curl -X POST "${ENDPOINT}/v2/models/default/infer" \
  -H "Authorization: Bearer ${RUNWAY_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "parameters": {"content_type": "pd"},
    "inputs": [
      {"name": "시간", "shape": [1], "datatype": "INT64", "data": [14]},
      {"name": "요일", "shape": [1], "datatype": "INT64", "data": [3]},
      {"name": "연중일수비율", "shape": [1], "datatype": "FP64", "data": [0.5]},
      {"name": "공휴일", "shape": [1], "datatype": "INT64", "data": [0]}
    ]
  }'
```

> 전체 126 피처 테스트는 GUI 에서 CSV 업로드로 수행 (Step 12).

## Step 11. GUI 배포

### 옵션 A: Helm Chart 로 배포 (kubectl 접근 가능 시)

```bash
helm install energy-demand-gui ./helm/gui -n <your-project-id>
```

### 옵션 B: Runway 콘솔 카탈로그 활용

Gitea Container Registry 에 올라간 GUI 이미지를 사용하여 범용 웹앱 차트로 배포.
httpRoute hostname: `energy-demand.<your-runway-domain>` (또는 원하는 서브도메인)

### 배포 확인

`https://energy-demand.<your-runway-domain>` 접속 → React 앱 로드 확인.

## Step 12. GUI 통합 테스트

### 12-1. API 설정

GUI 첫 로드 시 **API 설정** 패널이 열림. 아래 값 입력:

| 필드 | 값 |
|------|-----|
| Runway API 토큰 | OpenBao `runway_api_key` 값 |
| 추론 엔드포인트 URL | `https://inference.<your-runway-domain>/api/<your-project-id>/energy-demand-prediction/energy-demand-v1` |
| Deployment ID | `default` |
| Airflow URL | `https://airflow.<your-runway-domain>` |
| Airflow Username | Airflow 로그인명 |
| Airflow Password | Airflow 비밀번호 |
| DAG ID | `energy_demand_prediction_<your-project-id>` |

**저장** 클릭.

### 12-2. 추론 테스트

1. 헤더의 **추론 데이터 업로드** 클릭
2. `Q1_test_x.csv` 업로드
3. 자동 일괄 추론 실행 → 72시간 예측 결과 생성
4. **예측** 탭에서 차트 확인 (과거 실측 + 예측 라인)

### 12-3. 실측 비교 테스트

1. 헤더의 **실측 데이터 업로드** 클릭
2. `Q1_test_xy.csv` 업로드
3. 예측 vs 실측 비교 차트 표시
4. 상단 메트릭 카드에 정확도/MAPE/MAE 확인

### 12-4. 재학습 트리거 테스트

1. 전체 정확도가 임계값(85%) 이하이면 **재학습** 버튼 표시됨
2. 재학습 클릭 → Airflow REST API 로 DAG trigger
3. `https://airflow.<your-runway-domain>` 에서 새 DAG Run 생성 확인

---

## 정리 / 재실행

### 재실행 시

같은 구성으로 다시 돌릴 때는 **Step 8 (DAG 실행)** 부터.
코드 수정 시 `git push` → CI/CD 자동 동작 → Airflow 재실행.

### 삭제 순서

1. 추론 엔드포인트 → 모델 배포 삭제 → 엔드포인트 삭제
2. GUI 앱 (Helm uninstall 또는 카탈로그 삭제)
3. Code Server 삭제 (볼륨은 유지 가능)
4. 스토리지 → `energy-demand-data` 삭제 (재사용 시 유지)
5. Gitea 저장소 / Actions Secrets / OpenBao 시크릿은 재사용 가능

### OpenBao 토큰 만료 시

1. OpenBao 콘솔 재로그인 → 프로필 → Copy token
2. `energy_demand_prediction.py` 상단 `OPENBAO_TOKEN` 갱신
3. `git push` → sync-dag 자동 실행 → Airflow 재파싱
4. DAG 재실행

