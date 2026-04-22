"""
test_inference.py — 학습 데이터셋으로 배포된 모델의 추론 엔드포인트를 호출해보는 스크립트

이 파일이 무엇인가?
  Runway 모델 배포 UI 로 띄운 추론 엔드포인트가 실제로 잘 동작하는지, 학습에 쓴
  `dataset/turbine_data.csv` 에서 몇 행을 뽑아 KServe V2 Inference Protocol
  payload 로 변환 → POST 요청을 보내고, 모델 예측값과 실제 activepower 값을
  비교해본다.

  task_runner.py 의 전처리(`id/datetime/uuid/index/wtg` 제외)와 동일한 피처 순서를
  그대로 따르므로, 학습 시 feature layout 과 어긋나지 않는다.

언제 / 어디서 실행?
  - 언제 : WALKTHROUGH 9 단계 (모델 배포) 완료 후, 10 단계 추론 테스트에서
  - 어디 : IDE 터미널, 또는 엔드포인트에 접근 가능한 로컬 머신 어디서든

사전 준비:
  1. requirements.txt 에 포함된 `pandas`, `requests` 가 설치돼 있어야 함
     (IDE 환경에는 이미 깔려 있음. 로컬이라면 `pip install pandas requests`)
  2. 환경변수 또는 CLI 인자로 다음을 전달:
       RUNWAY_API_KEY       : WALKTHROUGH 3-4 의 Runway API 토큰 (Bearer 헤더용)
       INFERENCE_ENDPOINT   : WALKTHROUGH 10-1 에서 복사한 '추론 URL'
                              예: https://inference.v2.mrxrunway.ai/api/<proj>/<ep>
       DEPLOYMENT_ID        : 모델 배포 ID (WALKTHROUGH 9-2 의 `wind-power-v1`)

사용법:
    # 가장 많이 쓰는 형태: CSV 첫 행으로 호출
    python test_inference.py \
        --endpoint https://inference.v2.mrxrunway.ai/api/<proj>/<ep> \
        --deployment wind-power-v1 \
        --token "$RUNWAY_API_KEY"

    # 랜덤 5개 행으로 배치 호출
    python test_inference.py --num-rows 5 --random

    # 네트워크 호출 없이 payload JSON 만 출력 (디버깅)
    python test_inference.py --dry-run

출력:
    - 전송한 payload (shape, 샘플 값)
    - 모델 응답의 예측값 vs 데이터셋 실제 activepower 값 비교
    - MAE (여러 행일 때)
"""

import argparse
import json
import os
import sys

import pandas as pd
import requests

# =============================================================================
# [설정] task_runner.py 와 동일한 전처리 규약
# =============================================================================

# 학습 시 제외한 식별자/메타 컬럼 (task_runner.py:268 과 동일)
DROP_COLS = ["id", "datetime", "uuid", "index", "wtg"]

# 타겟 컬럼 — payload 에 포함하지 않고 예측값과 비교용으로 사용
TARGET_COL = "activepower"

# CSV 경로 기본값 — IDE 에서 저장소 루트에서 실행한다고 가정
DEFAULT_CSV = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "dataset", "turbine_data.csv"
)

# MLServer 가 MLmodel signature 에서 자동 추론하는 입력/출력 텐서 이름
# (모델 시그너처에 따라 다를 수 있음 — 다르면 --tensor-name 으로 덮어쓰기)
DEFAULT_INPUT_TENSOR = "input-0"


def build_payload(feature_df: pd.DataFrame, tensor_name: str) -> dict:
    """DataFrame → KServe V2 Inference Protocol JSON payload.

    shape = [n_rows, n_features], datatype = FP32 로 고정. XGBoost 회귀 모델은
    모든 입력을 float 로 받으므로 float32 변환이 안전 (int 컬럼도 처리됨).
    """
    n_rows, n_features = feature_df.shape
    return {
        "inputs": [
            {
                "name": tensor_name,
                "shape": [n_rows, n_features],
                "datatype": "FP32",
                # nested list 로 전송 — MLServer 가 shape 대로 reshape
                "data": feature_df.astype("float32").values.tolist(),
            }
        ]
    }


def extract_predictions(resp_json: dict) -> list:
    """응답 JSON 에서 예측값 리스트 꺼내기.

    MLServer 는 shape=[n,1] 을 일반적으로 1D flat list [p1, p2, ...] 로 돌려주지만,
    일부 설정에서는 nested list [[p1], [p2], ...] 로 내려오기도 한다. 둘 다
    처리하도록 한 단계 flatten 을 수행한다. 출력 텐서 이름은 모델별로 다를 수
    있으므로 첫 번째 output 을 그대로 사용.
    """
    outputs = resp_json.get("outputs", [])
    if not outputs:
        raise RuntimeError(f"응답에 outputs 가 비어있음: {resp_json}")
    data = outputs[0].get("data", [])
    if data and isinstance(data[0], list):
        data = [row[0] for row in data]
    return data


def main():
    parser = argparse.ArgumentParser(
        description="배포된 모델 추론 엔드포인트를 학습 데이터셋으로 테스트"
    )
    parser.add_argument("--csv", default=DEFAULT_CSV,
                        help=f"테스트 데이터 CSV 경로 (기본: {DEFAULT_CSV})")
    parser.add_argument("--num-rows", type=int, default=1,
                        help="추론 요청에 포함할 행 수 (기본 1)")
    parser.add_argument("--row-index", type=int, default=0,
                        help="--random 이 아닐 때 시작 행 (기본 0, 즉 처음부터)")
    parser.add_argument("--random", action="store_true",
                        help="랜덤 샘플링 (기본은 앞에서부터 순차)")
    parser.add_argument("--seed", type=int, default=42,
                        help="--random 시 재현 가능한 시드 (기본 42)")
    parser.add_argument("--endpoint", default=os.getenv("INFERENCE_ENDPOINT"),
                        help="WALKTHROUGH 10-1 의 추론 URL (env INFERENCE_ENDPOINT)")
    parser.add_argument("--deployment", default=os.getenv("DEPLOYMENT_ID", "wind-power-v1"),
                        help="모델 배포 ID (기본 wind-power-v1, env DEPLOYMENT_ID)")
    parser.add_argument("--token", default=os.getenv("RUNWAY_API_KEY"),
                        help="Runway API 토큰 (env RUNWAY_API_KEY)")
    parser.add_argument("--tensor-name", default=DEFAULT_INPUT_TENSOR,
                        help=f"입력 텐서 이름 (기본 {DEFAULT_INPUT_TENSOR})")
    parser.add_argument("--dry-run", action="store_true",
                        help="실제 호출 없이 payload JSON 만 출력")
    # TLS 검증: OPENBAO_VERIFY_TLS 정책과 동일한 env-over-default 규약.
    # 추론 엔드포인트와 OpenBao 는 다른 호스트이므로 env 이름은 별도 (INFERENCE_VERIFY_TLS).
    parser.add_argument("--verify-tls",
                        default=os.getenv("INFERENCE_VERIFY_TLS", "true"),
                        help="TLS 검증 (기본 true, 자체 서명 환경에서만 false. env INFERENCE_VERIFY_TLS)")
    args = parser.parse_args()

    # ── 1) CSV 로드 + task_runner.py 와 동일한 전처리 ──────────────────────────
    if not os.path.exists(args.csv):
        print(f"[test_inference] CSV 를 찾을 수 없음: {args.csv}", file=sys.stderr)
        sys.exit(1)

    df = pd.read_csv(args.csv)
    df = df.drop(columns=DROP_COLS, errors="ignore")
    if TARGET_COL not in df.columns:
        print(f"[test_inference] 타겟 컬럼 '{TARGET_COL}' 이 CSV 에 없음", file=sys.stderr)
        sys.exit(1)

    y_true_series = df[TARGET_COL]
    feature_df_all = df.drop(columns=[TARGET_COL])
    print(f"[test_inference] 전체 행: {len(df)}, 피처 수: {feature_df_all.shape[1]}")
    print(f"[test_inference] 피처 순서: {list(feature_df_all.columns)}")

    # ── 2) 샘플 선택 ────────────────────────────────────────────────────────────
    if args.random:
        sampled = feature_df_all.sample(n=args.num_rows, random_state=args.seed)
        idx = sampled.index
    else:
        end = args.row_index + args.num_rows
        if end > len(df):
            print(f"[test_inference] row 범위 초과: {args.row_index}..{end} > {len(df)}",
                  file=sys.stderr)
            sys.exit(1)
        sampled = feature_df_all.iloc[args.row_index:end]
        idx = sampled.index

    y_true = y_true_series.loc[idx].tolist()
    print(f"[test_inference] 선택된 행 인덱스: {list(idx)}")

    # ── 3) payload 생성 ────────────────────────────────────────────────────────
    payload = build_payload(sampled, args.tensor_name)
    # 출력은 길어질 수 있으므로 shape / 첫 샘플만 요약
    print(f"[test_inference] payload shape: {payload['inputs'][0]['shape']}")
    print(f"[test_inference] 첫 행 (앞 5개 피처): "
          f"{payload['inputs'][0]['data'][0][:5]}...")

    if args.dry_run:
        print("[test_inference] --dry-run — payload 전체 JSON:")
        print(json.dumps(payload, indent=2))
        return

    # ── 4) 엔드포인트 호출 ────────────────────────────────────────────────────
    if not args.endpoint:
        print("[test_inference] --endpoint 또는 env INFERENCE_ENDPOINT 필요",
              file=sys.stderr)
        sys.exit(1)
    if not args.token:
        print("[test_inference] --token 또는 env RUNWAY_API_KEY 필요", file=sys.stderr)
        sys.exit(1)

    # KServe V2 Inference Protocol 경로: {endpoint}/v2/models/{deployment}/infer
    url = f"{args.endpoint.rstrip('/')}/v2/models/{args.deployment}/infer"
    headers = {
        "Authorization": f"Bearer {args.token}",
        "Content-Type": "application/json",
    }
    verify_tls = args.verify_tls.lower() == "true"
    print(f"[test_inference] POST {url}  (verify_tls={verify_tls})")

    try:
        resp = requests.post(url, headers=headers, json=payload,
                             verify=verify_tls, timeout=30)
    except requests.RequestException as e:
        print(f"[test_inference] 요청 실패: {e}", file=sys.stderr)
        sys.exit(1)

    if resp.status_code != 200:
        print(f"[test_inference] HTTP {resp.status_code}", file=sys.stderr)
        print(resp.text, file=sys.stderr)
        sys.exit(1)

    # ── 5) 결과 비교 ────────────────────────────────────────────────────────────
    # 200 + HTML/텍스트로 내려오는 프록시 케이스도 대비해 JSON 파싱 실패 시 본문 출력
    try:
        resp_json = resp.json()
    except ValueError:
        print("[test_inference] 응답이 JSON 이 아님:", file=sys.stderr)
        print(resp.text[:500], file=sys.stderr)
        sys.exit(1)

    preds = extract_predictions(resp_json)
    if not preds:
        print(f"[test_inference] 예측 데이터가 비어있음: {resp_json}", file=sys.stderr)
        sys.exit(1)

    print("[test_inference] 예측 vs 실제:")
    print(f"{'row':>8} | {'predicted':>14} | {'actual':>14} | {'abs_err':>10}")
    print("-" * 55)
    errors = []
    idx_list = list(idx)
    for i, (pred, actual) in enumerate(zip(preds, y_true)):
        err = abs(pred - actual)
        errors.append(err)
        print(f"{idx_list[i]:>8} | {pred:>14.4f} | {actual:>14.4f} | {err:>10.4f}")

    if len(errors) > 1:
        mae = sum(errors) / len(errors)
        print(f"\n[test_inference] MAE ({len(errors)} 행): {mae:.4f}")


if __name__ == "__main__":
    main()
