#!/bin/bash
# init.sh — 플레이스홀더를 본인 환경 값으로 일괄 치환 + MY-STEPS.md 생성
#
# 사용법:
#   cd ~/workspace/energy-demand-prediction
#   bash init.sh
#
# 결과:
#   - 코드 파일 6개의 플레이스홀더가 실제 값으로 교체됨
#   - .env 파일 생성
#   - MY-STEPS.md 생성 (복사붙여넣기용 가이드)

if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
  echo "[init] 'bash init.sh' 로 실행하세요 (source 아님)." >&2
  return 1 2>/dev/null || exit 1
fi

set -euo pipefail
cd "$(dirname "$0")"

echo "============================================"
echo "  Energy Demand Prediction — 초기화 스크립트"
echo "============================================"
echo ""
echo "아래 값들을 미리 준비한 뒤 입력하세요."
echo "(STEPS.md Step 0 참고)"
echo ""

# ── 값 입력 ──
read -rp "Runway 프로젝트 ID (예: 001-energy-pred-proj): " PROJECT_ID
read -rp "Runway 베이스 도메인 (예: try.mrxrunway.ai): " BASE_DOMAIN
read -rp "PVC 볼륨 ID (예: energy-pred-fs): " PVC_NAME
read -rp "GUI 호스트명 (예: energy-demand-demo): " GUI_HOSTNAME
read -rp "OpenBao 토큰 (s.xxx): " OPENBAO_TOKEN
read -rp "Gitea 사용자명: " GITEA_USERNAME
read -rp "Gitea 액세스 토큰: " GITEA_TOKEN

echo ""
echo "[init] 입력값 확인:"
echo "  PROJECT_ID     = $PROJECT_ID"
echo "  BASE_DOMAIN    = $BASE_DOMAIN"
echo "  PVC_NAME       = $PVC_NAME"
echo "  GUI_HOSTNAME   = $GUI_HOSTNAME"
echo "  OPENBAO_TOKEN  = ${OPENBAO_TOKEN:0:20}..."
echo "  GITEA_USERNAME = $GITEA_USERNAME"
echo "  GITEA_TOKEN    = ${GITEA_TOKEN:0:10}..."
echo ""
read -rp "진행하시겠습니까? (y/N): " CONFIRM
if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then
  echo "[init] 취소됨."
  exit 0
fi

# ── 코드 파일 플레이스홀더 치환 ──
echo ""
echo "[init] 코드 파일 치환 중..."

# energy_demand_prediction.py
sed -i.bak \
  -e "s|<your-project-id>|${PROJECT_ID}|g" \
  -e "s|<your-runway-domain>|${BASE_DOMAIN}|g" \
  -e "s|<your-openbao-token>|${OPENBAO_TOKEN}|g" \
  -e "s|<your-pvc-name>|${PVC_NAME}|g" \
  energy_demand_prediction.py
echo "  ✓ energy_demand_prediction.py"

# gui/nginx.conf
sed -i.bak \
  -e "s|<your-runway-domain>|${BASE_DOMAIN}|g" \
  gui/nginx.conf
echo "  ✓ gui/nginx.conf"

# gui/vite.config.js
sed -i.bak \
  -e "s|<your-runway-domain>|${BASE_DOMAIN}|g" \
  gui/vite.config.js
echo "  ✓ gui/vite.config.js"

# helm/gui/values.yaml
sed -i.bak \
  -e "s|<your-runway-domain>|${BASE_DOMAIN}|g" \
  -e "s|<your-project-id>|${PROJECT_ID}|g" \
  -e "s|<your-gui-hostname>|${GUI_HOSTNAME}|g" \
  helm/gui/values.yaml
echo "  ✓ helm/gui/values.yaml"

# .bak 파일 정리
find . -name "*.bak" -delete

# ── .env 생성 ──
echo ""
echo "[init] .env 생성 중..."
cat > .env <<ENVEOF
RUNWAY_PROJECT_ID=${PROJECT_ID}
RUNWAY_BASE_DOMAIN=${BASE_DOMAIN}
OPENBAO_TOKEN=${OPENBAO_TOKEN}

INFERENCE_ENDPOINT=
DEPLOYMENT_ID=default
ENVEOF
echo "  ✓ .env"

# ── MY-STEPS.md 생성 ──
echo ""
echo "[init] MY-STEPS.md 생성 중..."

cat > MY-STEPS.md <<STEPSEOF
# MY-STEPS — 복사붙여넣기용 가이드

> 이 파일은 init.sh 가 자동 생성. git 에 커밋하지 마세요.

## 내 환경 값

| 항목 | 값 |
|------|-----|
| 프로젝트 ID | \`${PROJECT_ID}\` |
| 베이스 도메인 | \`${BASE_DOMAIN}\` |
| PVC 이름 | \`${PVC_NAME}\` |
| GUI 호스트명 | \`${GUI_HOSTNAME}\` |
| Gitea 사용자명 | \`${GITEA_USERNAME}\` |

---

## Step 4-1. Git 설정 + clone

\`\`\`bash
cd ~/workspace
git config --global user.name "${GITEA_USERNAME}"
git config --global user.email "${GITEA_USERNAME}@example.com"
git config --global credential.helper store
git clone https://gitea.${BASE_DOMAIN}/${PROJECT_ID}/energy-demand-prediction.git
cd energy-demand-prediction
\`\`\`

## Step 5. 첫 push

\`\`\`bash
cd ~/workspace/energy-demand-prediction
git add .
git commit -m "feat: initial energy-demand-prediction setup"
git push origin main
\`\`\`

## Step 6. Gitea Actions Secrets

| Secret | 값 |
|--------|-----|
| \`GIT_USERNAME\` | \`${GITEA_USERNAME}\` |
| \`GIT_TOKEN\` | \`${GITEA_TOKEN}\` |
| \`IMAGE_TAG\` | \`gitea.${BASE_DOMAIN}/${PROJECT_ID}/energy-demand-prediction:latest\` |
| \`GUI_IMAGE_TAG\` | \`gitea.${BASE_DOMAIN}/${PROJECT_ID}/energy-demand-gui:latest\` |

## Step 7. CI/CD 트리거

\`\`\`bash
git commit --allow-empty -m "chore: trigger CI/CD"
git push origin main
\`\`\`

## Step 8. 데이터셋 업로드

\`\`\`bash
sudo mkdir -p /mnt/data/dataset
sudo mv pred-demo-dataset/ /mnt/data/dataset/
sudo mv pred-demo-testset/ /mnt/data/dataset/
\`\`\`

## Step 9. OpenBao 시크릿

- URL: \`https://openbao.${BASE_DOMAIN}\`
- Path: \`energy-demand\`

| Key | 값 |
|-----|-----|
| \`aws_access_key_id\` | (Keys 메뉴에서 발급) |
| \`aws_secret_access_key\` | (Keys 메뉴에서 발급) |
| \`gitea_username\` | \`${GITEA_USERNAME}\` |
| \`gitea_password\` | \`${GITEA_TOKEN}\` |
| \`runway_api_key\` | (Runway API 토큰) |

## Step 10. RoleBinding

\`\`\`bash
kubectl create rolebinding airflow-scheduler-pod-runner \\
  --clusterrole=edit \\
  --serviceaccount=runway-applications:airflow-scheduler \\
  -n ${PROJECT_ID}
\`\`\`

## Step 11. 모델 학습

\`\`\`bash
cd ~/workspace/energy-demand-prediction
bash setup.sh
source venv/bin/activate
python task_runner.py --step load_data
python task_runner.py --step train_model
python task_runner.py --step evaluate_model
python task_runner.py --step log_to_mlflow
\`\`\`

## Step 12. MLflow 확인

- URL: \`https://mlflow.${BASE_DOMAIN}\`
- 실험명: \`${PROJECT_ID}.energy-demand-prediction\`
- 모델명: \`${PROJECT_ID}.energy-demand-xgboost\`

## Step 13. 모델 다운로드 + 추론 테스트

\`\`\`bash
source venv/bin/activate
python download_model.py --list
python download_model.py
ls /mnt/data/models/
python test_inference.py
\`\`\`

## Step 14. GUI Helm 배포

\`\`\`bash
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
helm package helm/gui/
curl -X POST \\
  --user "${GITEA_USERNAME}:${GITEA_TOKEN}" \\
  -H "Content-Type: application/octet-stream" \\
  --data-binary @energy-demand-gui-0.4.0.tgz \\
  https://gitea.${BASE_DOMAIN}/api/packages/${PROJECT_ID}/helm/api/charts
\`\`\`

Runway 콘솔 Helm 리포지토리 URL:
\`\`\`
https://gitea.${BASE_DOMAIN}/api/packages/${PROJECT_ID}/helm
\`\`\`

## Step 15. GUI API 설정

| 필드 | 값 |
|------|-----|
| 추론 엔드포인트 URL | \`/api/inference/${PROJECT_ID}/energy-demand-prediction/energy-demand-v1\` |
| Deployment ID | \`default\` |
| Airflow URL | \`/api/airflow\` |
| DAG ID | \`energy_demand_prediction_${PROJECT_ID}\` |

## Airflow DAG URL

\`https://airflow.${BASE_DOMAIN}\`

## GUI URL

\`https://${GUI_HOSTNAME}.${BASE_DOMAIN}\`

## OpenBao 토큰 갱신 시

1. \`https://openbao.${BASE_DOMAIN}\` 재로그인 → Copy token
2. \`energy_demand_prediction.py\` 상단 OPENBAO_TOKEN 갱신
3. \`.env\` 의 OPENBAO_TOKEN 갱신
4. \`git add . && git commit -m "fix: refresh openbao token" && git push origin main\`
STEPSEOF

echo "  ✓ MY-STEPS.md"

# ── .gitignore 에 MY-STEPS.md 추가 ──
if ! grep -q "MY-STEPS.md" .gitignore 2>/dev/null; then
  echo "MY-STEPS.md" >> .gitignore
  echo "  ✓ .gitignore 에 MY-STEPS.md 추가"
fi

echo ""
echo "============================================"
echo "  초기화 완료!"
echo "============================================"
echo ""
echo "다음 단계:"
echo "  1. git add . && git commit -m 'feat: initial setup' && git push origin main"
echo "  2. Gitea Actions Secrets 등록 (MY-STEPS.md Step 6 참고)"
echo "  3. MY-STEPS.md 를 열어서 복사붙여넣기로 진행"
