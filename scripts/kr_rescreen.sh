#!/bin/bash
# 월간 재스크리닝 — 매달 1일 실행. 召回池·去劣 갱신 + 큐 리셋 + 대상월을 이번 달로 설정.
set -uo pipefail
REPO=/home/ubuntu/ai-berkshire
export PATH=/home/ubuntu/.local/bin:/usr/local/bin:/usr/bin:/bin
cd "$REPO" || exit 1
mkdir -p logs
LOG="$REPO/logs/kr-monthly.log"
echo "===== [$(date '+%F %T %Z')] 월간 재스크리닝 시작 =====" >>"$LOG"
python3 tools/kr_recall_pool.py build --kospi 200 --kosdaq 150 >>"$LOG" 2>&1
python3 tools/kr_quality_screen.py run  >>"$LOG" 2>&1
python3 tools/kr_quality_screen2.py run >>"$LOG" 2>&1
python3 tools/kr_deep_queue.py reset    >>"$LOG" 2>&1
date +%Y-%m > "$REPO/data/kr_active_month.txt"
echo "  대상월=$(cat "$REPO/data/kr_active_month.txt") — 재스크리닝 완료" >>"$LOG"
