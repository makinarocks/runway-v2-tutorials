"""
download_model.py — MLflow 모델 아티팩트를 PVC 로 복사하는 IDE 스크립트

이 파일이 무엇인가?
  DAG 가 MLflow 에 등록한 pyfunc 모델을 추론 엔드포인트에 배포하려면
  PVC 경로 (/mnt/data/models/{model-id}/) 에 아티팩트 파일들을 배치해야 한다.
  이 스크립트가 MLflow 가 저장한 S3 경로에서 해당 파일들을 복사해준다.

언제 / 어디서 실행?
  - 언제 : DAG 실행이 끝난 뒤 (log_to_mlflow 성공 이후)
  - 어디 : Code Server 터미널 (PVC 가 /mnt/data 에 마운트되어 있어야 함)

사전 준비:
  1. `.env` 에 RUNWAY_PROJECT_ID, RUNWAY_BASE_DOMAIN, OPENBAO_TOKEN 설정
  2. `source venv/bin/activate`

사용법:
    # 사용 가능한 모델 목록
    python download_model.py --list

    # 최신 모델 다운로드
    python download_model.py

    # 특정 model_id 지정
    python download_model.py --model-id m-5d03d4e8d7844c5daa32d9b2ededb9d1

결과:
    /mnt/data/models/m-{model-id}/
      ├── MLmodel
      ├── python_model.pkl   (또는 model.ubj 등)
      ├── conda.yaml
      ├── python_env.yaml
      └── requirements.txt
"""

import argparse
import os
import boto3

from config import (
    MLFLOW_S3_ENDPOINT_URL,
    S3_BUCKET,
    S3_ARTIFACT_PREFIX,
    MODEL_REGISTRY_PATH,
    load_secrets,
)


def get_s3_client(secrets: dict):
    return boto3.client(
        "s3",
        endpoint_url=MLFLOW_S3_ENDPOINT_URL,
        aws_access_key_id=secrets["aws_access_key_id"],
        aws_secret_access_key=secrets["aws_secret_access_key"],
    )


def list_models(s3):
    resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=S3_ARTIFACT_PREFIX, Delimiter="/")
    models = []
    for prefix in resp.get("CommonPrefixes", []):
        model_id = prefix["Prefix"].rstrip("/").split("/")[-1]
        models.append(model_id)
    return models


def find_latest_model(s3):
    resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=S3_ARTIFACT_PREFIX)
    objects = resp.get("Contents", [])
    if not objects:
        return None
    latest = max(objects, key=lambda o: o["LastModified"])
    parts = latest["Key"].split("/")
    model_idx = parts.index("models") + 1
    return parts[model_idx]


def download_model(model_id: str, s3):
    prefix = f"{S3_ARTIFACT_PREFIX}{model_id}/artifacts/"
    print(f"[download_model] S3 prefix: {prefix}")

    resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=prefix)
    objects = resp.get("Contents", [])

    if not objects:
        print(f"[download_model] 아티팩트를 찾을 수 없습니다: {prefix}")
        return

    print(f"[download_model] 다운로드할 파일: {len(objects)}개")

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
    parser.add_argument("--model-id", default=None, help="다운로드할 model ID (미지정 시 최신)")
    parser.add_argument("--list", action="store_true", help="모델 목록만 출력")
    args = parser.parse_args()

    secrets = load_secrets()
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
