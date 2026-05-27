#!/bin/bash
# setup.sh — Code Server IDE 에서 Python 3.10 설치 + venv 생성 + 의존성 설치
#
# Python 3.10 을 사용하는 이유:
#   Runway MLServer 가 Python 3.10 기반이므로, pickle 호환성을 위해 동일 버전 사용.
#   3.11+ 에서 저장한 pickle 모델은 MLServer 에서 로드 시 TypeError 발생.
#
# 사용법:
#   cd ~/workspace/energy-demand-prediction
#   bash setup.sh
#
# 완료 후 반드시 직접 활성화:
#   source venv/bin/activate

if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
  echo "[setup] 이 스크립트는 source 가 아니라 'bash setup.sh' 로 실행하세요." >&2
  return 1 2>/dev/null || exit 1
fi

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

PYTHON_TARGET="python3.10"

echo "[setup] 프로젝트 디렉토리: $PROJECT_DIR"

if [ ! -f requirements.txt ]; then
  echo "[setup] requirements.txt 가 없습니다. 프로젝트 루트에서 실행했는지 확인하세요." >&2
  exit 1
fi

# 0) SUDO 설정
SUDO=""
if command -v sudo &>/dev/null && sudo -n true 2>/dev/null; then
  SUDO="sudo"
fi

# 1) Python 3.10 설치 (없으면)
if ! command -v $PYTHON_TARGET &>/dev/null; then
  echo "[setup] $PYTHON_TARGET 미설치 — 설치합니다..."
  $SUDO apt-get update

  # Ubuntu (deadsnakes PPA) vs Debian (소스 빌드) 분기
  if command -v add-apt-repository &>/dev/null || $SUDO apt-get install -y software-properties-common 2>/dev/null; then
    $SUDO add-apt-repository -y ppa:deadsnakes/ppa 2>/dev/null || true
    $SUDO apt-get update
    $SUDO apt-get install -y python3.10 python3.10-venv python3.10-dev 2>/dev/null
  fi

  # PPA 실패 또는 Debian 이면 시스템 python3 으로 fallback
  if ! command -v $PYTHON_TARGET &>/dev/null; then
    echo "[setup] python3.10 설치 실패 — 시스템 python3 으로 fallback"
    $SUDO apt-get install -y python3 python3-venv python3-dev
    PYTHON_TARGET="python3"
  fi
  echo "[setup] Python 설치 완료: $($PYTHON_TARGET --version)"
else
  echo "[setup] $PYTHON_TARGET 이미 설치됨: $($PYTHON_TARGET --version)"
fi

# venv가 git에 tracking된 경우 제거
git rm -r --cached venv/ 2>/dev/null || true

# 2) venv 생성 (기존 venv 가 다른 Python 버전이면 재생성)
if [ -d venv ]; then
  CURRENT_PY=$(./venv/bin/python --version 2>/dev/null || echo "unknown")
  if echo "$CURRENT_PY" | grep -q "3.10"; then
    echo "[setup] 기존 venv 재사용 ($CURRENT_PY)"
  else
    echo "[setup] 기존 venv 가 $CURRENT_PY — 3.10 으로 재생성..."
    rm -rf venv
    $PYTHON_TARGET -m venv venv
  fi
else
  echo "[setup] venv 생성 ($PYTHON_TARGET)..."
  $PYTHON_TARGET -m venv venv
fi

# 3) 활성화
echo "[setup] venv 활성화..."
# shellcheck disable=SC1091
source venv/bin/activate

# 4) pip 업그레이드
echo "[setup] pip 업그레이드..."
python -m pip install --upgrade pip

# 5) 의존성 설치
echo "[setup] requirements.txt 의 패키지 설치..."
pip install --no-cache-dir -r requirements.txt

echo ""
echo "[setup] 완료. Python: $(python --version)"
echo "[setup] ⚠️  호출한 터미널에는 venv 가 활성화되어 있지 않습니다."
echo "[setup]    직접 활성화하세요:"
echo "[setup]      cd $(pwd) && source venv/bin/activate"
