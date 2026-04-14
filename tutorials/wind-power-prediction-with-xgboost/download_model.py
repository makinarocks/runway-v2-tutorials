"""
S3에 저장된 MLflow 모델 아티팩트를 PVC에 다운로드하는 스크립트.

DAG 실행 완료 후 IDE에서 수동으로 실행한다.
S3 bucket에서 가장 최근 모델 아티팩트를 다운로드하거나,
특정 model_id를 지정하여 다운로드할 수 있다.

사용법:
    # 최신 모델 다운로드
    python download_model.py

    # 특정 model_id 지정
    python download_model.py --model-id m-5d03d4e8d7844c5daa32d9b2ededb9d1
"""

import argparse
import os
import boto3

# =============================================================================
# [설정]
# =============================================================================

# S3/MinIO 설정
MLFLOW_S3_ENDPOINT_URL = "https://s3.v2.mrxrunway.ai"
AWS_ACCESS_KEY_ID = "0F9CD3FF-37B-47E064A6E18E37"
AWS_SECRET_ACCESS_KEY = "pPWjNwymzm4B52d3PrnHjR5NPaOnMYY_f2y1c22gNwU"
S3_BUCKET = "tutorial-test"

# S3 내 아티팩트 경로 prefix
# 실제 구조: mlflow/experiments/{experiment_name}/models/m-{model_id}/artifacts/{파일들}
S3_ARTIFACT_PREFIX = "mlflow/experiments/wind-power-prediction/models/"

# 모델 이름 (PVC 저장 경로에 사용)
MODEL_NAME = "tutorial-test.wind-power-xgboost"

# PVC 마운트 경로
MODEL_REGISTRY_PATH = "/mnt/model-registry"


def get_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=MLFLOW_S3_ENDPOINT_URL,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
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


def download_model(model_id: str):
    """지정한 model_id의 아티팩트를 S3에서 PVC로 다운로드."""
    s3 = get_s3_client()

    # 해당 model_id의 아티팩트 파일 목록 조회
    prefix = f"{S3_ARTIFACT_PREFIX}{model_id}/artifacts/"
    print(f"[download_model] S3 prefix: {prefix}")

    resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=prefix)
    objects = resp.get("Contents", [])

    if not objects:
        print(f"[download_model] 아티팩트를 찾을 수 없습니다: {prefix}")
        return

    print(f"[download_model] 다운로드할 파일: {len(objects)}개")

    # PVC 저장 경로 (MODEL_REGISTRY_PATH 바로 아래에 저장)
    save_dir = MODEL_REGISTRY_PATH
    os.makedirs(save_dir, exist_ok=True)
    print(f"[download_model] 저장 경로: {save_dir}")

    # 파일 다운로드
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
    args = parser.parse_args()

    s3 = get_s3_client()

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

        download_model(model_id)
