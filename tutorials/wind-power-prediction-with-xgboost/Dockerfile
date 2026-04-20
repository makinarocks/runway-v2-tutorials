# Wind Power Prediction — KubernetesPodOperator용 Docker 이미지
#
# 포함 내용:
#   - task_runner.py: 각 Airflow 태스크 로직
#   - dataset/turbine_data.csv: 학습 데이터 (load_data 태스크에서 공유 PVC로 복사)
#
# 빌드 & push:
#   docker build -t your-registry/wind-power:latest .
#   docker push your-registry/wind-power:latest

FROM python:3.11-slim

WORKDIR /app

# 레이어 캐시 최적화: 의존성을 먼저 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 태스크 실행기 복사
COPY task_runner.py .

# 학습 데이터 번들링
# - load_data 태스크에서 이 파일을 공유 PVC(/mnt/shared-workspace)로 복사한다
COPY dataset/turbine_data.csv /app/dataset/turbine_data.csv
