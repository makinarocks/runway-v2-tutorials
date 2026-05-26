# Energy Demand Prediction — Runway 2.0 배포 절차

새 Runway 프로젝트에서 처음부터 에너지 수요 예측 데모를 배포하는 전체 절차.

> **표기 규약**: `<your-project-id>`, `<your-runway-domain>` 등은 본인 환경에 맞게 교체.

---

## Step 0. 사전 준비 — 토큰 발급

시작 전 **3가지 토큰**을 미리 확보합니다:

| 토큰 | 발급 위치 | 용도 |
|------|----------|------|
| **Gitea 개인 액세스 토큰** | Gitea > 우측 상단 아바타 > Settings > Applications > Generate Token (Repository + Package write 체크) | 코드 push, CI/CD, Helm chart 업로드 |
| **OpenBao 서비스 토큰** | `https://openbao.<your-runway-domain>` 프로젝트 네임스페이스 로그인 → 프로필 → Copy token | AWS 키/Gitea 자격증명 런타임 조회 |
| **Runway API 토큰** | Runway 콘솔 > 계정 설정 > 액세스 키 > API 키 | MLflow 인증 + 추론 Bearer 토큰 |

> S3 자격증명 (aws_access_key_id / aws_secret_access_key): Runway 콘솔 > Keys 메뉴에서 발급.

---

## Step 1. PVC 생성

**Runway 콘솔 > 프로젝트 > 스토리지 > + 생성**:

| 필드 | 값 |
|------|-----|
| 볼륨 ID | `<your-pvc-name>` (예: `energy-pred-fs`) |
| 스토리지 클래스 | `ceph-filesystem` |
| 접근 모드 | **ReadWriteMany** (필수! RWO 는 Multi-Attach 에러 발생) |
| 크기 | `5` GiB |

생성 후 목록에서 `Bound` 상태 확인.

> 이 PVC 는 데이터셋 + 모델 아티팩트 + Code Server 작업 공간으로 공용 사용됩니다.

---

## Step 2. Code Server 배포 + PVC 마운트

**Runway 콘솔 > 카탈로그 > Code Server** 배포.

### 기본 정보

| 필드 | 값 (예시) |
|------|----------|
| 이름 | `Energy Prediction IDE` |
| ID | `energy-prediction-server` |

### values.yaml 수정

```yaml
persistence:
  enabled: true
  mountPath: /mnt/data
  existingClaim: <your-pvc-name>    # Step 1 에서 만든 볼륨 ID

httpRoute:
  enabled: true
  hostname: "<your-ide-hostname>.<your-runway-domain>"
```

### 접속

생성 후 **리소스 현황 > 링크 추가** 에서 URL 등록.
`https://<your-ide-hostname>.<your-runway-domain>` 접속 → 브라우저 VS Code.

---

## Step 3. Gitea 레포 생성

1. `https://gitea.<your-runway-domain>` 접속
2. 우측 상단 **+ → 새 저장소 만들기**
3. **소유자**: `<your-project-id>` 조직
4. **이름**: `energy-demand-prediction`
5. **가시성**: Private
6. **저장소 초기화**: 체크 (README.md 자동 생성)
7. **저장소 만들기**

---

## Step 4. 코드 복사 + 플레이스홀더 수정

Code Server 터미널에서:

### 4-1. Git 설정 + clone

```bash
cd ~/workspace

git config --global user.name "<your-name>"
git config --global user.email "<your-email>"
git config --global credential.helper store

# Gitea 레포 clone
git clone https://gitea.<your-runway-domain>/<your-project-id>/energy-demand-prediction.git
cd energy-demand-prediction
```

### 4-2. 튜토리얼 코드 복사

```bash
# GitHub 에서 레퍼런스 코드 clone (별도 인증 불필요 — public)
cd ~/workspace
git clone https://github.com/makinarocks/runway-v2-tutorials.git reference

# 튜토리얼 소스를 본인 레포로 복사
cd reference/tutorials/energy-demand-prediction
cp -r .gitea Dockerfile Dockerfile.gui requirements.txt config.py task_runner.py \
      energy_demand_prediction.py download_model.py test_inference.py setup.sh \
      gui helm .env.example .gitignore ~/workspace/energy-demand-prediction/

# reference 삭제
cd ~/workspace && rm -rf reference
```

### 4-3. 플레이스홀더 값 수정

**6개 파일**에서 `<your-...>` 값을 본인 환경으로 교체:

#### ① `energy_demand_prediction.py` (DAG) — 4곳

```python
RUNWAY_PROJECT_ID  = "<your-project-id>"        # 본인 프로젝트 ID
RUNWAY_BASE_DOMAIN = "<your-runway-domain>"      # 예: try.mrxrunway.ai
OPENBAO_TOKEN      = "<your-openbao-token>"      # Step 0 에서 발급한 OpenBao 토큰
PVC_NAME           = "<your-pvc-name>"           # Step 1 에서 생성한 PVC 볼륨 ID
```

#### ② `gui/nginx.conf` — 2곳

```nginx
proxy_pass https://inference.<your-runway-domain>/api/;
proxy_set_header Host inference.<your-runway-domain>;
...
proxy_pass https://airflow.<your-runway-domain>/;
proxy_set_header Host airflow.<your-runway-domain>;
```

#### ③ `gui/vite.config.js` — 2곳 (로컬 개발용)

```javascript
target: 'https://inference.<your-runway-domain>',
...
target: 'https://airflow.<your-runway-domain>',
```

#### ④ `helm/gui/values.yaml` — 2곳

```yaml
image:
  repository: gitea.<your-runway-domain>/<your-project-id>/energy-demand-gui
httpRoute:
  hostname: "<your-gui-hostname>.<your-runway-domain>"
```

---

## Step 5. 첫 push

```bash
cd ~/workspace/energy-demand-prediction
git add .
git commit -m "feat: initial energy-demand-prediction setup"
git push origin main
```

---

## Step 6. Gitea Actions Secrets 등록

레포 **Settings > Secrets and Variables > Actions** 에 4개 등록:

| Secret | 값 |
|--------|-----|
| `GIT_USERNAME` | Gitea 로그인명 |
| `GIT_TOKEN` | Step 0 의 Gitea 개인 액세스 토큰 |
| `IMAGE_TAG` | `gitea.<your-runway-domain>/<your-project-id>/energy-demand-prediction:latest` |
| `GUI_IMAGE_TAG` | `gitea.<your-runway-domain>/<your-project-id>/energy-demand-gui:latest` |

---

## Step 7. CI/CD 워크플로우 트리거 + 확인

Secrets 등록 후 워크플로우를 트리거합니다:

```bash
git commit --allow-empty -m "chore: trigger CI/CD"
git push origin main
```

Gitea Actions 탭에서 3개 워크플로우 확인:
- **Build ML Image** — ✅ 녹색 확인 (5~10분)
- **Build GUI Image** — ✅ 녹색 확인
- **Sync DAG** — ✅ 녹색 확인

---

## Step 8. 데이터셋 업로드

Code Server 터미널에서:

```bash
mkdir -p /mnt/data/dataset

# 아래 디렉토리를 /mnt/data/dataset/ 에 업로드 (드래그앤드롭 또는 git)
# pred-demo-dataset/   — 학습 데이터 (Q1.csv, Q2.csv, Q3.csv)
# pred-demo-testset/   — 평가 데이터 (Q1.csv, Q2.csv, Q3.csv, Q4.csv)
```

확인:
```bash
ls /mnt/data/dataset/pred-demo-dataset/
# Q1.csv  Q2.csv  Q3.csv
ls /mnt/data/dataset/pred-demo-testset/
# Q1.csv  Q2.csv  Q3.csv  Q4.csv
```

---

## Step 9. OpenBao 시크릿 등록

1. `https://openbao.<your-runway-domain>` 접속 → 프로젝트 네임스페이스 로그인
2. **Secret Engines** > `secret/` > **Create secret**
3. **Path**: `energy-demand`
4. 5개 key-value 입력:

| Key | 값 | 용도 |
|-----|-----|------|
| `aws_access_key_id` | S3 Access Key | task_runner S3 접근 |
| `aws_secret_access_key` | S3 Secret Key | task_runner S3 접근 |
| `gitea_username` | Gitea 로그인명 | ensure_pull_secret 이미지 pull 인증 |
| `gitea_password` | Step 0 Gitea 액세스 토큰 | ensure_pull_secret 이미지 pull 인증 |
| `runway_api_key` | Step 0 Runway API 토큰 | MLflow 인증 + 추론 Bearer |

---

## Step 10. Airflow RoleBinding 확인

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

---

## Step 11. 모델 학습

### 옵션 A: Code Server 에서 수동 실행 (권장 — 디버깅 용이)

```bash
cd ~/workspace/energy-demand-prediction
bash setup.sh                    # Python 3.10 + venv (최초 1회)
source venv/bin/activate
cp .env.example .env             # 편집: RUNWAY_PROJECT_ID, RUNWAY_BASE_DOMAIN, OPENBAO_TOKEN

python task_runner.py --step load_data
python task_runner.py --step train_model        # 수 분 소요
python task_runner.py --step evaluate_model
python task_runner.py --step log_to_mlflow
```

> `setup.sh` 는 Python 3.10 미설치 시 자동으로 `apt install` 합니다.
> Python 3.10 을 사용하는 이유: MLServer 가 3.10 기반이라 pickle 호환성 필요.

### 옵션 B: Airflow DAG 실행

1. `https://airflow.<your-runway-domain>` 접속
2. DAG 목록에서 `energy_demand_prediction_<your-project-id>` 찾기
3. DAG 토글 **활성화** → **Trigger** 클릭
4. 5개 태스크 순차 실행 확인:
   ```
   ensure_pull_secret → load_data → train_model → evaluate_model → log_to_mlflow
   ```

> 주의: 프로젝트 CPU 쿼터(보통 10코어)가 부족하면 Code Server 리소스를 줄이거나 옵션 A 사용.

### 트러블슈팅

| 증상 | 원인 / 대응 |
|------|------------|
| `OpenBao 403 Forbidden` | 토큰 만료 → OpenBao 재로그인 → 토큰 갱신 → push |
| `FileNotFoundError` | PVC 에 데이터셋 미업로드 → Step 8 확인 |
| `OOMKilled` | 메모리 부족 → DAG 의 memory_limit 증가 |
| `RUNWAY_BASE_DOMAIN 이 비어 있습니다` | `.env` 또는 DAG 상수 확인 |

---

## Step 12. MLflow 모델 확인

1. `https://mlflow.<your-runway-domain>` 접속
2. **Experiments** > `<your-project-id>.energy-demand-prediction` 확인
3. **Registered Models** > `<your-project-id>.energy-demand-xgboost` 버전 존재 확인

---

## Step 13. 모델 다운로드 + 추론 엔드포인트 배포

### 13-1. 모델 아티팩트 PVC 에 다운로드

```bash
source venv/bin/activate
python download_model.py --list       # 모델 목록 확인
python download_model.py              # 최신 모델 다운로드
ls /mnt/data/models/                  # m-xxx 디렉토리 확인
```

### 13-2. 추론 엔드포인트 생성

**Runway 콘솔 > 추론 엔드포인트 > + 생성**:

| 필드 | 값 |
|------|-----|
| 엔드포인트 이름 | `Energy Demand Prediction` |
| 엔드포인트 ID | `energy-demand-prediction` |
| 서빙 런타임 | `MLServer` |

### 13-3. 모델 배포 추가

엔드포인트 상세 > **모델 배포** 클릭:

| 필드 | 값 |
|------|-----|
| 이름 | `Energy Demand v1` |
| ID | `energy-demand-v1` |
| 볼륨 | `<your-pvc-name>` |
| 모델 경로 | `/mnt/models/m-<model-id>` |
| CPU | `500` millicores |
| Memory | `1024` MiB |

> `/mnt/models` 는 추론 Pod 의 PVC 마운트 경로 (`/mnt/data` 가 아님에 주의).
> 모델이 `/mnt/data/models/m-xxx/` 에 있으면 경로는 `/mnt/models/m-xxx` 로 입력.

### 13-4. 추론 테스트

```bash
# .env 에 INFERENCE_ENDPOINT 추가
# INFERENCE_ENDPOINT=https://inference.<your-runway-domain>/api/<your-project-id>/energy-demand-prediction/energy-demand-v1

python test_inference.py
```

---

## Step 14. GUI 배포

### 14-1. Helm chart 패키징 + Gitea 업로드

```bash
helm package helm/gui/

curl -X POST \
  --user "<gitea-username>:<gitea-token>" \
  -H "Content-Type: application/octet-stream" \
  --data-binary @energy-demand-gui-0.4.0.tgz \
  https://gitea.<your-runway-domain>/api/packages/<your-project-id>/helm/api/charts
```

> `helm` CLI 미설치 시: `curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash`

### 14-2. Runway 콘솔에서 배포

**Runway 콘솔 > 애플리케이션 > + 생성**:

| 필드 | 값 |
|------|-----|
| Helm 리포지토리 URL | `https://gitea.<your-runway-domain>/api/packages/<your-project-id>/helm` |
| 사용자 이름 | Gitea 로그인명 |
| 비밀번호 | Gitea 액세스 토큰 |
| 차트 | `energy-demand-gui` |
| 버전 | `0.4.0` |

### 14-3. 접속 확인

`https://<your-gui-hostname>.<your-runway-domain>` 접속 → React 앱 로드 확인.

> HTTPRoute 가 동작하려면 `parentRefs` (platform-core-gateway) 가 설정되어 있어야 합니다 (이미 Helm chart 에 포함).

---

## Step 15. GUI 통합 테스트

### 15-1. API 설정

GUI 첫 로드 시 **API 설정** 패널이 열림. 아래 값 입력:

| 필드 | 값 (Runway 배포 시) |
|------|-----|
| Runway API 토큰 | Step 0 의 Runway API 토큰 |
| 추론 엔드포인트 URL | `/api/inference/<your-project-id>/energy-demand-prediction/energy-demand-v1` |
| Deployment ID | `default` |
| Airflow URL | `/api/airflow` |
| Airflow 토큰 | 브라우저 DevTools > Network > Authorization 헤더에서 복사 |
| DAG ID | `energy_demand_prediction_<your-project-id>` |

> 로컬 개발 시에도 동일 경로 사용 (Vite 프록시 경유).

### 15-2. 추론 테스트

1. **추론 데이터 업로드** → CSV 업로드 → 72시간 예측 차트
2. **실측 데이터 업로드** → 예측 vs 실측 비교 + 메트릭

### 15-3. 재학습 트리거

1. 전체 정확도가 임계값(85%) 이하이면 **재학습** 버튼 표시
2. 클릭 → Airflow DAG trigger (Q1+Q2+Q3 학습)
3. `https://airflow.<your-runway-domain>` 에서 새 DAG Run 확인

---

## 정리 / 재실행

### 재실행 시

같은 구성으로 다시 돌릴 때는 **Step 11 (모델 학습)** 부터.
코드 수정 시 `git push` → CI/CD 자동 동작.

### 삭제 순서

1. 추론 엔드포인트 → 모델 배포 삭제 → 엔드포인트 삭제
2. GUI 앱 삭제
3. Code Server 삭제 (볼륨은 유지 가능)
4. 스토리지 삭제 (재사용 시 유지)
5. Gitea 저장소 / Actions Secrets / OpenBao 시크릿은 재사용 가능

### OpenBao 토큰 만료 시

1. OpenBao 콘솔 재로그인 → 프로필 → Copy token
2. `energy_demand_prediction.py` 상단 `OPENBAO_TOKEN` 갱신
3. `.env` 파일의 `OPENBAO_TOKEN` 도 갱신 (Code Server 수동 실행 시)
4. `git push` → sync-dag 자동 실행 → Airflow 재파싱
