<!-- v2.2.0 에너지 수요 예측 MLOps 튜토리얼 신규 추가 | 2026-06-16 -->

# 1단계. 개발 환경 설정

이 단계에서 두 가지를 준비합니다.

1. **공용 PVC 생성** — PVC(Persistent Volume Claim)는 여러 앱이 함께 사용하는 파일 저장 공간입니다. Code Server, 학습 Pod, 추론 엔드포인트가 모두 같은 PVC를 통해 데이터셋과 모델 아티팩트(학습 결과 파일)를 주고받습니다.
2. **Code Server 카탈로그 앱 배포** — 0단계에서 등록한 시크릿이 Agent Injector를 통해 Pod에 자동 마운트되는지 실제로 검증합니다. 이 annotation 패턴이 이후 Airflow, GUI에도 동일하게 적용됩니다.

## 이 단계에서 하는 일

| 하위 페이지 | 내용 |
|------------|------|
| **1-1. PVC 생성** | 공용 ReadWriteMany PVC를 Runway 스토리지에서 생성합니다. |
| **1-2. Code Server 배포** | 카탈로그에서 Code Server를 배포하고 OpenBao annotation을 values.yaml에 설정합니다. |
| **1-3. 시크릿 주입 및 마운트 확인** | `/vault/secrets/creds.env` 파일과 PVC 마운트(`/mnt/data`)를 검증합니다. |
