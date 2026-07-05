#!/bin/bash
# 코스피/코스닥 4-Agent 딥리서치 — 한 그룹(배치) 처리 (헤드리스 Claude).
# 사용: run_kr_group.sh [그룹크기N] [월(YYYY-MM|auto)] [재스크리닝(0|1)]
#   N        : 이번 그룹에서 처리할 종목 수 (기본 10)
#   월        : Notion 발행 대상 월. auto면 오늘 날짜의 월 (기본 auto)
#   재스크리닝 : 1이면 召回池·去劣 전체 갱신 후 큐 리셋 (매달 첫 그룹만 1)
set -uo pipefail
REPO=/home/ubuntu/ai-berkshire
export PATH=/home/ubuntu/.local/bin:/usr/local/bin:/usr/bin:/bin
cd "$REPO" || exit 1
mkdir -p logs
LOG="$REPO/logs/kr-monthly.log"

N="${1:-10}"
MONTH="${2:-auto}"
RESCREEN="${3:-0}"
[ "$MONTH" = "auto" ] && MONTH="$(date +%Y-%m)"

echo "===== [$(date '+%F %T %Z')] 그룹 시작 N=$N month=$MONTH rescreen=$RESCREEN =====" >>"$LOG"

if [ "$RESCREEN" = "1" ]; then
  echo "  재스크리닝 중..." >>"$LOG"
  python3 tools/kr_recall_pool.py build --kospi 200 --kosdaq 150 >>"$LOG" 2>&1
  python3 tools/kr_quality_screen.py run  >>"$LOG" 2>&1
  python3 tools/kr_quality_screen2.py run >>"$LOG" 2>&1
  python3 tools/kr_deep_queue.py reset    >>"$LOG" 2>&1
fi

# 이번 그룹 종목코드 추출 (아직 처리 안 된 상위 N)
CODES=$(python3 tools/kr_deep_queue.py next --n "$N" \
        | python3 -c "import sys,json; b=json.load(sys.stdin); print(','.join(x['code'] for x in b))")
if [ -z "$CODES" ]; then
  echo "  큐 비어있음 — 사이클 완료." >>"$LOG"; exit 0
fi
echo "  배치 종목: $CODES" >>"$LOG"

PROMPT="코스피/코스닥 4-Agent 심층분석 배치를 실행하라. 대상 종목코드: ${CODES}. \
각 종목마다 skills/kr-weekly-picks.md 절차대로 돤융핑·워런 버핏·찰리 멍거·리루 4개 Agent를 한 메시지에서 병렬 실행(각자 독립 리서치+상호반박)하고, 팀장이 100% 한국어로 종합(중국어 금지)한 뒤, \
각 종목을 'python3 tools/notion_publish.py add <report.json> --month ${MONTH}' 로 발행하고 'python3 tools/kr_deep_queue.py mark <코드>' 로 완료표시하라. \
report.json 필드는 kr-weekly-picks.md 규격을 따르고 body_md에 % 리터럴 대신 '퍼센트'를 쓴다. 작업 디렉터리는 ${REPO}."

/home/ubuntu/.local/bin/claude -p "$PROMPT" --dangerously-skip-permissions >>"$LOG" 2>&1
echo "===== [$(date '+%F %T %Z')] 그룹 종료 exit=$? =====" >>"$LOG"
