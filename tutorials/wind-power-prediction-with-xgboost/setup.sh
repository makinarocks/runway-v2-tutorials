#!/bin/bash
# setup.sh — Code Server IDE 에서 한 번에 venv 생성 + 의존성 설치
#
# 전제:
#   - Step 3-1 의 시스템 Python 설치 (sudo apt install python3 python3-pip python3-venv)
#     가 이미 끝나 있어야 함
#   - 이 스크립트는 wind-power-prediction 프로젝트 루트에서 실행 (requirements.txt 가 있는 곳)
#
# 사용법:
#   cd ~/workspace/wind-power-prediction
#   bash setup.sh
#
# 결과:
#   - ./venv/ 생성 (또는 기존 venv 재사용)
#   - pip 업그레이드 + requirements.txt 의존성 설치
#   ⚠️ 스크립트 안에서만 venv 가 활성화되고, 종료 후 호출한 셸에는 활성화가
#      유지되지 않습니다. 스크립트 종료 후 반드시 직접 활성화:
#        cd ~/workspace/wind-power-prediction && source venv/bin/activate

# `source setup.sh` 로 실행하면 set -e 가 호출자 셸을 죽일 수 있음 → 방지
if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
  echo "[setup] 이 스크립트는 source 가 아니라 'bash setup.sh' 로 실행하세요." >&2
  return 1 2>/dev/null || exit 1
fi

set -euo pipefail

# 스크립트 위치 = 프로젝트 루트 (어디서 실행해도 동일하게 동작)
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

echo "[setup] 프로젝트 디렉토리: $PROJECT_DIR"

if [ ! -f requirements.txt ]; then
  echo "[setup] requirements.txt 가 없습니다. 프로젝트 루트에서 실행했는지 확인하세요." >&2
  exit 1
fi

# 1) venv 생성/재사용
if [ ! -d venv ]; then
  echo "[setup] venv 생성..."
  python3 -m venv venv
else
  echo "[setup] 기존 venv 재사용"
fi

# 2) 활성화
echo "[setup] venv 활성화..."
# shellcheck disable=SC1091
source venv/bin/activate

# 3) pip 업그레이드
echo "[setup] pip 업그레이드..."
python -m pip install --upgrade pip

# 4) 의존성 설치
echo "[setup] requirements.txt 의 패키지 설치..."
pip install --no-cache-dir -r requirements.txt

# 5) 결과 확인
echo "[setup] 설치된 패키지:"
pip list

echo ""
echo "[setup] 완료."
echo "[setup] ⚠️  이 스크립트는 자식 셸에서 돌았기 때문에 호출한 터미널에는 venv 가 활성화되어 있지 않습니다."
echo "[setup]    같은 터미널에서 python 명령을 쓰기 전에 직접 활성화하세요:"
echo "[setup]      cd $(pwd) && source venv/bin/activate"
