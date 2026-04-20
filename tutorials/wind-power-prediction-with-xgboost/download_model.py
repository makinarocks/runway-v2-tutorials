"""
S3에 저장된 MLflow 모델 아티팩트를 PVC에 다운로드하는 스크립트.

DAG 실행 완료 후 IDE에서 수동으로 실행한다.
S3 bucket에서 가장 최근 모델 아티팩트를 다운로드하거나,
특정 model_id를 지정하여 다운로드할 수 있다.

AWS 크레덴셜은 OpenBao에서 런타임에 조회한다. 사전에:
  1. OpenBao 웹 콘솔에서 secret/data/<OPENBAO_SECRET_PATH> 에
     aws_access_key_id, aws_secret_access_key 키로 저장
  2. 아래 중 하나로 Keycloak offline token 제공:
     - env var: export RUNWAY_API_KEY="eyJ..."
     - CLI 옵션: --token "eyJ..."

사용법:
    # 최신 모델 다운로드 (env var로 토큰 주입)
    export RUNWAY_API_KEY="eyJ..."
    python download_model.py

    # 특정 model_id 지정
    python download_model.py --model-id m-5d03d4e8d7844c5daa32d9b2ededb9d1

    # CLI 옵션으로 토큰 전달
    python download_model.py --token "eyJ..."
"""

import argparse
import os
import boto3

# =============================================================================
# [설정]
# =============================================================================

# S3/MinIO 설정
MLFLOW_S3_ENDPOINT_URL = "https://s3.v2.mrxrunway.ai"
S3_BUCKET = "rwyt-energy-forecasting"

# S3 내 아티팩트 경로 prefix
# 실제 구조: mlflow/experiments/{experiment_name}/models/m-{model_id}/artifacts/{파일들}
S3_ARTIFACT_PREFIX = "mlflow/experiments/wind-power-prediction/models/"

# 모델 이름 (참고용)
MODEL_NAME = "rwyt-energy-forecasting.wind-power-xgboost"

# PVC 마운트 경로 (사용자가 IDE 배포 시 지정한 경로에 맞춰 조정)
# Runway 모델 배포 UI는 /mnt/models/{model-id}/ 구조를 기대함
MODEL_REGISTRY_PATH = "/mnt/models"

# OpenBao 설정
OPENBAO_URL         = os.getenv("OPENBAO_URL", "https://openbao.v2.mrxrunway.ai")
OPENBAO_SECRET_PATH = os.getenv("OPENBAO_SECRET_PATH", "rwyt-energy-forecasting/wind-power")
OPENBAO_JWT_ROLE    = os.getenv("OPENBAO_JWT_ROLE", "runway-user")


def load_secrets(runway_api_key: str) -> dict:
    """Keycloak offline token으로 OpenBao JWT auth → KV v2에서 크레덴셜 조회."""
    import hvac
    client = hvac.Client(url=OPENBAO_URL)
    client.auth.jwt.jwt_login(role=OPENBAO_JWT_ROLE, jwt=runway_api_key)
    resp = client.secrets.kv.v2.read_secret_version(
        path=OPENBAO_SECRET_PATH,
        mount_point="secret",
    )
    data = resp["data"]["data"]
    print(f"[openbao] 크레덴셜 로드 완료: path=secret/{OPENBAO_SECRET_PATH} keys={list(data.keys())}")
    return data


def get_s3_client(secrets: dict):
    return boto3.client(
        "s3",
        endpoint_url=MLFLOW_S3_ENDPOINT_URL,
        aws_access_key_id=secrets["aws_access_key_id"],
        aws_secret_access_key=secrets["aws_secret_access_key"],
    )


def list_models(s3):
    """S3에서 사용 가능한 model_id 목록을 조회."""
    resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=S3_ARTIFACT_PREFIX, Delimiter="/")
    models = []
    for prefix in resp.get("CommonPrefixes", []):
        # mlflow/experiments/.../models/m-xxx/ → m-xxx
        model_id = prefix["Prefix"].rstrip("/").split("/")[-1]
        models.append(model_id)
    return models


def find_latest_model(s3):
    """S3에서 가장 최근에 업로드된 모델의 model_id를 반환."""
    resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=S3_ARTIFACT_PREFIX)
    objects = resp.get("Contents", [])
    if not objects:
        return None

    # 가장 최근 수정된 파일의 model_id 추출
    latest = max(objects, key=lambda o: o["LastModified"])
    # mlflow/experiments/.../models/m-xxx/artifacts/file → m-xxx
    parts = latest["Key"].split("/")
    model_idx = parts.index("models") + 1
    return parts[model_idx]


def download_model(model_id: str, s3):
    """지정한 model_id의 아티팩트를 S3에서 PVC로 다운로드."""
    prefix = f"{S3_ARTIFACT_PREFIX}{model_id}/artifacts/"
    print(f"[download_model] S3 prefix: {prefix}")

    resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=prefix)
    objects = resp.get("Contents", [])

    if not objects:
        print(f"[download_model] 아티팩트를 찾을 수 없습니다: {prefix}")
        return

    print(f"[download_model] 다운로드할 파일: {len(objects)}개")

    # PVC 저장 경로: /mnt/models/{model-id}/ 구조 (Runway 모델 배포 UI 규약)
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
    parser.add_argument("--list", action="store_true", help="사용 가능한 model ID 목록 출력")
    parser.add_argument("--token", default=None, help="Runway Keycloak offline token (미지정 시 env RUNWAY_API_KEY 사용)")
    args = parser.parse_args()

    runway_api_key = args.token or os.getenv("RUNWAY_API_KEY")
    if not runway_api_key:
        print("[download_model] RUNWAY_API_KEY 가 필요합니다. env 또는 --token 으로 전달하세요.")
        exit(1)

    secrets = load_secrets(runway_api_key)
    s3 = get_s3_client(secrets)

    if args.list:
        models = list_models(s3)
        print(f"사용 가능한 모델 ({len(models)}개):")
        for m in models:
            print(f"  {m}")
    else:
        model_id = args.model_id
        if not model_id:
            model_id = find_latest_model(s3)
            if not model_id:
                print("[download_model] S3에 모델이 없습니다.")
                exit(1)
            print(f"[download_model] 최신 모델: {model_id}")

        download_model(model_id, s3)
