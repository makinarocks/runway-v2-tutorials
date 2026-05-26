# Energy Demand Prediction — Runway 2.0 튜토리얼

에너지 수요량 72시간 예측 MLOps 데모. XGBoost MultiOutput 모델 학습 → MLflow 등록 → 추론 엔드포인트 배포 → GUI 데모.

## 빠른 시작

### 1. 사전 준비 — 아래 값을 미리 확보하세요

| 항목 | 어디서 발급 | 비고 |
|------|----------|------|
| **Runway 프로젝트 ID** | Runway 콘솔 프로젝트 페이지 | 예: `001-energy-pred-proj` |
| **Runway 베이스 도메인** | Runway 콘솔 리소스 현황 | 예: `try.mrxrunway.ai` |
| **Gitea 사용자명** | Gitea 로그인 시 사용하는 이름 | |
| **Gitea 액세스 토큰** | Gitea > Settings > Applications > Generate Token | Repository + Package write 권한 |
| **OpenBao 서비스 토큰** | `https://openbao.<domain>` 로그인 → Copy token | 프로젝트 네임스페이스로 로그인 |
| **Runway API 토큰** | Runway 콘솔 > 계정 설정 > 액세스 키 > API 키 | |
| **S3 Access Key / Secret Key** | Runway 콘솔 > Keys 메뉴 | |
| **PVC 볼륨 ID** | Runway 콘솔에서 생성 후 확인 | ceph-filesystem, RWX |
| **GUI 호스트명** | 원하는 서브도메인 지정 | 예: `energy-demand-demo` |

### 2. 초기화

Gitea 레포에 코드를 복사한 뒤:

```bash
cd ~/workspace/energy-demand-prediction
bash init.sh
```

대화형으로 위 값들을 입력하면:
- 코드 파일의 플레이스홀더가 본인 값으로 **일괄 치환**
- `.env` 파일 자동 생성
- `MY-STEPS.md` 생성 — **복사붙여넣기만으로 진행 가능한 가이드**

### 3. 진행

`MY-STEPS.md` 또는 `STEPS.md`를 따라 Step 5 부터 진행.

## 문서

| 파일 | 설명 |
|------|------|
| `STEPS.md` | 전체 배포 절차 (플레이스홀더 포함 — 레퍼런스용) |
| `MY-STEPS.md` | init.sh 가 생성한 복사붙여넣기용 가이드 (git 미추적) |
| `TODO.md` | 남은 작업 + 알려진 이슈 |
| `docs/*.excalidraw` | 아키텍처 다이어그램 (Excalidraw) |

## 주요 스택

XGBoost MultiOutput, Airflow (KubernetesPodOperator), MLflow, OpenBao, Gitea Actions, React GUI, Helm, nginx
