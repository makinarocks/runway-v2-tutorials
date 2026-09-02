# Energy Demand Prediction — KubernetesPodOperator 용 ML 이미지
#
# ## 무엇에 쓰이나
#
# Airflow DAG(`energy/dag/energy.py`)가 KubernetesPodOperator 로 이 이미지를 띄우고
# `python /app/task_runner.py --step <load_data|train_model|evaluate_model|log_to_mlflow|copy_model_to_pvc>`
# 를 실행한다. 4단계 학습 파이프라인이 모두 이 이미지 안에서 돈다.
#
# ## 이미지 내부 구성
#
#   /app/
#     ├── task_runner.py    스텝별 로직. `--step` 인자로 분기
#     ├── config.py         환경값·파생값 중앙 모듈. task_runner 가 import
#     └── requirements.txt  파이썬 의존성
#
# 학습 데이터는 **이미지에 넣지 않는다.** 공유 볼륨(`/mnt/data/dataset/`)에서 읽는다 —
# 사용자가 분기 CSV 를 추가하면 다음 DAG run 에 자동으로 반영되는 것이 튜토리얼의 핵심 흐름이라,
# 데이터를 이미지에 굽으면 그 흐름이 성립하지 않는다. (wind-power 튜토리얼은 S3 기반이라 번들한다)
#
# ## 자격 증명은 어디서 오나 (v1.4.0)
#
#   - RUNWAY_* 3개  : OpenBao Agent Injector 가 /vault/secrets/energy.env 로 마운트.
#                     config.py 가 그 파일을 직접 읽어 대문자 env 로 올린다 (`source` 불필요)
#   - AWS_* / S3_*  : 프로젝트에 자동 제공되는 `s3-rw` Secret 을 DAG 가 envFrom 으로 연결
#
# ## 빌드
#
#   빌드 컨텍스트는 이 파일이 있는 디렉터리(`tutorials/energy-demand-prediction`)다.
#   상세 절차는 가이드 부록 A 참고.
#
#     docker build -f Dockerfile.ml -t gitea.<도메인>/<네임스페이스>/energy-ml:<태그> .
#
#   ⚠️ **태그는 가이드 문서 버전에 맞춘다.** 가이드 v1.4.0 → `energy-ml:1.4.0`.
#      옛 태그는 그 버전 가이드를 보는 사용자가 계속 쓰므로 **레지스트리에서 지우지 않는다.**

# Python 3.10 slim.
#
# ⚠️ **3.10 을 벗어나면 안 된다.** 4단계에서 모델을 서빙하는 MLServer 가 Python 3.10 기반이라,
# 다른 버전으로 학습하면 cloudpickle 로 직렬화된 모델을 MLServer 가 읽지 못한다.
# 3.11 로 만들었을 때 실제로 아래 오류가 났다 (2026-09-02):
#
#     TypeError: code expected at most 16 arguments, got 18
#
# 가이드 2-3 이 Code Server 가상 환경을 3.10 으로 맞추라고 안내하는 것과 같은 이유다.
FROM python:3.10-slim

WORKDIR /app

# 레이어 캐시 최적화: 의존성이 그대로면 아래 RUN 이 캐시된다.
# task_runner.py 만 고칠 때는 pip install 을 다시 돌지 않는다.
COPY energy/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 실행 모듈. config.py 는 task_runner.py 가 import 한다.
COPY energy/task_runner.py energy/config.py ./

# ENTRYPOINT / CMD 는 의도적으로 두지 않는다 —
# KubernetesPodOperator 의 `arguments` 가 실행 명령을 덮어쓴다.
