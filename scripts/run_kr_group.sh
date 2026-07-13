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

# 시작일 가드 — 이 날짜 이전에는 크론이 걸려도 실행하지 않음(첫 사이클 개시일 지정).
# data/kr_start_date.txt (YYYY-MM-DD). 파일이 없으면 가드 없이 즉시 실행.
START="$(cat "$REPO/data/kr_start_date.txt" 2>/dev/null || true)"
if [ -n "$START" ] && [ "$(date +%F)" \< "$START" ]; then
  echo "===== [$(date '+%F %T %Z')] 시작일($START) 이전 — 스킵 =====" >>"$LOG"
  exit 0
fi

N="${1:-10}"
BUCKET_ARG="${2:-cycle}"
RESCREEN="${3:-0}"
# 2번째 인자: cycle=현재 사이클 라벨(기본, Notion을 사이클별로 정리), auto=오늘의 월, YYYY-MM 직접지정
resolve_bucket() {
  case "$BUCKET_ARG" in
    cycle) python3 tools/kr_deep_queue.py label ;;
    auto)  date +%Y-%m ;;
    *)     echo "$BUCKET_ARG" ;;
  esac
}
BUCKET="$(resolve_bucket)"

echo "===== [$(date '+%F %T %Z')] 그룹 시작 N=$N bucket=$BUCKET rescreen=$RESCREEN =====" >>"$LOG"

rescreen() {
  echo "  재스크리닝 중 (召回池·去劣·유니버스300·큐리셋)..." >>"$LOG"
  python3 tools/kr_recall_pool.py build --kospi 200 --kosdaq 150 >>"$LOG" 2>&1
  python3 tools/kr_quality_screen.py run  >>"$LOG" 2>&1
  python3 tools/kr_quality_screen2.py run >>"$LOG" 2>&1
  python3 tools/kr_universe.py build --n 300 >>"$LOG" 2>&1
  python3 tools/kr_deep_queue.py reset    >>"$LOG" 2>&1
  date +%Y-%m > "$REPO/data/kr_active_month.txt"
  BUCKET="$(resolve_bucket)"   # 새 사이클 → 라벨 갱신
}

fetch_codes() {
  python3 tools/kr_deep_queue.py next --n "$N" \
    | python3 -c "import sys,json; b=json.load(sys.stdin); print(','.join(x['code'] for x in b))"
}

[ "$RESCREEN" = "1" ] && rescreen

# 이번 배치 종목코드 추출 (아직 처리 안 된 상위 N)
CODES=$(fetch_codes)
# 큐 소진(300종목 12주 사이클 완료) → 자동 재스크리닝 후 재시도(무한루프 방지: 1회)
if [ -z "$CODES" ]; then
  echo "  큐 비어있음 — 사이클 완료. 자동 재스크리닝 후 새 사이클 시작." >>"$LOG"
  rescreen   # 내부에서 BUCKET 갱신
  CODES=$(fetch_codes)
fi
if [ -z "$CODES" ]; then
  echo "  재스크리닝 후에도 큐 비어있음 — 종료." >>"$LOG"; exit 0
fi
echo "  배치 종목: $CODES" >>"$LOG"

PROMPT="코스피/코스닥 4-Agent 심층분석 배치를 실행하라. 대상 종목코드: ${CODES}. \
각 종목마다 skills/kr-weekly-picks.md 절차대로 돤융핑·워런 버핏·찰리 멍거·리루 4개 Agent를 한 메시지에서 병렬 실행(각자 독립 리서치+상호반박)하고, 팀장이 100% 한국어로 종합(중국어 금지)한 뒤, \
각 종목을 'python3 tools/notion_publish.py add <report.json> --bucket \"${BUCKET}\"' 로 발행(Notion을 사이클별로 정리)하고 'python3 tools/kr_deep_queue.py mark <코드>' 로 완료표시하라. \
report.json 필드는 kr-weekly-picks.md 규격을 따르고 body_md에 % 리터럴 대신 '퍼센트'를 쓴다. 작업 디렉터리는 ${REPO}."

/home/ubuntu/.local/bin/claude -p "$PROMPT" --dangerously-skip-permissions >>"$LOG" 2>&1
echo "===== [$(date '+%F %T %Z')] 그룹 종료 exit=$? =====" >>"$LOG"
