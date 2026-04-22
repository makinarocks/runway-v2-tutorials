#!/bin/bash
# run_dag.sh — Airflow REST API 로 DAG 를 trigger 하고 태스크별 상태를 폴링하는 스크립트
#
# 언제 쓰나?
#   개발 중 빠른 테스트용. 프로덕션은 Airflow UI 또는 스케줄러 자체 스케줄로 실행.
#
# 사용법:
#   1) 아래 API_KEY 값을 본인 Airflow JWT 로 교체 (수명 ~24h, 만료되면 401)
#   2) bash run_dag.sh
#
# API_KEY 획득 방법 (Airflow 3.0 기준 — 실제 경로는 환경별로 다를 수 있음):
#   A) /auth/token 엔드포인트 사용:
#      curl -X POST "https://airflow.v2.mrxrunway.ai/auth/token" \
#        -H "Content-Type: application/json" \
#        -d '{"username":"<계정>","password":"<비밀번호>"}'
#      응답의 access_token 값을 아래 API_KEY 에 붙여넣기
#      → 실패 시 `/api/v2/auth/login` 등 배포본별 다른 경로일 수 있음
#   B) 브라우저 DevTools 로 Airflow UI 로그인 후 Network 탭에서
#      Authorization: Bearer <token> 헤더 값 복사 (가장 확실)
#
# ⚠️ 이 파일에 API_KEY 가 하드코딩되어 있음 — 저장소를 public 으로 전환하지 말 것.
# 프로덕션 환경에서는 env 파일이나 Secrets Manager 로 분리 권장.

AIRFLOW_HOST="https://airflow.v2.mrxrunway.ai"
DAG_ID="wind_power_prediction_v4"
# ↓ 본인 Airflow JWT 로 교체 (기본값은 만료된 예시)
API_KEY="eyJhbGciOiJIUzUxMiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIzIiwiaXNzIjpbXSwiYXVkIjoiYXBhY2hlLWFpcmZsb3ciLCJuYmYiOjE3NzQzNjMyNzMsImV4cCI6MTc3NDQ0OTY3MywiaWF0IjoxNzc0MzYzMjczfQ.kIJs59Ik8_lkkUrlG3YQo3CsV5bxyu3T7ZDc6Tq_2IpzZZM_7O_gH0w-1nmV0UZ2CdS88lq6RGA6cA5Ce2EY4g"

echo "=== DAG 실행: ${DAG_ID} ==="

# DAG trigger
RESPONSE=$(curl -s -X POST \
  "${AIRFLOW_HOST}/api/v2/dags/${DAG_ID}/dagRuns" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{\"logical_date\": \"$(date -u +%Y-%m-%dT%H:%M:%S.000000Z)\", \"conf\": {}}")

echo "${RESPONSE}" | python3 -m json.tool 2>/dev/null || echo "${RESPONSE}"

# dag_run_id 추출
DAG_RUN_ID=$(echo "${RESPONSE}" | python3 -c "import sys,json; print(json.load(sys.stdin)['dag_run_id'])" 2>/dev/null)

if [ -z "${DAG_RUN_ID}" ]; then
  echo "DAG trigger 실패"
  exit 1
fi

echo ""
echo "=== DAG Run ID: ${DAG_RUN_ID} ==="
echo "=== 상태 확인 중... ==="

# 태스크 목록 (DAG 실행 순서에 맞춤)
TASKS=("ensure_pull_secret" "load_data" "load_model" "train_model" "evaluate_model" "log_to_mlflow")

# 상태 폴링
while true; do
  sleep 10

  # DAG 전체 상태
  DAG_RUN=$(curl -s -X GET \
    "${AIRFLOW_HOST}/api/v2/dags/${DAG_ID}/dagRuns/${DAG_RUN_ID}" \
    -H "Authorization: Bearer ${API_KEY}")
  STATUS=$(echo "${DAG_RUN}" | python3 -c "import sys,json; print(json.load(sys.stdin)['state'])" 2>/dev/null)

  echo "──────────────────────────────────────"
  echo "$(date '+%H:%M:%S') - DAG 상태: ${STATUS}"

  # 태스크별 상태 조회
  TASK_INSTANCES=$(curl -s -X GET \
    "${AIRFLOW_HOST}/api/v2/dags/${DAG_ID}/dagRuns/${DAG_RUN_ID}/taskInstances" \
    -H "Authorization: Bearer ${API_KEY}")

  for TASK in "${TASKS[@]}"; do
    TASK_STATE=$(echo "${TASK_INSTANCES}" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for t in data.get('task_instances', []):
    if t['task_id'] == '${TASK}':
        print(t.get('state') or 'waiting')
        break
else:
    print('waiting')
" 2>/dev/null)
    printf "  %-20s %s\n" "${TASK}" "${TASK_STATE}"
  done

  if [ "${STATUS}" = "success" ] || [ "${STATUS}" = "failed" ]; then
    echo ""
    echo "=== 최종 상태: ${STATUS} ==="

    # 성공 시 모델 다운로드 안내
    if [ "${STATUS}" = "success" ]; then
      echo ""
      echo "=== 모델 아티팩트를 PVC에 저장하려면 IDE에서 다음 명령어를 실행하세요 ==="
      echo "  python download_model.py                       # 최신 모델"
      echo "  python download_model.py --model-id m-xxxx...  # 특정 모델"
    fi

    # 실패 시 실패 태스크 로그 출력
    if [ "${STATUS}" = "failed" ]; then
      echo ""
      echo "=== 실패 태스크 로그 ==="
      for TASK in "${TASKS[@]}"; do
        TASK_STATE=$(echo "${TASK_INSTANCES}" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for t in data.get('task_instances', []):
    if t['task_id'] == '${TASK}':
        print(t.get('state') or '')
        break
" 2>/dev/null)
        if [ "${TASK_STATE}" = "failed" ]; then
          echo "--- ${TASK} ---"
          curl -s -X GET \
            "${AIRFLOW_HOST}/api/v2/dags/${DAG_ID}/dagRuns/${DAG_RUN_ID}/taskInstances/${TASK}/logs/1" \
            -H "Authorization: Bearer ${API_KEY}" | tail -30
        fi
      done
    fi
    break
  fi
done
