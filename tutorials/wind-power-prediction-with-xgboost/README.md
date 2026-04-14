# Wind Power Prediction with XGBoost

풍력 터빈 센서 데이터를 활용해 발전량(`activepower`)을 예측하는 XGBoost 모델의 학습/평가/배포 파이프라인 튜토리얼입니다.

## 개요

- **목적**: Runway v2에서 Airflow DAG 기반 ML 파이프라인 구성 및 MLflow 실험 추적/모델 레지스트리 연동 방법 학습
- **난이도**: Intermediate
- **주요 기술 스택**: XGBoost, Apache Airflow, MLflow, scikit-learn

## 사전 요구사항

- Runway v2 클러스터 접근 권한
- MLflow / Keycloak 접근 가능한 사용자 계정
- PVC `/mnt/model-registry` 마운트

## 디렉토리 구성

```
wind-power-prediction-with-xgboost/
├── README.md
├── requirements.txt
├── wind_power_prediction.py   # Airflow DAG 정의
├── download_model.py          # MLflow에서 모델 내려받기
├── run_dag.sh                 # DAG 실행 스크립트
├── tutorial_source.zip        # 튜토리얼 소스 번들
└── dataset/
    └── turbine_data.csv       # 풍력 터빈 센서 데이터 (~10,000행)
```

## 실행 방법

```bash
pip install -r requirements.txt
bash run_dag.sh
```

## DAG 태스크 흐름

```
load_data ─┐
            ├→ train_model → evaluate_model → log_to_mlflow → save_model_to_pvc
load_model ─┘
```

- Airflow SDK (`airflow.sdk`)의 `@task` 데코레이터로 태스크 정의
- MLflow 실험 추적: Keycloak OIDC 토큰 인증 → 클러스터 내부 URL 접근 (Host 헤더를 `mlflow.v2.mrxrunway.ai` 로 패치하여 DNS rebinding 우회)
- 학습된 모델을 MLflow 레지스트리에 등록하고, PVC (`/mnt/model-registry`)에 MLflow 형식으로 저장

## 데이터셋

- 위치: `dataset/turbine_data.csv`
- 크기: ~10,000 행
- 타겟 변수: `activepower`
