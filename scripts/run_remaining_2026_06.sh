#!/bin/bash
# 잔여 종목을 2026-06 DB로 4그룹(10개씩) 6시간 간격 처리 (1회성).
set -uo pipefail
for i in 1 2 3 4; do
  /bin/bash /home/ubuntu/ai-berkshire/scripts/run_kr_group.sh 10 2026-06 0
  [ "$i" -lt 4 ] && sleep 21600
done
