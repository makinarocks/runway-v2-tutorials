# End-to-End Walkthrough — Runway Code Server IDE 에서 처음부터 끝까지

이 문서는 **Runway 콘솔에서 PVC 와 Code Server IDE 를 배포하고, 그 IDE 안에서 wind-power-prediction 프로젝트 전체를 처음부터 구현 → 본인 Gitea 저장소에 push → DAG 실행 → 모델 배포 → 추론 테스트** 까지 한 사이클을 처음 따라가는 사용자를 위한 가이드입니다.

전제:
- 개발은 **로컬이 아니라 Runway 에 배포한 Code Server (VS Code in browser)** 에서 수행
- Gitea 의 **`airflow-dags` 저장소는 이미 존재** (플랫폼에서 기본 제공)
- **`wind-power-prediction` 저장소는 네가 직접 생성**

[README.md](./README.md) 는 구조/참조 문서이고, 본 문서는 실행 가이드입니다.

---

## 0. 시작 전에 확인할 것 (관리자/플랫폼 영역)

아래는 **일반적으로 Runway 관리자가 한 번만 세팅**하는 항목입니다. 본인이 처음 시도하는 사용자라면 관리자에게 확인 후 시작하세요.

| 항목 | 확인 방법 | 참고 |
|---|---|---|
| Runway 콘솔 로그인 가능 (Google SSO) | `https://runway.v2.mrxrunway.ai` 접속 | 워크스페이스에 초대되어 있어야 함 |
| 대상 프로젝트 존재 | 본 가이드 예시: `energy-forecasting` (namespace `rwyt-energy-forecasting`, S3 bucket 동일) | |
| K8s RoleBinding (`runway-applications:airflow-scheduler` → edit on `<project-ns>`) | `kubectl get rolebinding -n <project-ns>` | [README § 사전 준비 3](./README.md#3-airflow-scheduler-sa에-rolebinding-생성) |
| Gitea 조직(`rwyt-energy-forecasting`) 존재 + `airflow-dags` 저장소 기본 생성됨 | Gitea UI 에서 확인 | DAG sync 대상 |
| OpenBao namespace (`rwyt-energy-forecasting`) 로그인 가능 & KV v2 엔진(`secret`) enabled | `https://openbao.v2.mrxrunway.ai` 로그인 후 Secret Engines 메뉴 | [README § 사전 준비 4](./README.md#4-openbao-시크릿-등록) |

위가 다 OK 라면 **1단계부터** 시작.

---

## 1. 볼륨(PVC) 생성

학습된 모델 아티팩트를 S3 에서 내려받아 영구 보관할 볼륨. **IDE 개발 디렉토리도 여기 담아 세션 간 보존** 하도록 활용합니다.

### UI 경로

1. Runway 콘솔 → 워크스페이스(`Runway 2.0 Tutorials`) → 프로젝트 `energy-forecasting`
2. 좌측 **스토리지** 메뉴 → 우측 상단 **+ 생성**

### 입력 값

| 필드 | 값 | 이유 |
|---|---|---|
| 볼륨 ID | `wind-power-models` | 영문 소문자 + 하이픈, 최대 63자 |
| 스토리지 클래스 | `ceph-filesystem` | RWX 지원 (IDE + 추론 Pod 동시 마운트 가능) |
| 접근 모드 | `ReadWriteMany` | 여러 Pod 에서 동시 접근 |
| 크기 | `5` (GiB) | 모델 아티팩트/개발 작업 공간 |

**생성** 버튼 → 목록에 `Bound` 상태로 표시되면 성공.

---

## 2. Code Server IDE 배포 (PVC 마운트)

### UI 경로

1. 좌측 **카탈로그** → **Code server** 카드 → 우측 상단 **+ 애플리케이션 생성**

### 기본 정보

| 필드 | 값 (예시) |
|---|---|
| 이름 | `Wind Power IDE` |
| ID | `wind-power-ide` |
| 설명 | (선택) `풍력 예측 튜토리얼 개발용 VS Code` |

### Helm values.yaml 수정

기본 템플릿에서 두 군데만 바꿉니다:

**① `persistence` 를 기존 볼륨 재사용으로**
```yaml
persistence:
  enabled: true
  mountPath: /mnt/models
  existingClaim: wind-power-models   # 1단계에서 만든 볼륨 ID
  # existingClaim 사용 시 아래 필드는 무시됨
  # accessMode: ReadWriteMany
  # storageClassName: ""
  # size: 5Gi
```

**② `httpRoute.hostname` 을 본인 서브도메인으로**
```yaml
httpRoute:
  enabled: true
  hostname: "wind-power-ide.v2.mrxrunway.ai"
  hostnames: []
```

### 생성 & 접속

**생성** → 좌측 **애플리케이션** 메뉴 → 카드 상태가 `Healthy` 가 될 때까지 1-2분 대기 → 카드 클릭 → **상세 페이지 상단의 접속 URL** 로 진입하면 브라우저 VS Code 가 열립니다.

---

## 3. IDE 개발 환경 초기 설정

Code Server 터미널을 엽니다 (`Terminal > New Terminal` 또는 ``Ctrl+` ``).

### 3-1. 작업 디렉토리 이동 & git 기본 설정

```bash
# Code Server 홈 내 개발 디렉토리 (소스 코드 작업 공간)
cd ~/workspace

# git 사용자 정보 (본인 값으로)
git config --global user.name  "gyuseon.han"
git config --global user.email "gyuseon.han@makinarocks.ai"

# 자격증명 캐시: 한 번 입력 후 재사용
git config --global credential.helper store
```

> **두 경로의 역할 구분** — `~/workspace` 는 소스 코드 개발용 (git clone 대상), `/mnt/models` 는 1단계에서 마운트한 PVC 로 **모델 아티팩트 전용** (8단계 `download_model.py` 출력). 섞지 마세요.

### 3-2. 파이썬 패키지 설치

IDE 안에서 `download_model.py` / 기타 스크립트를 돌리려면:

```bash
pip install boto3 hvac
```

권한 오류가 나면:
```bash
pip install --user boto3 hvac && export PATH="$HOME/.local/bin:$PATH"
# 또는 venv
python -m venv .venv && source .venv/bin/activate && pip install boto3 hvac
```

### 3-3. OpenBao 서비스 토큰 확보 (AWS 키 조회용)

`download_model.py` 와 DAG 의 `ensure_pull_secret` / `task_runner.py` 가 공통으로 사용합니다.

1. 새 탭에서 `https://openbao.v2.mrxrunway.ai` 접속
2. 프로젝트 namespace (`rwyt-energy-forecasting`) 로 로그인 — 자동 발급되는 서비스 토큰을 **Copy token** 으로 복사
3. IDE 터미널에 저장 (나중에 코드의 `OPENBAO_TOKEN` 상수에도 넣을 값):
   ```bash
   export OPENBAO_TOKEN="<콘솔에서 복사한 값>"
   export OPENBAO_NAMESPACE="rwyt-energy-forecasting"
   ```

> 토큰은 세션 만료 시 재발급 필요.

### 3-4. Runway API 토큰 확보 (MLflow / 추론용)

**OpenBao 토큰과 별개**입니다. 이 토큰은 DAG 가 MLflow 에 접근할 때, 그리고 추론 endpoint 호출 시 `Authorization: Bearer` 헤더로 사용됩니다.

1. Runway 콘솔 우측 상단 **프로필 아이콘** → **API 토큰** (또는 **사용자 설정 > API 토큰**)
2. **새 토큰 발급** → 값을 안전한 곳에 저장

> 같은 사용자가 콘솔에서 새 토큰을 발급받으면 **이전 토큰은 무효화**됩니다. 실행 도중 재발급했다면 아래 5단계에서 DAG 파일의 `RUNWAY_API_KEY` 도 같이 갱신해야 함.

---

## 4. Gitea 에 본인 저장소(`wind-power-prediction`) 생성

### 4-1. 저장소 생성

1. `https://gitea.v2.mrxrunway.ai` 접속 & 로그인
2. 우측 상단 **+ → 새 저장소 만들기**
3. 입력:
   - **소유자**: `rwyt-energy-forecasting` (프로젝트와 동일 조직)
   - **저장소 이름**: `wind-power-prediction`
   - **가시성**: Private (팀 내부)
   - **저장소 초기화**: 체크 (README.md 자동 생성 — 첫 커밋 용)
4. **저장소 만들기**

> `airflow-dags` 저장소는 **이미 생성되어 있음** (플랫폼 기본 제공). 손대지 않습니다.

### 4-2. Actions Secrets 등록

저장소 **Settings → Secrets and Variables → Actions** 메뉴에 3개:

| 이름 | 값 |
|---|---|
| `GIT_USERNAME` | 본인의 Gitea 로그인명 |
| `GIT_TOKEN` | 개인 액세스 토큰 (패키지 write + `airflow-dags` write 권한) |
| `IMAGE_TAG` | `gitea.v2.mrxrunway.ai/rwyt-energy-forecasting/wind-power-prediction:latest` |

> 개인 액세스 토큰 발급: Gitea 우측 상단 아바타 → **Settings → Applications → Manage Access Tokens → Generate New Token** — UI 에서 **Repository**(write) 와 **Package**(write) 권한 체크. 이 토큰은 저장소 push 와 Container Registry push 양쪽에 동시에 쓰입니다.

---

## 5. IDE 에서 소스 코드 구성

공개 튜토리얼 저장소(`makinarocks/runway-v2-tutorials`) 를 참고 자료로 받아서 본인 Gitea 저장소에 맞게 복사/수정하는 흐름입니다.

> ⚠️ **보안 주의** — 이 튜토리얼은 단순화를 위해 **토큰/시크릿 값을 코드에 하드코딩**(`RUNWAY_API_KEY`, `OPENBAO_TOKEN`) 하는 방식을 사용합니다. 그러므로:
> - Gitea 저장소를 **반드시 Private 으로 유지** (4-1 에서 이미 Private 선택). 실수로 Public 전환 시 세계에 토큰이 노출됩니다.
> - 프로덕션 환경에서는 **Gitea Actions Secrets + OpenBao + K8s Secret** 으로 완전 분리 권장.
> - 토큰이 유출되었다고 판단되면 즉시 Runway UI / Gitea UI 에서 토큰 revoke 후 재발급 → 저장소 갱신.

### 5-1. 빈 저장소 clone

IDE 터미널 (`cd ~/workspace`) 에서:

```bash
cd ~/workspace
git clone https://gitea.v2.mrxrunway.ai/rwyt-energy-forecasting/wind-power-prediction.git
cd wind-power-prediction
```

> `~/workspace` 는 Code Server 의 홈 내 개발 디렉토리입니다. `/mnt/models` PVC 는 **모델 아티팩트 저장 전용** (8단계 `download_model.py` 의 출력 경로) 이며, 소스 코드를 거기에 두면 나중에 볼륨을 모델 배포 Pod 에 마운트할 때 코드가 섞여 혼란이 생깁니다.

> username 에 본인 Gitea 로그인명, password 에 4-2 에서 만든 개인 액세스 토큰 입력. `credential.helper store` 덕분에 이번 한 번만 입력.

### 5-2. 참고용 공개 저장소에서 파일 복사

> `makinarocks/runway-v2-tutorials` 는 GitHub public 저장소입니다 — 별도 인증 없이 clone 가능. 접근이 안 되면 플랫폼 담당자에게 대체 URL 을 문의하세요.

```bash
# 잠시 상위로 이동해서 reference 저장소 clone
cd ~/workspace
git clone https://github.com/makinarocks/runway-v2-tutorials.git reference

# 튜토리얼 소스를 본인 저장소로 복사 (.git 제외)
cd reference/tutorials/wind-power-prediction-with-xgboost
cp -r Dockerfile requirements.txt task_runner.py config.py .env.example \
      wind_power_prediction_v4.py download_model.py test_inference.py \
      run_dag.sh dataset ~/workspace/wind-power-prediction/
cp -r .gitea ~/workspace/wind-power-prediction/

# (선택) 문서도 함께
cp README.md WALKTHROUGH.md ~/workspace/wind-power-prediction/

# reference 는 삭제 가능
cd ~/workspace && rm -rf reference
```

### 5-3. 본인 환경 값 설정 — **두 곳만** 수정

설정이 `config.py` + `.env` + DAG 상단 2줄로 중앙화돼 있어서, 사용자가 손댈 곳은 **딱 2곳**입니다. 나머지 값(`NAMESPACE`, `IMAGE`, `EXPERIMENT_NAME`, `MODEL_NAME`, `S3_ARTIFACT_PREFIX` 등)은 `RUNWAY_PROJECT_ID` 한 값에서 자동 파생됩니다.

#### ① `.env` 생성 (IDE 스크립트용)

VS Code 에서 `~/workspace/wind-power-prediction/` 워크스페이스를 연 뒤 터미널에서:

```bash
cd ~/workspace/wind-power-prediction
cp .env.example .env
```

`.env` 를 열어 아래 값 설정:

```dotenv
RUNWAY_PROJECT_ID=rwyt-energy-forecasting          # 본인 프로젝트 ID
OPENBAO_TOKEN=<3-3 에서 복사한 OpenBao 서비스 토큰>

# 추론 테스트는 9단계(모델 배포) 이후 채움. 지금은 비워둬도 됨.
INFERENCE_ENDPOINT=
DEPLOYMENT_ID=wind-power-v1
```

> `.env` 는 `.gitignore` 에 포함되어 있어 Gitea 로 커밋되지 않습니다. IDE 스크립트 (`download_model.py`, `test_inference.py`) 가 `config.py` 를 통해 자동 로드합니다.

#### ② DAG 파일 상수 (`wind_power_prediction_v4.py`)

파일 상단 [사용자 설정] 섹션의 **2줄만** 수정:

```python
RUNWAY_PROJECT_ID = "rwyt-energy-forecasting"       # ← 본인 프로젝트 ID
OPENBAO_TOKEN     = "<3-3 의 OpenBao 서비스 토큰>"
```

그 아래 [파생값] 섹션은 **수정 불필요** — f-string 으로 `NAMESPACE`, `IMAGE`, `OPENBAO_NAMESPACE` 등이 자동 계산됩니다.

> **왜 DAG 는 `.env` 를 못 쓰나?** DAG 는 `airflow-dags` 저장소로 sync 되어 Airflow 스케줄러 Pod 에서 실행됩니다. 사용자의 `.env` 파일은 거기 없으므로 DAG 상단에 직접 하드코딩해야 합니다. 대신 주입되는 env 는 최소한 (`RUNWAY_PROJECT_ID`, `OPENBAO_TOKEN`, `DAG_RUN_ID`) 으로 줄어 있습니다.

> **`RUNWAY_API_KEY` 는?** 코드에서 완전히 빠졌습니다 — `task_runner.py` 와 `test_inference.py` 가 OpenBao `secret/wind-power` 의 `runway_api_key` 값을 런타임에 조회합니다 (5-4 참조).

#### ③ `.gitea/workflows/sync-dag.yml` (조직명이 다른 경우에만)

`API_BASE` 안의 조직명이 본인 Gitea 조직과 일치해야 합니다:
```yaml
# .gitea/workflows/sync-dag.yml 내 API_BASE 라인
API_BASE="https://gitea.v2.mrxrunway.ai/api/v1/repos/rwyt-energy-forecasting/airflow-dags"
#                                                   └── 여기를 본인 조직명으로
```
> 튜토리얼 예시대로 `rwyt-energy-forecasting` 조직이면 수정 불필요.
> `build-image.yml` 은 Secrets 값(`IMAGE_TAG`)을 쓰므로 워크플로우 파일 자체 수정은 불필요.

### 5-4. OpenBao 에 시크릿 등록

OpenBao 콘솔(3-3 에서 로그인한 탭) 에서:

1. 좌측 **Secret Engines** → `secret/` 클릭 → **Create secret +**
2. **Path**: `wind-power`
3. **Secret data** 에 아래 **5개** key-value 입력:

| Key | Value |
|---|---|
| `aws_access_key_id` | Runway 에서 발급받은 S3 Access Key ID |
| `aws_secret_access_key` | 위 Access Key 의 Secret |
| `gitea_username` | 4-2 의 `GIT_USERNAME` 과 동일 |
| `gitea_password` | 4-2 의 `GIT_TOKEN` 과 동일 |
| `runway_api_key` | 3-4 에서 발급받은 Keycloak offline token |

> `runway_api_key` 는 MLflow 인증(task_runner) 과 추론 엔드포인트 호출(test_inference) 에서 사용됩니다. 여기 한 곳에만 넣으면 두 스크립트 모두 자동으로 가져다 씁니다.
>
> S3 자격증명은 Runway 관리자에게 문의 또는 콘솔의 **Keys** 메뉴에서 발급.

---

## 6. 첫 Push — CI/CD 자동 트리거

이제 본인 코드를 Gitea 로 올리면 Gitea Actions 가 자동으로 이미지를 빌드하고 DAG 파일을 airflow-dags 로 동기화합니다.

```bash
cd ~/workspace/wind-power-prediction
git add .
git commit -m "feat: initial wind-power-prediction setup"
git push origin main
```

### 6-1. Gitea Actions 동작 확인

Gitea 저장소 **Actions** 탭 진입. 두 워크플로우가 실행됩니다:

- **Build and Push to Gitea CR** — Docker 이미지 빌드 & CR 푸시 (`task_runner.py`, `Dockerfile`, `requirements.txt`, `dataset/**` 변경 시)
- **Sync DAG to airflow-dags** — DAG 파일을 `rwyt-energy-forecasting/airflow-dags` 저장소의 `wind_power_prediction/v4/wind_power_prediction.py` 로 복사 (`wind_power_prediction_v4.py` 변경 시)

두 개 모두 녹색 체크로 끝날 때까지 대기 (이미지 빌드는 5-10분).

### 6-2. DAG 인식 확인

Airflow UI (`https://airflow.v2.mrxrunway.ai`) → DAG 목록에 `wind_power_prediction_v4` 가 있어야 합니다. 없으면 **Sync DAG 워크플로우** 로그 확인.

---

## 7. DAG 실행 (최초 학습)

### 옵션 A — Airflow UI (권장)

1. `wind_power_prediction_v4` DAG 클릭 → 우측 ▶ **Trigger DAG**
2. 그래프 뷰에서 태스크가 순서대로 초록색으로 바뀌는지 확인:
   ```
   ensure_pull_secret → [load_data, load_model] → train_model → evaluate_model → log_to_mlflow
   ```
3. 각 태스크 클릭 → **Logs** 탭에서 표준출력 확인 (실패 시 에러 추적)

### 옵션 B — IDE 에서 `run_dag.sh`

> ⚠️ `run_dag.sh` 상단 `API_KEY=` 는 **Airflow 전용 JWT** (Runway API 토큰과 다름, 수명 ~24h). 실행 전에 본인 값으로 교체 필수.
>
> **Airflow JWT 획득 방법** (Airflow 3.0 기준):
> ```bash
> curl -X POST "https://airflow.v2.mrxrunway.ai/auth/token" \
>   -H "Content-Type: application/json" \
>   -d '{"username":"<본인 계정>","password":"<본인 비밀번호>"}'
> ```
> 응답의 `access_token` 값을 `run_dag.sh` 의 `API_KEY` 에 붙여넣기.
>
> 또는 브라우저에서 Airflow UI 로그인 후 DevTools → Network 탭에서 API 요청의 `Authorization: Bearer <token>` 헤더를 복사.

```bash
cd ~/workspace/wind-power-prediction
# 에디터에서 run_dag.sh 의 API_KEY 변경 후
bash run_dag.sh
```

### MLflow 에서 결과 확인

`https://mlflow.v2.mrxrunway.ai` → Experiments → `rwyt-energy-forecasting.wind-power-prediction` → 최신 run 에서 파라미터/메트릭/아티팩트 확인. Registered Models 에 `rwyt-energy-forecasting.wind-power-xgboost` 가 등록되어 있어야 합니다.

---

## 8. 모델 아티팩트를 PVC 로 복사

IDE 터미널에서:

```bash
cd ~/workspace/wind-power-prediction

# 3-3, 3-4 의 토큰이 아직 환경변수에 있는지 확인
echo $OPENBAO_TOKEN | head -c 10   # 값이 보이면 OK
echo $OPENBAO_NAMESPACE            # rwyt-energy-forecasting

# 사용 가능한 모델 목록
python download_model.py --list

# 최신 모델 다운로드
python download_model.py
```

완료되면 `/mnt/models/m-xxxxxxxx.../` 에 아티팩트 복사:
```
/mnt/models/m-aa64f3852e0845838624882dfc40794b/
  ├── MLmodel
  ├── model.ubj
  ├── conda.yaml
  ├── python_env.yaml
  └── requirements.txt
```

> 이 디렉토리 전체 경로가 다음 단계의 **모델 경로** 가 됩니다. 복사해두세요.

---

## 9. 추론 엔드포인트 & 모델 배포

### 9-1. 엔드포인트 생성

1. Runway 콘솔 좌측 **추론 엔드포인트** → **+ 생성**
2. 입력:

| 필드 | 값 |
|---|---|
| 엔드포인트 이름 | `Wind Power Prediction` |
| 엔드포인트 ID | `wind-power-prediction` |
| 서빙 런타임 | `MLServer` |

> MLServer = sklearn/XGBoost/LightGBM 용. Triton = 딥러닝(PyTorch/TF/ONNX) 용. XGBoost 이므로 MLServer.

### 9-2. 첫 모델 배포 추가

엔드포인트 상세 페이지 우측 상단 **모델 배포** 클릭:

**기본 정보**

| 필드 | 값 |
|---|---|
| 이름 | `Wind Power Model v1` |
| ID | `wind-power-v1` |

**모델 소스**

| 필드 | 값 |
|---|---|
| 볼륨 | `wind-power-models` |
| 모델 경로 | `/mnt/models/m-aa64f3852e0845838624882dfc40794b` (8단계에서 복사한 디렉토리) |

**컴퓨팅 리소스**

| 필드 | 값 |
|---|---|
| CPU (millicores) | `500` |
| Memory (MiB) | `1024` |
| GPU | Off |

**스케일링**: 복제본 `1`

**생성** 클릭 → 엔드포인트가 `Healthy` 로 전환되면 서빙 준비 완료.

---

## 10. 추론 호출 테스트

### 10-1. 추론 URL 확인 (중요)

**엔드포인트 상세 페이지 > 세부 정보 > 요청 URL 을 그대로 복사**해서 사용. 형식:
```
https://inference.v2.mrxrunway.ai/api/<project-id>/<endpoint-id>/<deployment-id>/v2/models/default/infer
```

- 앞 3 경로 세그먼트 (`<project-id>/<endpoint-id>/<deployment-id>`) = **Runway 라우팅 경로** — UI 에서 본인이 설정한 엔드포인트 ID / 배포 ID 가 반영됨
- 끝 `/v2/models/default/infer` = **KServe V2 Inference Protocol 경로** — Runway MLServer 는 내부 모델명을 `default` 로 고정

`test_inference.py` 는 이 URL 을 두 부분으로 나눠 사용:

| 환경변수 | 값 |
|---|---|
| `INFERENCE_ENDPOINT` | `https://inference.v2.mrxrunway.ai/api/<project-id>/<endpoint-id>/<deployment-id>` (끝의 `/v2/...` 이전까지) |
| `DEPLOYMENT_ID` | `default` (KServe V2 의 model name, Runway 고정값) |

> ⚠️ **"배포 ID" 가 두 번 등장** — 9-2 에서 만든 배포 ID (예: `wind-power-v1`) 는 URL 3번째 세그먼트로 **이미 INFERENCE_ENDPOINT 에 포함**됩니다. `DEPLOYMENT_ID` 환경변수는 별개의 개념 (KServe 모델명) 이며 Runway 에서는 항상 `default` 입니다.

### 10-2. 인증 토큰

`Authorization: Bearer` 헤더에 **Runway API 토큰** (Keycloak offline token) 이 필요합니다. `test_inference.py` 는 다음 우선순위로 토큰을 찾습니다:

1. `--token` CLI 인자 (명시적 오버라이드)
2. env `RUNWAY_API_KEY`
3. **OpenBao 의 `runway_api_key`** (기본 — `.env` 에 `OPENBAO_TOKEN` 만 있으면 자동 조회)

5-4 단계에서 OpenBao 에 `runway_api_key` 를 등록했다면 **토큰 관련 인자를 따로 지정할 필요가 없습니다**.

### 10-3. 학습 데이터셋으로 추론 테스트 (권장)

저장소에 포함된 `test_inference.py` 가 `dataset/turbine_data.csv` 에서 행을 뽑아 `task_runner.py` 와 동일한 전처리(`id/datetime/uuid/index/wtg` 제외)를 수행한 뒤 KServe V2 payload 로 호출하고, 예측값과 실제 `activepower` 값을 나란히 출력합니다.

IDE 터미널에서:

```bash
# 저장소 루트에서 실행
cd ~/workspace/wind-power-prediction

# .env 파일에 INFERENCE_ENDPOINT 추가 (한 번만)
#   INFERENCE_ENDPOINT=<10-1 UI 요청 URL 에서 /v2/models/... 이전까지>
#   DEPLOYMENT_ID=default            # Runway MLServer 고정값

# CSV 첫 행으로 호출 — 토큰/엔드포인트 자동 로드
python test_inference.py

# 랜덤 5개 행을 한 번에 배치 호출 (MAE 자동 계산)
python test_inference.py --num-rows 5 --random

# 네트워크 호출 없이 payload JSON 만 확인 (스키마 디버깅용)
python test_inference.py --dry-run
```

출력 예:
```
[test_inference] 전체 행: 10060, 피처 수: 19
[test_inference] 선택된 행 인덱스: [0]
[test_inference] payload shape: [1, 19]
[test_inference] POST https://inference.v2.mrxrunway.ai/api/<proj>/<ep>/<deploy>/v2/models/default/infer  (verify_tls=True)
[test_inference] 예측 vs 실제:
     row |      predicted |         actual |    abs_err
-------------------------------------------------------
       0 |       362.1845 |       363.1963 |     1.0118
```

### 10-4. curl 로 직접 호출 (옵션 — 내부 페이로드 확인용)

스크립트를 쓰지 않고 페이로드를 직접 보고 싶으면 `python test_inference.py --dry-run` 출력을 복사해 curl 에 붙여넣거나 아래 형태로 보냅니다. 피처 순서는 `task_runner.py` 의 전처리와 동일(`id/datetime/uuid/index/wtg/activepower` 제외)이며 총 **19 개** 입니다.

curl 은 `.env` 를 직접 읽지 못하므로 `RUNWAY_API_KEY` 는 별도로 export 해야 합니다 (OpenBao 콘솔 또는 5-4 에서 등록한 값을 복사):

```bash
export RUNWAY_API_KEY="eyJhbGciOi..."
export INFERENCE_ENDPOINT="https://inference.v2.mrxrunway.ai/api/<proj>/<ep>/<deploy>"
export DEPLOYMENT_ID="default"          # Runway MLServer KServe V2 모델명 (고정)

curl -X POST "${INFERENCE_ENDPOINT}/v2/models/${DEPLOYMENT_ID}/infer" \
  -H "Authorization: Bearer ${RUNWAY_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "inputs": [
      {
        "name": "input-0",
        "shape": [1, 19],
        "datatype": "FP32",
        "data": [[v1, v2, ..., v19]]
      }
    ]
  }'
```

응답 예:
```json
{
  "model_name": "default",
  "outputs": [
    { "name": "output-0", "datatype": "FP32", "shape": [1, 1], "data": [362.18] }
  ]
}
```

> **환경별 검증 필요**
> - 인증 방식 (Bearer / API key header / 무인증)
> - `input-0` / `output-0` 텐서 이름 — 다르면 `test_inference.py --tensor-name <name>` 으로 재시도
> - 400/422 나면 MLServer Pod 로그(엔드포인트 상세 → ArgoCD 링크) 에서 기대하는 입력 스키마 확인

---

## 11. 정리 / 재실행

### 삭제 순서

1. **추론 엔드포인트** → 모델 배포 먼저 삭제 → 엔드포인트 삭제
2. **애플리케이션** → `Wind Power IDE` 삭제 (볼륨은 남음)
3. **스토리지** → `wind-power-models` 삭제 (재사용할 거면 유지)
4. **Gitea 저장소 / Actions Secrets / OpenBao 시크릿** 은 재사용 가능

### 재실행

같은 구성으로 다시 돌린다면 **7단계(DAG 실행)** 부터 바로 가능. 코드만 바꿀 경우 IDE 에서 수정 → `git push` → CI/CD 자동 동작 → Airflow 재실행.

---

## 트러블슈팅 포인터

| 증상 | 원인 가능성 & 대응 |
|---|---|
| Code Server 가 `Pending` 에서 안 넘어감 | 스토리지 클래스/access mode 미지원. 스토리지 목록 확인 후 클래스 변경 |
| IDE 터미널에서 `/mnt/models` 쓰기 실패 (`permission denied`) | PVC fsGroup 이슈. Code Server values 의 `podSecurityContext.fsGroup: 1000` 확인. 또는 `sudo chown -R $(id -u):$(id -g) /mnt/models` (sudo 가능 시) |
| `git clone` 인증 실패 | username 은 로그인명, password 는 **개인 액세스 토큰** (패스워드 아님). `credential.helper store` 설정 |
| Gitea Actions 에서 `build-image.yml` 이 실패 | `IMAGE_TAG` Secret 값 확인. Gitea CR 에 같은 경로로 저장됨. 401 이면 `GIT_TOKEN` 의 packages write 권한 확인 |
| Gitea Actions 에서 `sync-dag.yml` 이 `The target couldn't be found (404)` | `airflow-dags` 저장소가 빈 상태. README 자동 생성으로 초기화했는지 확인 |
| `ensure_pull_secret` 태스크 실패 | OpenBao 의 `gitea_username`/`gitea_password` 등록 여부, `OPENBAO_TOKEN` 유효성 확인 |
| `load_data` / `train_model` 등이 `FailedToRetrieveImagePullSecret` | `ensure_pull_secret` 가 만든 Secret 이 사라졌을 수 있음. DAG 재실행 (다음 run 에서 자동 복구) |
| MLflow 에서 `permission denied` | `RUNWAY_API_KEY` 가 다른 프로젝트/만료된 토큰. 3-4 재수행 후 DAG 파일 갱신 → 재 push |
| MLflow experiment 생성 `permission denied` | `EXPERIMENT_NAME` 이 `{프로젝트ID}.{실험명}` 규약 위반. `task_runner.py` 수정 |
| `download_model.py` 에서 `permission denied` (S3) | `OPENBAO_TOKEN`/`OPENBAO_NAMESPACE` 미설정 또는 만료. 3-3 재수행 |
| `run_dag.sh` 실행 시 `401 Unauthorized` | 스크립트 내 `API_KEY` 가 기본값 (수명 ~24h). 본인 Airflow JWT 로 교체 |
| 엔드포인트 생성 후 `Unhealthy` / `NotReady` | 모델 경로가 잘못됐거나 PVC 가 비어있음. 추론 엔드포인트 상세 → ArgoCD 링크로 Pod 로그 확인 |
| 추론 호출 404 | 10-1 의 UI 복사 URL + `/v2/models/<deployment-id>/infer` 조합인지 재확인 |
| 추론 호출 401/403 | 토큰을 OpenBao 토큰과 혼동했는지 확인. Runway API 토큰 재발급(3-4) |
| 추론 호출 400/422 | payload 스키마(텐서 이름/shape) 가 모델 시그너처와 불일치. MLServer 로그에서 기대 입력 확인 |

코드 레벨 설명(아키텍처/상수/인증 흐름)은 [README.md](./README.md) 를 참고하세요.
