#!/bin/bash
# 재스크리닝(수동/안전용) — 召回池·去劣·유니버스300 갱신 + 큐 리셋 + 대상월 갱신.
# 자동 순환에서는 run_kr_group.sh가 큐 소진 시 동일 작업을 수행하므로 고정 크론은 없음.
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
python3 tools/kr_universe.py build --n 300 >>"$LOG" 2>&1
python3 tools/kr_deep_queue.py reset    >>"$LOG" 2>&1
date +%Y-%m > "$REPO/data/kr_active_month.txt"
echo "  대상월=$(cat "$REPO/data/kr_active_month.txt") — 재스크리닝 완료" >>"$LOG"
