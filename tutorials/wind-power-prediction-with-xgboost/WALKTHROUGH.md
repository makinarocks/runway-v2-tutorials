# End-to-End Walkthrough — Runway IDE 로 풍력 발전량 예측 모델 배포까지

이 문서는 **Runway 콘솔 UI 를 처음 써보는 사용자** 가 풍력 발전량 예측 튜토리얼을 **볼륨 생성 → IDE 배포 → DAG 실행 → 모델 다운로드 → 추론 엔드포인트 배포 → 추론 호출** 까지 한 번에 따라할 수 있도록 작성된 단계별 가이드입니다.

> 이 가이드는 [README.md](./README.md) 의 **사전 요구사항** 이 이미 충족되어 있다고 가정합니다(RoleBinding, OpenBao 시크릿, Gitea Actions secrets, Runway 프로젝트 등). 아직 안 됐다면 README 의 "사전 준비" 섹션을 먼저 완료해 주세요.

---

## 0. 체크리스트 — 시작 전에

| 항목 | 완료 여부 | 참고 |
|---|---|---|
| Runway 콘솔 접속 가능 (Google SSO) | ☐ | `https://runway.v2.mrxrunway.ai` |
| 대상 워크스페이스 / 프로젝트 존재 | ☐ | 본 가이드 예시: workspace `Runway 2.0 Tutorials` / project `energy-forecasting` |
| K8s namespace 에 `airflow-scheduler` SA RoleBinding 완료 | ☐ | [README § 사전 준비 3](./README.md#3-airflow-scheduler-sa에-rolebinding-생성) |
| OpenBao KV v2 에 `aws_access_key_id`, `aws_secret_access_key`, `gitea_username`, `gitea_password` 등록 | ☐ | [README § 사전 준비 4](./README.md#4-openbao-시크릿-등록) |
| Gitea 저장소 `wind-power-prediction`, `airflow-dags` 생성 + Actions Secrets 등록 | ☐ | [README § 사전 준비 2](./README.md#2-gitea-저장소-준비) |
| DAG 상수 수정 & push 완료 (이미지 빌드 & DAG sync 워크플로우 1회 이상 성공) | ☐ | [README § 배포 & 실행](./README.md#배포--실행) |

체크리스트를 다 통과해야 아래 단계가 정상 동작합니다.

---

## 1. 볼륨(PVC) 생성

학습된 모델 아티팩트를 S3 에서 복사해둘 영구 저장소를 만듭니다. 이 볼륨은 **IDE 에서는 `/mnt/models` 에 마운트**되고, **추론 엔드포인트 배포 시에도 같은 볼륨을 가리켜 모델 파일을 서빙**합니다.

### UI 경로

1. Runway 콘솔 로그인 → 워크스페이스 `Runway 2.0 Tutorials` 선택 → 프로젝트 `energy-forecasting` 클릭
2. 좌측 네비게이션 **스토리지** 메뉴로 이동
3. 우측 상단 **+ 생성** 클릭

### 입력 값

| 필드 | 값 | 이유 |
|---|---|---|
| 볼륨 ID | `wind-power-models` | 영문소문자-하이픈. 최대 63자 |
| 스토리지 클래스 | `ceph-filesystem` | RWX 를 지원하는 file system 기반 클래스 (ceph-block 은 RWO 만 지원) |
| 접근 모드 | `ReadWriteMany` | IDE + 추론 Pod 가 동시에 같은 볼륨을 읽기 위함 |
| 크기 | `5` (GiB) | XGBoost 모델 여러 버전을 보관해도 충분. 최대 25 GiB |

**생성** 버튼 클릭 → 스토리지 목록에 `wind-power-models` 가 `Bound` 상태로 표시되면 성공.

---

## 2. Code Server IDE 배포 (PVC 마운트)

`download_model.py` 스크립트를 돌리기 위한 브라우저 IDE 를 띄웁니다. 방금 만든 볼륨을 `/mnt/models` 에 마운트합니다.

### UI 경로

1. 좌측 **카탈로그** 메뉴
2. **Code server** 카드 클릭 → 우측 상단 **+ 애플리케이션 생성** 클릭
3. 다이얼로그에 아래 값 입력

### 기본 정보

| 필드 | 값 |
|---|---|
| 이름 | `Wind Power IDE` |
| ID | `wind-power-ide` |
| 설명 | (선택) `모델 다운로드용 VS Code` |

### Helm values.yaml 수정

기본 템플릿에서 아래 두 군데를 수정하세요:

**① `persistence` 섹션을 기존 PVC 재사용으로 변경**
```yaml
persistence:
  enabled: true
  mountPath: /mnt/models
  existingClaim: wind-power-models   # ← 1단계에서 만든 볼륨 ID
  # 아래 네 줄은 existingClaim 사용 시 무시되지만 주석 해제하지 않아도 됨
  # accessMode: ReadWriteMany
  # storageClassName: ""
  # size: 5Gi
```

**② `httpRoute.hostname` 을 본인 sub-domain 으로 변경**
```yaml
httpRoute:
  enabled: true
  hostname: "wind-power-ide.v2.mrxrunway.ai"   # ← 원하는 서브도메인
  hostnames: []
```

나머지(image, resources 등)는 기본값 유지.

### 생성 & 접속

**생성** 클릭 → **애플리케이션** 메뉴로 이동 → 카드 상태가 `Healthy` 로 바뀔 때까지 1-2분 대기 → 카드 클릭 → 상단의 접속 URL(또는 httpRoute.hostname) 로 진입 → **Code Server (VS Code) 브라우저 IDE** 가 열립니다.

---

## 3. IDE 에서 저장소 clone + 스크립트 실행 준비

Code Server 터미널을 엽니다 (상단 메뉴 `Terminal > New Terminal` 또는 `Ctrl+``).

### 3-1. Gitea 저장소 clone & 자격증명 저장

```bash
cd /mnt/models            # 볼륨이 마운트된 경로 — 여기서 작업하면 파일이 영구 보존됨

# credential helper 를 켜두면 한 번만 입력하고 이후 재사용됨 (/home/coder 도 PVC 에 속함)
git config --global credential.helper store

git clone https://gitea.v2.mrxrunway.ai/rwyt-energy-forecasting/wind-power-prediction.git
cd wind-power-prediction
```

> Gitea private 저장소라 username/password 를 물어봅니다. **username 에 본인 Gitea 계정, password 에 개인 접근 토큰**(패스워드 아님) 을 입력하세요. 2단계 인증을 쓰는 경우 비밀번호는 안 먹을 수 있어요.

### 3-2. 필요한 파이썬 패키지 설치

Code Server 이미지에는 `pip` 가 있지만 `boto3` / `hvac` 가 없을 수 있습니다:

```bash
# 시스템 파이썬에 설치 (Code Server 기본 사용자는 root 권한을 가짐)
pip install boto3 hvac
```

권한 오류가 나면 `--user` 옵션 또는 venv 를 사용하세요:

```bash
# 대안 1: 사용자 디렉토리에 설치 (PATH 에 ~/.local/bin 포함 필요)
pip install --user boto3 hvac
export PATH="$HOME/.local/bin:$PATH"

# 대안 2: venv 사용 (권장)
python -m venv .venv && source .venv/bin/activate
pip install boto3 hvac
```

### 3-3. OpenBao 서비스 토큰 확보 (S3 자격증명용)

`download_model.py` 는 AWS 키를 OpenBao 에서 읽어오므로 **OpenBao 서비스 토큰** 이 필요합니다.

1. 새 브라우저 탭에서 `https://openbao.v2.mrxrunway.ai` 접속
2. 프로젝트 namespace (`rwyt-energy-forecasting`) 로 로그인하면 자동 발급되는 서비스 토큰을 **Copy token** 으로 복사 (콘솔에서 복사한 값 그대로 사용)
3. IDE 터미널에서 환경변수로 설정:
   ```bash
   export OPENBAO_TOKEN="<복사한 값 그대로>"
   export OPENBAO_NAMESPACE="rwyt-energy-forecasting"
   ```

> 토큰은 세션이 끊기면 만료됩니다. 실행 직전에 새로 복사하세요.

### 3-4. Runway API 토큰 확보 (MLflow 및 추론 호출용)

**OpenBao 서비스 토큰과 별개**로, **Runway API 토큰(Keycloak offline token)** 도 필요합니다. 이 토큰은:
- DAG 가 MLflow 에 접근할 때 사용됩니다 (`wind_power_prediction_v4.py` 의 `RUNWAY_API_KEY` 상수)
- 7단계 추론 호출 시 `Authorization: Bearer` 헤더 값으로도 사용됩니다

발급 경로:
1. Runway 콘솔 우측 상단 **프로필 아이콘** (이니셜) 클릭
2. **API 토큰** 메뉴 (또는 **사용자 설정 > API 토큰**) 진입
3. **새 토큰 발급** → 복사해서 안전한 곳에 저장

> 이 토큰은 **세션별 offline token** 이라, 같은 사용자가 콘솔에서 새 토큰을 발급받으면 이전 토큰이 무효화됩니다 (README §트러블슈팅 참고). 4단계 이후 재발급했다면 DAG 파일의 `RUNWAY_API_KEY` 상수도 갱신 필요.

---

## 4. DAG 실행 (최초 학습)

실제 학습을 돌려 MLflow 에 모델을 등록합니다. DAG 는 Gitea push 로 이미 동기화되어 있으므로 **Airflow UI 에서 수동 trigger** 또는 IDE 에서 `run_dag.sh` 실행 중 선택하면 됩니다.

### 옵션 A — Airflow UI

1. `https://airflow.v2.mrxrunway.ai` 접속
2. DAG 목록에서 `wind_power_prediction_v4` 클릭
3. 우측 ▶ 버튼으로 `Trigger DAG`
4. 각 태스크가 순서대로 초록색으로 바뀌는지 확인 (`ensure_pull_secret → [load_data, load_model] → train_model → evaluate_model → log_to_mlflow`)

### 옵션 B — `run_dag.sh` 스크립트

> ⚠️ `run_dag.sh` 상단의 `API_KEY=` 는 **Airflow 전용 JWT** (Keycloak offline token 과 다름, 수명 ~24h) 입니다. 실행 전에 본인의 값으로 교체해야 합니다.
>
> - 방법 1: Airflow UI (`https://airflow.v2.mrxrunway.ai`) 로그인 후 브라우저 DevTools → Application → Cookies 또는 Local Storage 에서 JWT 추출
> - 방법 2: Airflow UI **Security > List Users > Generate Token** (버전에 따라 다름)
>
> 교체 없이 실행하면 401 Unauthorized 가 납니다.

IDE 터미널에서:
```bash
cd /mnt/models/wind-power-prediction
# 파일 편집해서 API_KEY 값 교체 후 실행
bash run_dag.sh
```

스크립트가 REST API 로 DAG 를 trigger 하고 10초 간격으로 각 태스크 상태를 출력합니다. 성공하면 `=== 최종 상태: success ===` 로 끝나고 모델 다운로드 안내가 나옵니다.

### MLflow 에서 결과 확인

`https://mlflow.v2.mrxrunway.ai` → Experiments → `rwyt-energy-forecasting.wind-power-prediction` → 최신 run 에서 metrics/params/artifacts 확인. Registered Models 에 `rwyt-energy-forecasting.wind-power-xgboost` 가 등록되어 있어야 합니다.

---

## 5. 모델 아티팩트를 PVC 로 복사

IDE 터미널에서:

```bash
cd /mnt/models/wind-power-prediction

# 사용 가능한 모델 목록 조회
python download_model.py --list
# → 사용 가능한 모델 (1개):
#     m-aa64f3852e0845838624882dfc40794b

# 최신 모델 다운로드 (가장 최근 업로드된 m-xxx 자동 선택)
python download_model.py
```

완료되면 `/mnt/models/m-aa64f.../` 경로에 아래 파일들이 복사됩니다:
```
/mnt/models/m-aa64f3852e0845838624882dfc40794b/
  ├── MLmodel           # MLflow 메타
  ├── model.ubj         # XGBoost 모델 바이너리
  ├── conda.yaml
  ├── python_env.yaml
  └── requirements.txt
```

특정 모델을 지정하려면:
```bash
python download_model.py --model-id m-aa64f3852e0845838624882dfc40794b
```

> **중요**: 이 디렉토리 경로(`/mnt/models/{model-id}/`)가 다음 단계(모델 배포) 에서 "모델 경로" 로 지정됩니다.

---

## 6. 추론 엔드포인트 & 모델 배포 생성

Runway 의 "추론 엔드포인트" 는 **한 URL 안에서 여러 모델 배포 간 트래픽을 분배** 할 수 있는 구조입니다. 엔드포인트 1개 → 모델 배포 N개 (A/B 테스트, 카나리아 배포).

### 6-1. 엔드포인트 생성

1. 좌측 **추론 엔드포인트** 메뉴
2. 우측 상단 **+ 생성**
3. 입력:

| 필드 | 값 |
|---|---|
| 엔드포인트 이름 | `Wind Power Prediction` |
| 엔드포인트 ID | `wind-power-prediction` (URL 에 쓰임) |
| 서빙 런타임 | `MLServer` (XGBoost 지원) |

> **서빙 런타임 선택 가이드**
> - **MLServer** : sklearn / XGBoost / LightGBM 등 전통 ML 모델
> - **Triton Inference Server** : PyTorch / TF / ONNX 등 딥러닝 & 고성능 inference
>
> 본 튜토리얼의 XGBoost 모델은 **MLServer** 가 맞습니다.

### 6-2. 첫 모델 배포 추가

엔드포인트 생성 완료 후 엔드포인트 상세 페이지에서 우측 상단 **모델 배포** 클릭. 다이얼로그에 입력:

**기본 정보**
| 필드 | 값 |
|---|---|
| 이름 | `Wind Power Model v1` |
| ID | `wind-power-v1` |
| 설명 | (선택) MLflow run `xgboost-2026xxxx...` 기반 |

**모델 소스**
| 필드 | 값 |
|---|---|
| 볼륨 | `wind-power-models` (1단계에서 만든 PVC) |
| 모델 경로 | `/mnt/models/m-aa64f3852e0845838624882dfc40794b` (5단계에서 내려받은 모델 디렉토리 절대 경로) |

**컴퓨팅 리소스**
| 필드 | 값 |
|---|---|
| CPU (millicores) | `500` |
| Memory (MiB) | `1024` |
| GPU 가속화 | Off |

**스케일링**
| 필드 | 값 |
|---|---|
| 복제본 | `1` |

**트래픽 설정**: 이 첫 배포는 자동으로 `1 (100%)` 할당됩니다.

**생성** 클릭 → 엔드포인트 상세의 **모델 배포** 목록에 추가되고, 엔드포인트 상태가 **Healthy** 로 전환되면 서빙 준비 완료.

---

## 7. 추론 호출 테스트

### 7-1. 추론 URL 확인 (매우 중요)

**반드시 엔드포인트 상세 페이지 우측 "세부 정보" 섹션의 "추론 URL" 필드 값을 그대로 복사**해서 사용하세요. 환경마다 도메인이 다를 수 있으므로 아래 예시를 그대로 쓰면 404 가 날 수 있습니다.

참고용 형식 (실제 값은 UI 에서 확인):
```
https://inference.v2.mrxrunway.ai/api/<project-id>/<endpoint-id>
```

MLServer 는 **KServe V2 Inference Protocol** 을 따르므로 실제 호출 경로는 **복사한 URL 뒤에** `/v2/models/{deployment-id}/infer` 를 붙입니다 (`{deployment-id}` 는 6-2 에서 지정한 모델 배포 ID, 예시는 `wind-power-v1`).

### 7-2. 인증 토큰

3-4 단계에서 확보한 **Runway API 토큰** (코드의 `RUNWAY_API_KEY`) 을 `Authorization: Bearer` 헤더로 전달합니다. OpenBao 서비스 토큰(3-3) 과 다른 토큰이니 혼동 주의.

### 7-3. 추론 요청 예시

turbine_data 컬럼에 맞춰 입력(예: `windspeed`, `winddirection`, `blade_pitch` 등) 을 구성합니다. `task_runner.py` 의 `drop_cols = ["id", "datetime", "uuid", "index", "wtg"]` 를 제외한 피처들이 X 입니다.

```bash
TOKEN="eyJhbGciOi..."        # 3-4 단계의 Runway API 토큰 (RUNWAY_API_KEY 와 동일)
ENDPOINT="<7-1 UI 에서 복사한 추론 URL>"
DEPLOYMENT="wind-power-v1"   # 6-2 에서 만든 배포 ID

curl -X POST "${ENDPOINT}/v2/models/${DEPLOYMENT}/infer" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "inputs": [
      {
        "name": "input-0",
        "shape": [1, N],
        "datatype": "FP32",
        "data": [[v1, v2, v3, ..., vN]]
      }
    ]
  }'
```

응답 예시 (KServe V2 포맷):
```json
{
  "model_name": "wind-power-v1",
  "outputs": [
    {
      "name": "output-0",
      "datatype": "FP32",
      "shape": [1, 1],
      "data": [1234.56]
    }
  ]
}
```

> **검증 필요 사항** (환경마다 다를 수 있음)
> - 인증 방식 (Bearer / API key header / 무인증)
> - `input-0` / `output-0` 텐서 이름 (MLServer 가 모델 MLmodel 시그너처에서 자동 추론)
> - feature 개수 N 및 입력 순서
>
> 첫 호출 시 400/422 오류가 나면 MLServer 로그(Argo CD 에서 Pod 로그 확인) 또는 [MLServer 문서](https://mlserver.readthedocs.io) 참고.

---

## 8. 정리 / 오프보딩

튜토리얼을 마쳤다면 리소스를 정리합니다 (과금/쿼터 절약).

### 삭제 순서 (의존성 순)

1. **추론 엔드포인트** → 개별 모델 배포 삭제 → 엔드포인트 삭제
2. **애플리케이션** → `Wind Power IDE` 삭제 (볼륨은 남음)
3. **스토리지** → `wind-power-models` 삭제 (또는 다음 실험에 재사용하려면 유지)
4. **Gitea 저장소 Actions Secrets / K8s RoleBinding** 은 다른 튜토리얼/실험에 재사용 가능하므로 유지 권장
5. **OpenBao KV 시크릿** 도 동일 — 재사용 권장

### 재실행 시

이미 모든 사전 준비가 되어 있으므로 **4단계(DAG 실행)** 부터 바로 시작하면 됩니다.

---

## 트러블슈팅 포인터

| 증상 | 원인 가능성 & 대응 |
|---|---|
| Code Server 가 `Pending` 상태에서 넘어가지 않음 | 스토리지 클래스/access mode 가 클러스터에서 지원 안 됨. 스토리지 목록에서 볼륨 상태가 `Pending` 이면 스토리지 클래스 변경 필요 |
| IDE 터미널에서 `/mnt/models` 에 쓰기 실패 (`permission denied`) | PVC 가 root 소유로 마운트됐는데 code-server 가 uid=1000 으로 실행 중. Code Server 배포 values 에 `podSecurityContext.fsGroup: 1000` 이 설정됐는지 확인. 또는 IDE 터미널에서 `sudo chown -R $(id -u):$(id -g) /mnt/models` (sudo 가능한 경우) |
| IDE 에서 `git clone` 인증 실패 | Gitea username 에는 로그인명, password 란에는 **개인 액세스 토큰** 사용. 재입력이 잦으면 `git config --global credential.helper store` 로 캐시 |
| `download_model.py` 에서 `permission denied` (S3) | `OPENBAO_NAMESPACE` 환경변수 누락 또는 토큰 만료. 3-3 재수행 |
| `run_dag.sh` 실행 시 `401 Unauthorized` | 스크립트 내 `API_KEY` 가 기본값으로 남아있음 (수명 ~24h). 본인의 Airflow JWT 로 교체 |
| 엔드포인트 생성 후 `Unhealthy` / `NotReady` | 모델 경로가 잘못됐거나 PVC 가 비어있음. ArgoCD 링크로 Pod 로그 확인 |
| 추론 호출 시 404 | 7-1 에서 복사한 URL 이 아니라 문서 예시를 그대로 쓴 경우가 잦음. 엔드포인트 상세 UI 의 "추론 URL" 필드 값 + `/v2/models/{deployment-id}/infer` 조합인지 재확인 |
| 추론 호출 시 401/403 | 토큰 형식/헤더명 재확인. OpenBao 토큰과 혼동했는지 확인 (3-3 vs 3-4). 필요 시 Runway UI 에서 **API 토큰** 재발급 |
| 추론 호출 시 400/422 | payload 스키마(텐서 이름/shape) 가 모델 시그너처와 불일치. MLServer Pod 로그(ArgoCD 또는 애플리케이션 상세에서 접근) 에서 기대하는 입력 스키마 확인 |

코드 레벨 문제(태스크 실패 등) 는 [README § 트러블슈팅](./README.md#트러블슈팅) 을 참고하세요.
