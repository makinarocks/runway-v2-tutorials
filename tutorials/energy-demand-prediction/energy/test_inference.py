"""
test_inference.py — 배포된 에너지 수요 예측 모델의 추론 엔드포인트 테스트

사용법:
    # 기본 (CSV 첫 행, .env 에서 설정 로드)
    python test_inference.py

    # 랜덤 3행 배치 호출
    python test_inference.py --num-rows 3 --random

    # payload 만 확인 (호출 안 함)
    python test_inference.py --dry-run

    # 명시적 엔드포인트 지정 (REST API URL — path 없는 endpoint base)
    python test_inference.py --endpoint https://inference.<domain>/api/<proj>/<ep>
"""

import argparse
import json
import os
import sys

import pandas as pd
import requests

from config import (
    INFERENCE_ENDPOINT as CFG_INFERENCE_ENDPOINT,
    INFERENCE_VERIFY_TLS as CFG_INFERENCE_VERIFY_TLS,
    load_secrets,
)

# task_runner.py 와 동일한 피처/타겟 컬럼 스펙
from task_runner import resolve_col_specs, FEATURE_COL_SPECS, TARGET_COL_SPECS

# 테스트 CSV 기본 경로
DEFAULT_CSV = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "dataset", "pred-demo-testset", "Q1.csv"
)
# PVC 경로 fallback
PVC_CSV = "/mnt/data/dataset/pred-demo-testset/Q1.csv"


def resolve_token(cli_token):
    """추론 호출용 토큰 해석 — Agent Injector 의 RUNWAY_API_KEY env 우선.

    우선순위:
      1. --token CLI 인자
      2. RUNWAY_API_KEY env (Agent Injector 가 /vault/secrets/creds.env 로 주입)
      3. config.load_secrets() 의 dict (Agent Injector env 가 비어도 시도)
    """
    if cli_token:
        return cli_token
    env_token = os.getenv("RUNWAY_API_KEY")
    if env_token:
        return env_token
    try:
        return load_secrets().get("runway_api_key")
    except Exception as e:
        print(f"[test_inference] 시크릿 조회 실패: {e}", file=sys.stderr)
        return None


def build_payload(feature_df):
    """DataFrame → KServe V2 pd format payload (피처별 개별 input)."""
    n_rows = len(feature_df)
    INT_COLS = {"시간", "요일", "공휴일"}
    inputs = []
    for col in feature_df.columns:
        is_int = col in INT_COLS
        inputs.append({
            "name": col,
            "shape": [n_rows],
            "datatype": "INT64" if is_int else "FP64",
            "data": feature_df[col].astype("int64" if is_int else "float64").tolist(),
        })
    return {"parameters": {"content_type": "pd"}, "inputs": inputs}


def main():
    parser = argparse.ArgumentParser(description="에너지 수요 예측 추론 엔드포인트 테스트")
    parser.add_argument("--csv", default=None, help="테스트 CSV 경로")
    parser.add_argument("--num-rows", type=int, default=1, help="요청 행 수 (기본 1)")
    parser.add_argument("--random", action="store_true", help="랜덤 샘플링")
    parser.add_argument("--seed", type=int, default=42, help="랜덤 시드")
    parser.add_argument("--endpoint", default=CFG_INFERENCE_ENDPOINT or None,
                        help="추론 엔드포인트 REST API URL (Runway 콘솔의 REST API URL 복붙. path 없는 endpoint base 형태). 예: https://inference.<domain>/api/<proj>/<ep>")
    parser.add_argument("--token", default=None)
    parser.add_argument("--dry-run", action="store_true", help="payload 만 출력")
    parser.add_argument("--verify-tls", default=None)
    args = parser.parse_args()

    # CSV 경로 해석
    csv_path = args.csv
    if not csv_path:
        if os.path.exists(PVC_CSV):
            csv_path = PVC_CSV
        elif os.path.exists(DEFAULT_CSV):
            csv_path = DEFAULT_CSV
        else:
            print("[test_inference] 테스트 CSV 를 찾을 수 없음. --csv 로 경로 지정하세요.", file=sys.stderr)
            sys.exit(1)

    df = pd.read_csv(csv_path)
    feature_cols = resolve_col_specs(df, FEATURE_COL_SPECS)
    target_cols = resolve_col_specs(df, TARGET_COL_SPECS)

    feature_df = df[feature_cols]
    target_df = df[target_cols]
    print(f"[test_inference] 전체 행: {len(df)}, 피처 수: {len(feature_cols)}, 타겟 수: {len(target_cols)}")

    # 샘플 선택
    if args.random:
        sampled = feature_df.sample(n=args.num_rows, random_state=args.seed)
    else:
        sampled = feature_df.iloc[:args.num_rows]
    idx = sampled.index
    y_true = target_df.loc[idx]
    print(f"[test_inference] 선택된 행: {list(idx)}")

    # payload 생성
    payload = build_payload(sampled)
    print(f"[test_inference] payload inputs: {len(payload['inputs'])} columns, {args.num_rows} rows")

    if args.dry_run:
        print("[test_inference] --dry-run — payload JSON (처음 3개 input):")
        preview = {**payload, "inputs": payload["inputs"][:3]}
        print(json.dumps(preview, indent=2, ensure_ascii=False))
        print(f"  ... 총 {len(payload['inputs'])}개 inputs")
        return

    # 엔드포인트 호출
    if not args.endpoint:
        print("[test_inference] --endpoint 또는 .env INFERENCE_ENDPOINT 필요", file=sys.stderr)
        sys.exit(1)

    token = resolve_token(args.token)
    if not token:
        print("[test_inference] 토큰 없음. --token CLI 인자 또는 RUNWAY_API_KEY env 필요 (Agent Injector 가 보통 자동 주입)", file=sys.stderr)
        sys.exit(1)

    url = args.endpoint
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    verify_tls = CFG_INFERENCE_VERIFY_TLS if args.verify_tls is None else (args.verify_tls.lower() == "true")

    print(f"[test_inference] POST {url}")
    resp = requests.post(url, headers=headers, json=payload, verify=verify_tls, timeout=60)

    if resp.status_code != 200:
        print(f"[test_inference] HTTP {resp.status_code}", file=sys.stderr)
        print(resp.text[:500], file=sys.stderr)
        sys.exit(1)

    resp_json = resp.json()

    # 응답 파싱 — pd format 응답 (outputs 배열에 컬럼별 데이터)
    outputs = resp_json.get("outputs", [])
    if not outputs:
        print(f"[test_inference] 응답에 outputs 없음: {resp_json}", file=sys.stderr)
        sys.exit(1)

    # 예측값 재구성
    pred_dict = {}
    for out in outputs:
        name = out.get("name", "")
        data = out.get("data", [])
        pred_dict[name] = data

    n_rows_resp = len(next(iter(pred_dict.values()), []))
    print(f"[test_inference] 응답: {len(pred_dict)} 출력 컬럼, {n_rows_resp} 행")

    # 처음 5개 타겟에 대해 예측 vs 실측 비교
    print(f"\n[test_inference] 예측 vs 실측 (처음 5개 타겟):")
    print(f"{'target':>25} | {'predicted':>12} | {'actual':>12} | {'abs_err':>10}")
    print("-" * 65)
    for col in target_cols[:5]:
        preds = pred_dict.get(col, [])
        for i, row_idx in enumerate(idx):
            if i >= len(preds):
                break
            pred_val = float(preds[i])
            actual_val = float(y_true.loc[row_idx, col])
            err = abs(pred_val - actual_val)
            print(f"{col:>25} | {pred_val:>12.2f} | {actual_val:>12.2f} | {err:>10.2f}")


if __name__ == "__main__":
    main()
