"""
download_model.py — MLflow 모델 아티팩트를 PVC 로 복사하는 IDE 스크립트

이 파일이 무엇인가?
  DAG 가 MLflow 에 등록한 모델을 실제 모델 서빙에 사용하려면 Runway 모델 배포 UI
  가 기대하는 PVC 경로 (/mnt/models/{model-id}/) 에 아티팩트 파일들을 배치해야 한다.
  이 스크립트가 MLflow 가 저장한 S3 경로에서 해당 파일들을 복사해준다.

언제 / 어디서 실행?
  - 언제 : DAG 실행이 끝난 뒤 (log_to_mlflow 성공 이후)
  - 어디 : Runway 콘솔에서 배포한 IDE(VS Code Server / JupyterLab) 터미널
           → 이때 해당 IDE Pod 에 PVC 가 /mnt/models 에 마운트되어 있어야 함

사전 준비:
  1. OpenBao 웹 콘솔에 시크릿 등록 (task_runner.py 와 같은 path):
       aws_access_key_id, aws_secret_access_key
  2. OpenBao 서비스 토큰 확보 (콘솔 namespace 로그인 시 자동 발급):
       export OPENBAO_TOKEN="s.xxx..."       ← 권장 방식
       또는 CLI 옵션: --openbao-token "s.xxx..."
  3. (기본값과 다르면) OPENBAO_NAMESPACE 도 export
       export OPENBAO_NAMESPACE="<project-id>"

사용법:
    # 사용 가능한 모델 목록
    python download_model.py --list

    # 최신 모델 다운로드 (가장 최근 업로드된 m-xxx)
    python download_model.py

    # 특정 model_id 지정
    python download_model.py --model-id m-5d03d4e8d7844c5daa32d9b2ededb9d1

결과:
    /mnt/models/m-{model-id}/
      ├── MLmodel           (MLflow 메타데이터)
      ├── model.ubj         (XGBoost binary 모델)
      ├── conda.yaml
      ├── python_env.yaml
      └── requirements.txt
"""

import argparse
import os
import boto3

# =============================================================================
# [설정]
# =============================================================================

# ── S3 / MinIO ──────────────────────────────────────────────────────────────
# MLflow 서버가 모델 아티팩트를 올려두는 MinIO 엔드포인트와 bucket.
# (Runway 프로젝트 생성 시 bucket 이름은 프로젝트 ID 와 같게 자동 프로비저닝)
MLFLOW_S3_ENDPOINT_URL = "https://s3.v2.mrxrunway.ai"
S3_BUCKET = "rwyt-energy-forecasting"

# S3 내 아티팩트 경로 prefix
# 전체 구조: mlflow/experiments/{experiment_name}/models/m-{model_id}/artifacts/{파일들}
#
# ⚠️ 주의 — task_runner.py 의 EXPERIMENT_NAME 과 연동됩니다.
# task_runner.py 의 EXPERIMENT_NAME 이 "{프로젝트ID}.{실험명}" 형식이라면
# 이 경로의 "wind-power-prediction" 부분이 "{실험명}" 과 일치해야 함.
# 실험명을 바꿨다면 이 값도 함께 업데이트해야 S3 prefix 가 일치.
S3_ARTIFACT_PREFIX = "mlflow/experiments/wind-power-prediction/models/"

# 모델 이름 (참고용 — 이 스크립트는 직접 사용하지 않지만, 어느 모델을 받는지 명시)
MODEL_NAME = "rwyt-energy-forecasting.wind-power-xgboost"

# PVC 마운트 경로
# Runway 모델 배포 UI 는 /mnt/models/{model-id}/ 구조에 있는 파일을 인식한다.
# IDE 배포 시 PVC 를 이 경로에 마운트했다는 전제. 경로를 바꿨다면 이 값도 수정.
MODEL_REGISTRY_PATH = "/mnt/models"

# ── OpenBao 설정 ─────────────────────────────────────────────────────────────
# task_runner.py 의 동일 구성을 IDE 환경에서도 재사용.
# OPENBAO_TOKEN 은 env/CLI 로만 받음 (코드에 하드코딩 금지).
OPENBAO_URL         = os.getenv("OPENBAO_URL", "https://openbao.v2.mrxrunway.ai")
OPENBAO_NAMESPACE   = os.getenv("OPENBAO_NAMESPACE", "")
OPENBAO_SECRET_PATH = os.getenv("OPENBAO_SECRET_PATH", "wind-power")
OPENBAO_KV_MOUNT    = os.getenv("OPENBAO_KV_MOUNT", "secret")
# TLS 검증 정책: 기본 "true" (Runway OpenBao 공식 CA 서명 전제).
# 자체 서명 인증서 환경에서만 "false" 로 오버라이드.
OPENBAO_VERIFY_TLS  = os.getenv("OPENBAO_VERIFY_TLS", "true").lower() == "true"


def load_secrets(openbao_token: str) -> dict:
    """OpenBao 서비스 토큰으로 KV v2 시크릿을 조회한다.

    반환 dict 에서 aws_access_key_id / aws_secret_access_key 를 꺼내 S3 인증에 사용.
    토큰 잘못이면 hvac 가 permission denied 예외 발생 → 메시지 확인 후 토큰 재발급.
    """
    import hvac
    kwargs = {"url": OPENBAO_URL, "token": openbao_token, "verify": OPENBAO_VERIFY_TLS}
    if OPENBAO_NAMESPACE:
        kwargs["namespace"] = OPENBAO_NAMESPACE
    client = hvac.Client(**kwargs)
    resp = client.secrets.kv.v2.read_secret_version(
        path=OPENBAO_SECRET_PATH,
        mount_point=OPENBAO_KV_MOUNT,
    )
    data = resp["data"]["data"]
    print(f"[openbao] 크레덴셜 로드 완료: path={OPENBAO_KV_MOUNT}/{OPENBAO_SECRET_PATH} keys={list(data.keys())}")
    return data


def get_s3_client(secrets: dict):
    """OpenBao 에서 받은 키로 boto3 S3 클라이언트 생성. endpoint_url 로 MinIO 지정."""
    return boto3.client(
        "s3",
        endpoint_url=MLFLOW_S3_ENDPOINT_URL,
        aws_access_key_id=secrets["aws_access_key_id"],
        aws_secret_access_key=secrets["aws_secret_access_key"],
    )


def list_models(s3):
    """S3 에서 사용 가능한 model_id 목록을 조회.

    S3 `list_objects_v2` 에 Delimiter="/" 를 주면 prefix 에서 한 단계 아래의
    '디렉토리 같은 것'들만 CommonPrefixes 로 뽑아준다. 즉 models/ 바로 아래
    m-xxx/ 폴더들을 얻는다 (내부 파일까지 스캔하지 않음).
    """
    resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=S3_ARTIFACT_PREFIX, Delimiter="/")
    models = []
    for prefix in resp.get("CommonPrefixes", []):
        # "mlflow/experiments/.../models/m-xxx/" → "m-xxx"
        model_id = prefix["Prefix"].rstrip("/").split("/")[-1]
        models.append(model_id)
    return models


def find_latest_model(s3):
    """S3 에서 가장 최근에 업로드된 모델의 model_id 를 반환.

    모든 파일을 순회하며 LastModified 가장 큰 것의 key 에서 m-xxx 부분을 추출.
    프로덕션이라면 MLflow API 로 'latest version' 을 조회하는 게 더 정확하지만,
    튜토리얼에서는 S3 기반으로 단순화.
    """
    resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=S3_ARTIFACT_PREFIX)
    objects = resp.get("Contents", [])
    if not objects:
        return None

    # 가장 최근 수정된 파일의 key 에서 m-xxx 부분 추출
    latest = max(objects, key=lambda o: o["LastModified"])
    # "mlflow/experiments/.../models/m-xxx/artifacts/file" → "m-xxx"
    parts = latest["Key"].split("/")
    model_idx = parts.index("models") + 1
    return parts[model_idx]


def download_model(model_id: str, s3):
    """지정한 model_id 의 아티팩트를 S3 에서 PVC 로 복사.

    저장 경로는 /mnt/models/{model_id}/ 이므로 모델 배포 UI 가 바로 인식 가능.
    각 파일을 순차 다운로드 (대량 파일 아니므로 병렬화 불필요).
    """
    prefix = f"{S3_ARTIFACT_PREFIX}{model_id}/artifacts/"
    print(f"[download_model] S3 prefix: {prefix}")

    resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=prefix)
    objects = resp.get("Contents", [])

    if not objects:
        print(f"[download_model] 아티팩트를 찾을 수 없습니다: {prefix}")
        return

    print(f"[download_model] 다운로드할 파일: {len(objects)}개")

    # PVC 저장 경로: /mnt/models/{model-id}/ 구조 (Runway 모델 배포 UI 규약)
    # exist_ok=True : 이미 있는 디렉토리면 그대로 사용 (재실행 시 덮어쓰기 허용)
    save_dir = os.path.join(MODEL_REGISTRY_PATH, model_id)
    os.makedirs(save_dir, exist_ok=True)
    print(f"[download_model] 저장 경로: {save_dir}")

    for obj in objects:
        key = obj["Key"]
        filename = os.path.basename(key)
        local_path = os.path.join(save_dir, filename)
        print(f"  다운로드: {filename} ({obj['Size']} bytes)")
        s3.download_file(S3_BUCKET, key, local_path)

    print(f"[download_model] 다운로드 완료: {save_dir}")
    print(f"[download_model] 저장된 파일: {os.listdir(save_dir)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="S3에서 MLflow 모델 아티팩트를 PVC로 다운로드")
    parser.add_argument("--model-id", default=None, help="다운로드할 model ID (예: m-5d03d4e...). 미지정 시 최신 모델")
    parser.add_argument("--list", action="store_true", help="사용 가능한 model ID 목록만 출력 (다운로드 없이)")
    parser.add_argument("--openbao-token", default=None, help="OpenBao 서비스 토큰 (미지정 시 env OPENBAO_TOKEN 사용)")
    args = parser.parse_args()

    # 토큰은 CLI 인자가 env 를 덮어쓰도록 처리 (둘 다 없으면 안내하고 종료)
    openbao_token = args.openbao_token or os.getenv("OPENBAO_TOKEN")
    if not openbao_token:
        print("[download_model] OPENBAO_TOKEN 이 필요합니다. env 또는 --openbao-token 으로 전달하세요.")
        exit(1)

    # 시크릿 → S3 클라이언트 준비 (list / download 양쪽에서 공용)
    secrets = load_secrets(openbao_token)
    s3 = get_s3_client(secrets)

    if args.list:
        # 조회만. 다운로드는 하지 않음
        models = list_models(s3)
        print(f"사용 가능한 모델 ({len(models)}개):")
        for m in models:
            print(f"  {m}")
    else:
        # --model-id 지정 시 그대로 사용, 없으면 가장 최근 모델 자동 선택
        model_id = args.model_id
        if not model_id:
            model_id = find_latest_model(s3)
            if not model_id:
                print("[download_model] S3에 모델이 없습니다.")
                exit(1)
            print(f"[download_model] 최신 모델: {model_id}")

        download_model(model_id, s3)
