#!/bin/bash
# 코스피·코스닥 주간 종목선정 → Notion 발행 (헤드리스 Claude 실행)
# crontab에서 매주 월요일 호출. /kr-weekly-picks 런북을 비대화식으로 실행한다.
set -uo pipefail

REPO="/home/ubuntu/ai-berkshire"
export PATH="/home/ubuntu/.local/bin:/usr/local/bin:/usr/bin:/bin"
cd "$REPO" || exit 1
mkdir -p logs

TS="$(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "===== [$TS] kr-weekly-picks 시작 =====" >> logs/kr-weekly-picks.log

# 헤드리스 실행. 서브에이전트 다수 호출 → 승인 프롬프트 스킵 필요.
/home/ubuntu/.local/bin/claude -p "/kr-weekly-picks 이번 주 배치를 실행하라" \
  --dangerously-skip-permissions \
  >> logs/kr-weekly-picks.log 2>&1

echo "===== [$(date '+%Y-%m-%d %H:%M:%S %Z')] 종료(exit=$?) =====" >> logs/kr-weekly-picks.log
