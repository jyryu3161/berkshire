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

BASE="${1:-5}"          # 하루 기본 몫(밀림 없으면 이만큼). 실제 N은 plan이 밀림 보정.
CAP="${KR_DAILY_CAP:-10}"  # 한 실행 최대 처리(밀림 따라잡기 상한)
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

rescreen() {
  echo "  재스크리닝 중 (召回池·去劣·유니버스300·큐리셋)..." >>"$LOG"
  python3 tools/kr_recall_pool.py build --kospi 200 --kosdaq 150 >>"$LOG" 2>&1
  python3 tools/kr_quality_screen.py run  >>"$LOG" 2>&1
  python3 tools/kr_quality_screen2.py run >>"$LOG" 2>&1
  python3 tools/kr_universe.py build --n 300 >>"$LOG" 2>&1
  python3 tools/kr_deep_queue.py reset    >>"$LOG" 2>&1   # target(밀림) 0으로 초기화
  date +%Y-%m > "$REPO/data/kr_active_month.txt"
  BUCKET="$(resolve_bucket)"   # 새 사이클 → 라벨 갱신
}

# 이번 실행 N: 밀림 보정(plan). 어제 실패 등으로 backlog 있으면 CAP 내에서 더 처리해 따라잡음.
plan_n() { python3 tools/kr_deep_queue.py plan --base "$BASE" --cap "$CAP"; }

fetch_codes() {
  python3 tools/kr_deep_queue.py next --n "$N" \
    | python3 -c "import sys,json; b=json.load(sys.stdin); print(','.join(x['code'] for x in b))"
}

[ "$RESCREEN" = "1" ] && rescreen

N="$(plan_n)"
echo "===== [$(date '+%F %T %Z')] 그룹 시작 N=$N (base=$BASE cap=$CAP) bucket=$BUCKET rescreen=$RESCREEN =====" >>"$LOG"

# 이번 배치 종목코드 추출 (아직 처리 안 된 상위 N)
CODES=$(fetch_codes)
# 큐 소진(300종목 사이클 완료) → 자동 재스크리닝 후 재시도(무한루프 방지: 1회)
if [ -z "$CODES" ]; then
  echo "  큐 비어있음 — 사이클 완료. 자동 재스크리닝 후 새 사이클 시작." >>"$LOG"
  rescreen   # 내부에서 BUCKET 갱신 + target 0
  N="$(plan_n)"
  CODES=$(fetch_codes)
fi
if [ -z "$CODES" ]; then
  echo "  재스크리닝 후에도 큐 비어있음 — 종료." >>"$LOG"; exit 0
fi
echo "  배치 종목: $CODES" >>"$LOG"

PROMPT="코스피/코스닥 4-Agent 심층분석 배치를 실행하라. 대상 종목코드: ${CODES}. \
**실행 방식(반드시 준수)**: Workflow 툴·백그라운드 워크플로우·비동기 오케스트레이션을 절대 사용하지 마라(헤드리스 세션은 백그라운드 미완료 시 강제 종료된다). \
종목을 하나씩 순차로 처리하고, 한 종목의 4-Agent(한 메시지 병렬)가 끝나면 그 결과를 받아 **즉시** 그 종목을 발행·마크한 뒤 다음 종목으로 넘어가라. 부분 진행이라도 매 종목 저장되게 하라. 모든 작업은 이 세션 내에서 동기적으로 완료한다. \
각 종목마다 skills/kr-weekly-picks.md 절차대로 돤융핑·워런 버핏·찰리 멍거·리루 4개 Agent를 한 메시지에서 병렬 실행(각자 독립 리서치+상호반박)하고, 팀장이 100% 한국어로 종합(중국어 금지)한 뒤, \
**리포트 양식(반드시 준수·모든 종목 동일)**: skills/kr-weekly-picks.md의 고정 섹션 순서·헤더를 그대로 따르고, 목표주가 섹션은 반드시 'python3 tools/valuation.py target --config data/kr_work/<코드>_val.json --md' 출력 전체(헤더 '## 목표주가 교차검증 (PER·PBR·EV/EBITDA·RIM·SOTP)' + 방법별 적정주가·**가중평균(중립)** 표 + 보수/중립/공격 밴드 표 + 해석줄)를 한 글자도 바꾸지 말고 그대로 삽입하라(표 수기 재작성 금지). 방식이 일부 빠져도 이 헤더·표 구조는 유지하라. \
각 종목을 'python3 tools/notion_publish.py add data/kr_work/<코드>_report.json --bucket \"${BUCKET}\"' 로 발행(Notion을 사이클별로 정리)하고 'python3 tools/kr_deep_queue.py mark <코드>' 로 완료표시하라. \
**중간 파일 위치(반드시 준수)**: 종목별 임시·산출 파일(리포트 JSON, 밸류 설정 val.json, bull/bear 시나리오, 임시 build 스크립트 등)은 전부 'data/kr_work/' 아래에 '<코드>_report.json / <코드>_val.json / <코드>_bull.json / <코드>_bear.json' 형식으로 생성하라. 저장소 루트에는 절대 파일을 만들지 마라. \
report.json 필드는 kr-weekly-picks.md 규격을 따르고 body_md에 % 리터럴 대신 '퍼센트'를 쓴다. 작업 디렉터리는 ${REPO}."

# 안전장치: 혹시 백그라운드 작업이 남더라도 넉넉히 대기(강제종료로 인한 미발행 방지, 40분)
export CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=2400000
# 모델 고정: 사용자 기본값(Fable 5)은 토큰 소모가 커서 일일 분석은 Opus 5로 실행.
# 서브에이전트(4대가 Agent)도 부모 모델을 상속한다.
/home/ubuntu/.local/bin/claude -p "$PROMPT" --model claude-opus-5 --dangerously-skip-permissions >>"$LOG" 2>&1
echo "===== [$(date '+%F %T %Z')] 그룹 종료 exit=$? =====" >>"$LOG"
