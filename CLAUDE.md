# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Runway v2 튜토리얼용 샘플 코드와 데이터를 제공하는 프로젝트. Makinarocks Runway v2 AI 플랫폼에서 ML 워크플로우를 실행하는 방법을 보여주는 예제들을 포함한다.

## Architecture

각 튜토리얼은 `tutorials/<tutorial-name>/` 디렉토리에 독립적으로 구성된다.

### 공통 규칙

- 모든 튜토리얼 디렉토리에는 **`README.md`** 와 **`requirements.txt`** 가 반드시 존재해야 한다.
- 새 튜토리얼을 추가할 때는 `tutorials/_template/` 를 복사해 스캐폴드한 뒤, 루트 `README.md`의 튜토리얼 목록에 항목을 추가한다.
- 튜토리얼별 샘플 데이터는 해당 디렉토리의 `dataset/` 하위에 둔다.
- 공통 파이썬 패키지 캐시/MLflow 산출물 등은 `.gitignore` 로 제외한다.

### wind-power-prediction-with-xgboost

풍력 발전량 예측 XGBoost 모델의 학습/평가/배포 파이프라인. Airflow DAG로 구성되며, Runway v2 클러스터 내에서 실행된다.

**DAG 태스크 흐름:**
```
load_data ─┐
            ├→ train_model → evaluate_model → log_to_mlflow → save_model_to_pvc
load_model ─┘
```

- Airflow SDK (`airflow.sdk`)의 `@task` 데코레이터로 태스크 정의
- MLflow 실험 추적: Keycloak 인증 → MLflow 내부 URL 접근 (Host 헤더 패치로 DNS rebinding 우회)
- 학습된 모델을 MLflow에 등록하고, PVC (`/mnt/model-registry`)에 MLflow 형식으로 저장
- 데이터셋: 풍력 터빈 센서 데이터 (~10,000행), 타겟 변수는 `activepower`

**주요 의존성:** pandas, xgboost, scikit-learn, mlflow, airflow

## Runway v2 Infrastructure Context

- **Keycloak**: `keycloak.v2.mrxrunway.ai` — MLflow 인증용 OIDC 토큰 발급
- **MLflow**: 클러스터 내부 URL(`runway-exp-mlflow-tracking-server.runway-applications.svc.cluster.local`)로 접근하되, Host 헤더를 외부 도메인(`mlflow.v2.mrxrunway.ai`)으로 설정
- **PVC**: `/mnt/model-registry` — 모델 아티팩트 영구 저장소
