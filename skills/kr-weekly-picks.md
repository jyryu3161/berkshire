# 코스피·코스닥 주간 종목 선정 → Notion 발행 (KR Weekly Picks)

> **[출력 언어] 모든 리포트는 무조건 한국어.**
> AI Berkshire 한국 파이프라인의 주간 실행 런북. 매주 월요일 실행(수동: `/kr-weekly-picks`, 자동: 크론).
> 작업 디렉터리는 반드시 `/home/ubuntu/ai-berkshire` (툴 상대경로 해석).

## 목표

코스피·코스닥에서 4대 대가(段永平·버핏·멍거·리루) 프레임으로 **去劣 → 딥리서치 → Notion 등록**을
매주 자동 수행한다. 롤링 배치로 2차 통과분(약 69종목)을 매주 상위 N개씩 순환 처리한다.

## 실행 절차 (순서대로)

### 0. 작업 디렉터리
```bash
cd /home/ubuntu/ai-berkshire
```

### 1. 월간 재스크리닝 (매월 첫 실행 시에만)
오늘이 그 달의 첫 월요일이면 召回池·去劣을 갱신하고 큐를 리셋한다:
```bash
python3 tools/kr_recall_pool.py build --kospi 200 --kosdaq 150
python3 tools/kr_quality_screen.py run          # 1차 去劣 (Naver)
python3 tools/kr_quality_screen2.py run          # 2차 정밀 去劣 (DART)
python3 tools/kr_deep_queue.py reset             # 새 사이클
```
그 외 주에는 이 단계를 건너뛴다.

### 2. 이번 주 배치 가져오기
```bash
python3 tools/kr_deep_queue.py next --n 13
```
→ 처리할 종목(code/name/market/score) JSON. 비어 있으면 사이클 완료이므로
`kr_deep_queue.py reset` 후 1단계부터 다시(또는 다음 달까지 대기).

### 3. 각 종목 딥리서치 (投研团队 4-Agent, 한국어)
배치의 **각 종목마다** `skills/investment-team.md` 방식으로 4개 Agent를 병렬 실행한다:
- Agent1 段永平(사업모델·해자) / Agent2 버핏(재무·밸류) / Agent3 멍거(산업·역발상) / Agent4 리루(리스크·경영진)
- 데이터는 반드시 절대경로 툴로 교차검증:
  - `python3 /home/ubuntu/ai-berkshire/tools/krx_data.py {quote|valuation|financials} {code}` (네이버, 무키)
  - `python3 /home/ubuntu/ai-berkshire/tools/dart_data.py {corpcode|financials|disclosures} ...` (DART 원천)
  - `python3 /home/ubuntu/ai-berkshire/tools/financial_rigor.py ...` (정밀계산)
- 네이버+DART 2개 독립 소스 교차검증, 오차>1% 표기, 네이버 (E)추정 컬럼 배제.
- 팀장이 4대가 관점을 종합해 **한국어 리포트** 작성: 한줄결론, 4대가 스코어카드(각 ★), 종합점수,
  전략별 권고(공격/중립/보수 + 원화 가격대), 3-시나리오 목표가, 실패 시나리오, 데이터 교차검증표.

### 4. 각 리포트 Notion 등록
종목별로 report.json 을 만들어 발행한다:
```bash
python3 tools/notion_publish.py add <report.json>
```
report.json 필드: name, code, market, score(종합 5점), verdict(매수|보류|관망|제외),
one_liner, s_biz/s_fin/s_ind/s_risk(각 대가 ★), gross_margin, ocf_ni, fcf_eok,
date(YYYY-MM-DD), body_md(한국어 리포트 전문).
(DB가 없으면 `notion_publish.py ensure-db <경제분석_page_id>` 먼저. page_id는 data/notion_db.json 에 캐시됨.)

### 5. 완료 표시
```bash
python3 tools/kr_deep_queue.py mark <이번주_처리한_코드들_쉼표구분>
python3 tools/kr_deep_queue.py status
```

## 비용 주의
딥리서치 1종목 ≈ 4-Agent 약 30만 토큰. 배치 13종목 ≈ 주당 ~4M 토큰. 배치 크기(`--n`)로 조절.

## 산출물
- Notion "경제 분석" → "코스피·코스닥 종목 리서치" DB에 종목별 행(점수·결론·재무·전문) 누적.
- 로컬 리포트는 `reports/{종목명}/` 에도 저장 권장.
