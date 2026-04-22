# Wind Power Prediction — KubernetesPodOperator 용 Docker 이미지
#
# 어떻게 빌드되는가?
#   보통 손으로 빌드하지 않습니다. Gitea Actions workflow(.gitea/workflows/build-image.yml)가
#   task_runner.py / config.py / Dockerfile / requirements.txt / dataset/** 변경 시
#   자동으로 빌드해서 Gitea Container Registry 에 :latest 태그로 푸시합니다.
#
# 이미지 내부 구성:
#   /app/
#     ├── task_runner.py          # Airflow 태스크 로직 (--step 인자로 단계 실행)
#     ├── config.py               # 환경/파생값 중앙 모듈 (task_runner 가 import)
#     ├── requirements.txt        # 파이썬 의존성 (pandas/xgboost/mlflow/boto3/hvac 등)
#     └── dataset/turbine_data.csv   # 학습 데이터 번들 (load_data 태스크가 S3 로 업로드)
#
# 실행 시점:
#   KubernetesPodOperator 가 이 이미지로 Pod 를 띄우고
#   `python task_runner.py --step <load_data|load_model|...>` 명령을 실행합니다.
#
# 수동 빌드 (CI 없이 테스트할 때):
#   docker build -t gitea.v2.mrxrunway.ai/<org>/wind-power-prediction:latest .
#   docker push gitea.v2.mrxrunway.ai/<org>/wind-power-prediction:latest

# Python 3.11 slim — 런타임 이미지 크기 최소화용 변종
FROM python:3.11-slim

WORKDIR /app

# 레이어 캐시 최적화: 의존성 파일이 바뀌지 않으면 아래 RUN 레이어가 캐시됨
# task_runner.py 만 바꿀 때는 pip install 재실행 없이 다음 COPY 레이어만 갱신
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 태스크 실행기 + 설정 모듈 복사 (컨테이너 실행 시 entrypoint 로 호출됨)
# config.py 는 task_runner.py 가 import. 이 이미지에서는 .env 파일이 없지만
# config 내부의 load_dotenv() 는 조용히 no-op 이라 Pod env 만으로 동작함.
COPY task_runner.py config.py ./

# 학습 데이터 번들링
# load_data 태스크가 /app/dataset/turbine_data.csv 를 읽어서 S3 로 업로드한다.
# (v1 에서는 공유 PVC 를 썼지만 v4 는 S3 기반 — 이 CSV 도 S3 로 올라간 뒤 다른 태스크가 받아감)
COPY dataset/turbine_data.csv /app/dataset/turbine_data.csv

# CMD / ENTRYPOINT 는 의도적으로 없음 —
# KubernetesPodOperator 의 `cmds=["python", "task_runner.py", "--step", <name>]` 가 덮어씀
