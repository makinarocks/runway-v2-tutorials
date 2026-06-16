# Energy Demand Prediction — ML task_runner 용 Docker 이미지
#
# wind-power 와 달리 데이터셋을 이미지에 번들하지 않음 — PVC 에서 읽음.
# Gitea Actions (build-ml-image.yml) 가 자동 빌드 & CR 푸시.

# Python 3.10 — MLServer(Runway)가 3.10 기반이므로 pickle 호환성을 위해 맞춤
FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY task_runner.py config.py ./

# CMD 없음 — KubernetesPodOperator 의 cmds 가 덮어씀
