# Runway 1.0 vs 2.0 비교

## 아키텍처 비교

| 관점 | Runway 1.0 | Runway 2.0 |
|------|-----------|-----------|
| **인증/키** | Runway API Key 1개 (플랫폼 내장) | Gitea 토큰 + OpenBao 토큰 + Runway API Key + S3 키 + Airflow JWT (5종) |
| **코드 관리** | Jupyter Notebook (콘솔 내장) | Gitea 레포 + Code Server (VS Code) + Git push |
| **파이프라인** | Runway 전용 (Builder/Trainer) | Airflow DAG (KubernetesPodOperator) |
| **파이프라인 파라미터** | 콘솔 UI 에서 직접 입력 (FEATURE_COLS, TRAIN_SET 등) | DAG 파일 상수 + env var + Airflow trigger conf |
| **이미지 빌드** | 플랫폼 자동 (사용자 개입 없음) | Gitea Actions CI/CD (사용자가 워크플로우 관리) |
| **모델 저장** | Runway 내장 저장소 | MLflow + S3 (MinIO) |
| **모델 등록** | `runway.log_model()` (1.0 SDK) | `mlflow.pyfunc.log_model()` |
| **추론 서비스** | 콘솔에서 원클릭 배포 | 추론 엔드포인트 생성 + 모델 경로 지정 + PVC 마운트 |
| **앱 배포** | 콘솔 원클릭 | Helm Chart 패키징 → Gitea 레지스트리 업로드 → 콘솔 배포 |
| **시크릿 관리** | 플랫폼 내장 (사용자 투명) | OpenBao KV v2 에 수동 등록 (5개 키) |
| **외부 접근 (라우팅)** | 플랫폼 자동 | HTTPRoute + Gateway parentRefs 설정 필요 |
| **재학습** | Runway API 호출 (원클릭) | GUI → Airflow REST API v2 DAG trigger (JWT + logical_date) |

## 튜토리얼 진행 절차 비교

| 단계 | Runway 1.0 | Runway 2.0 |
|------|-----------|-----------|
| **환경 준비** | 콘솔에서 Jupyter 실행 (1단계) | PVC 생성 + Code Server 배포 + Gitea 레포 생성 (3단계) |
| **토큰 발급** | 없음 (내장) | Gitea 토큰 + OpenBao 토큰 + Runway API Key + S3 키 (4종 발급) |
| **코드 작성** | Jupyter 노트북 1개 | task_runner.py + config.py + DAG + GUI + Dockerfile 등 10+ 파일 |
| **코드 배포** | 저장 버튼 | git push → Gitea Actions 자동 빌드 + DAG sync |
| **시크릿 등록** | 없음 | OpenBao 에 5개 key-value 수동 등록 |
| **CI/CD 설정** | 없음 | Gitea Actions Secrets 4개 등록 + 워크플로우 3개 확인 |
| **권한 설정** | 없음 | Airflow RoleBinding + Keycloak MLflow 역할 바인딩 |
| **파이프라인 실행** | 콘솔 UI 에서 Trigger | Code Server 수동 실행 또는 Airflow UI 에서 Trigger |
| **모델 배포** | 콘솔에서 모델 선택 → 배포 | download_model.py → PVC 복사 → 콘솔에서 경로 지정 |
| **앱 배포** | 콘솔 원클릭 | Helm 패키징 → Gitea 업로드 → 콘솔에서 Helm 리포지토리 연결 |
| **추론 테스트** | GUI 에서 바로 호출 | GUI API 설정 (엔드포인트 URL + 토큰) 후 호출 |
| **재학습** | GUI 버튼 → Runway API | GUI 버튼 → Airflow v2 API (JWT 토큰 + logical_date) |
| **전체 소요 시간** | ~30분 | ~2시간 |

## 2.0 에서 추가된 작업

| 작업 | 설명 | 난이도 |
|------|------|--------|
| 🔑 토큰/키 5종 발급 | Gitea, OpenBao, Runway API, S3, Airflow JWT | 중 |
| 📄 플레이스홀더 치환 (6개 파일) | DAG, nginx.conf, vite.config.js, helm values | 하 (`init.sh` 로 자동화 가능) |
| 📦 Gitea Actions Secrets 등록 | 4개 Secret (GIT_USERNAME, GIT_TOKEN, IMAGE_TAG, GUI_IMAGE_TAG) | 하 |
| 📦 OpenBao 시크릿 등록 | 5개 key-value (AWS, Gitea, Runway API) | 하 |
| 📦 Helm Chart 패키징 + 업로드 | helm package → curl 로 Gitea 레지스트리 업로드 | 중 |
| 🔧 HTTPRoute parentRefs 설정 | Gateway 바인딩 (platform-core-gateway) — 미문서화 | 상 |
| 🔧 Keycloak MLflow 역할 바인딩 | 프로젝트별 mlflow:p:xxx:admin 역할 할당 필요 | 상 |
| 🔧 Airflow RoleBinding | airflow-scheduler SA 에 edit 권한 부여 | 중 |
| ⚠️ OpenBao 토큰 갱신 | 짧은 만료 주기 → DAG 상수 수정 + push 반복 | 고통 |
| ⚠️ Python 버전 맞추기 | MLServer 3.10 ↔ pickle 호환 필요 | 중 |

## 2.0 에서 개선된 점

| 개선 | 설명 |
|------|------|
| ✅ Git 기반 코드 관리 | 버전 관리, 코드 리뷰, 브랜치 전략 가능 |
| ✅ CI/CD 자동화 | 코드 push 시 이미지 빌드 + DAG sync 자동 |
| ✅ Airflow DAG 표준화 | KubernetesPodOperator 로 태스크별 리소스 격리 |
| ✅ MLflow 실험 추적 | 모델 버전 관리 + 메트릭 비교 + Model Registry |
| ✅ 오픈소스 기반 | Airflow, MLflow, XGBoost — vendor lock-in 감소 |
| ✅ GUI 독립 배포 | React 앱을 Helm Chart 로 자유롭게 배포/업데이트 |
| ✅ 재학습 파라미터 제어 | DAG conf 로 학습 파일 지정 (Q1만 vs Q1+Q2+Q3) |
| ✅ 인프라 투명성 | K8s, S3, Gateway 등 플랫폼 내부가 보임 → 디버깅 용이 |
