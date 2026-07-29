# 코스피·코스닥 주간 종목 선정 → Notion 발행 (KR Weekly Picks)

> **[출력 언어 — 필수] 모든 출력을 100% 한국어로. 중국어(간체·번체) 절대 금지 — 제목·표 헤더·항목명·전문용어·대가 이름까지 전부 한국어(段永平→돤융핑, 巴菲特→워런 버핏, 芒格→찰리 멍거, 李录→리루). 데이터 원문 인용 시에만 원어 병기. 각 Agent 프롬프트와 최종 종합 모두 이 규칙을 명시할 것.**
> AI Berkshire 한국 파이프라인의 주간 실행 런북. 자동: 크론이 **주5일(월~금) 07:00 KST에 5종목씩** 처리(주 25종목). 수동: `/kr-weekly-picks`.
> 작업 디렉터리는 반드시 `/home/ubuntu/ai-berkshire` (툴 상대경로 해석).

## 목표

코스피·코스닥에서 4대 대가(段永平·버핏·멍거·리루) 프레임으로 **去劣 → 유니버스300 → 딥리서치 → Notion 등록**을 자동 수행한다.

**핵심 설계(중복 방지형 12주 순환)**: 매주 상위 50종목을 반복 분석하면 상위 대형주가 매주 겹치는 **중복성 문제**가 생긴다.
이를 없애기 위해 **상위 300종목을 하나의 유니버스로 묶어, 12주에 걸쳐 주당 25종목씩 중복 없이 순차 분석**한다.
- **유니버스 300 구성**: 2차 去劣 통과분(우량주, score순, 약 53종목) **우선** → 나머지는 召回池(시총 상위순)로 300까지 채움.
- **롤링 큐**: 이미 분석한 종목은 `done`에 기록되어 사이클 내 재분석 없음.
- **자가 순환**: 300종목(≈12주) 소진 시 `run_kr_group.sh`가 **자동 재스크리닝 + 큐 리셋**하여 새 사이클 시작(고정 월간 리셋 없음 → 사이클 중간 초기화 방지).

## 실행 절차 (순서대로)

### 0. 작업 디렉터리
```bash
cd /home/ubuntu/ai-berkshire
```

### 1. 재스크리닝 (사이클 시작 시에만 — 12주에 1회)
새 사이클을 시작할 때만 召回池·去劣·유니버스를 갱신하고 큐를 리셋한다.
자동 실행에서는 `run_kr_group.sh`가 **큐 소진을 감지하면 이 단계를 자동 수행**한다.
수동으로 새 사이클을 강제할 때:
```bash
python3 tools/kr_recall_pool.py build --kospi 200 --kosdaq 150
python3 tools/kr_quality_screen.py run          # 1차 去劣 (Naver)
python3 tools/kr_quality_screen2.py run          # 2차 정밀 去劣 (DART)
python3 tools/kr_universe.py build --n 300        # 유니버스300 (去劣통과 우선 + 시총채움)
python3 tools/kr_deep_queue.py reset             # 새 사이클
```
사이클 중간(2~12주)에는 이 단계를 건너뛴다(진행 중 큐 유지).

### 2. 이번 배치 가져오기
```bash
python3 tools/kr_deep_queue.py next --n 5
```
→ 처리할 종목(code/name/market/score) JSON. 비어 있으면 300종목 한 사이클 완료이므로
1단계(재스크리닝) 후 새 사이클. 자동 실행에서는 이 처리도 `run_kr_group.sh`가 담당한다.
(4-Agent 딥리서치는 종목당 ~30만×4 토큰이므로 배치는 5종목/일. 주 25종목 × 12주 = 300종목 완주.)

### 3. 각 종목 딥리서치 (投研团队 4-Agent 병렬, 한국어) — 원본 /investment-team 그대로
배치의 **각 종목마다** `skills/investment-team.md` 방식으로 **4개 Agent를 한 메시지에서 병렬** 실행한다.
핵심: 4명은 각자 **독립적으로 완전 리서치**하고 **서로 반박**한다(단순 분업 아님). 팀장이 충돌을 종합한다.
- Agent1 돤융핑(사업모델·해자) / Agent2 버핏(재무·밸류·3시나리오) / Agent3 멍거(산업·역발상·실패시나리오) / Agent4 리루(장기확실성·경영진·리스크)
- 각 Agent는 독립적으로 데이터 취득·교차검증·결론·★점수를 산출한다(다른 Agent 결과에 의존 금지).
- 데이터는 반드시 절대경로 툴로 교차검증:
  - `python3 /home/ubuntu/ai-berkshire/tools/krx_data.py {quote|valuation|financials} {code}` (네이버, 무키)
  - `python3 /home/ubuntu/ai-berkshire/tools/dart_data.py {corpcode|financials|disclosures} ...` (DART 원천)
  - `python3 /home/ubuntu/ai-berkshire/tools/financial_rigor.py ...` (정밀계산)
- 네이버+DART 2개 독립 소스 교차검증, 오차>1% 표기, 네이버 (E)추정 컬럼 배제.
- **기술적 분석(월봉·주봉) 필수 — '밸류'와 '타이밍'을 분리**(리루 리스크 Agent 또는 팀장이 산출):
  - `python3 tools/kr_technical.py analyze {code} --tf month --md` (월봉 장기) 및 `--tf week` (주봉) 실행.
  - 산출: 국면 분류(장기 우상향 추세 / 박스권(사이클) / 하락 / 혼조), 현재 위치(상단·중단·하단, 종가 백분위 기준),
    장기 레인지·ATH 낙폭·이평 이격(1/3/5년)·RSI, **신규 진입 타이밍 적합도**.
  - 차트: `python3 tools/kr_technical.py chart {code} --tf month --out reports/{종목}/chart_month.png --name {종목}` (본문에 첨부).
  - **핵심 원칙**: 4대가 '매수'는 사업가치 판단이지 진입 타이밍이 아니다. **'싸다(밸류) ≠ 쌀 때(타이밍)'.**
    박스권 종목이 상단(종가 백분위 상위·이평 대비 크게 이격)에서 저PER '매수'로 나오면 신규진입은 위험보상 비대칭 →
    밸류 매수는 박스 하단서 분할. 추세주는 이평 지지 눌림목. 이 결론을 전략별 권고·목표주가와 반드시 정합시킬 것.
  - 예: 오리온(271560)은 9년 박스권 상단(ATH -17%·3년 이평 +24%)이라 저PER '매수'라도 신규진입 부적합 — 진입존은 박스 하단.
- **목표주가는 단일 방식 금지 — 3방식 교차검증 필수**(버핏 재무 Agent가 `tools/valuation.py`로 산출):
  - 상대가치: `python3 tools/valuation.py per --eps {예상EPS} --target-per {타깃PER} --current {현재가}`
    (성장·일반주 기본). 금융·자산주는 `pbr`, 설비집약·고감가주는 `ev-ebitda` 병행.
  - RIM(잔여이익, 가치투자 핵심·왜곡 최소): `python3 tools/valuation.py rim --bps {BPS} --roe {지속가능ROE} --coe {자기자본비용≈무위험+β프리미엄} --growth {영구g} --years 5 --current {현재가}`
  - DCF(현금흐름 안정 성숙기업): `python3 tools/valuation.py dcf --fcf-ps {주당FCF} --wacc {할인율} --g1 {예측기성장} --years 5 --g-term {영구성장} --current {현재가}`
  - **종합 교차검증(캐노니컬 섹션 — 반드시 이 툴 출력을 그대로 삽입)**: 가용 방식을 JSON에 담아
    `python3 tools/valuation.py target --config data/kr_work/{코드}_val.json --md` 실행 →
    출력 전체(헤더 `## 목표주가 교차검증 (PER·PBR·EV/EBITDA·RIM·SOTP)` + 방법별 적정주가·가중평균 표 + 보수/중립/공격 밴드 표 + 해석줄)를
    **한 글자도 바꾸지 말고** 리포트의 목표주가 섹션에 붙인다. 표를 손으로 다시 그리지 말 것(양식 붕괴 원인).
  - 원칙: 최소 2방식 교차, **가중평균=중립 목표가·최소값=보수 매수기준선·최대값=공격**. 데이터 부족(FCF 등)은 공란·임의추정 금지(GIGO).
    타깃PER·WACC·COE·g 등 모든 가정은 섹션 하단 `> 가정:` 인용줄에 투명 표기.
- 팀장이 4대가 관점을 종합해 **한국어 리포트**를 아래 **고정 섹션 순서·헤더 그대로** 작성한다(종목이 달라도 양식 동일):
  ```
  ## 1. 한줄결론
  ## 2. 4대가 스코어카드            (돤융핑·버핏·멍거·리루 각 ★ + 종합점수 표)
  ## 3. 핵심 데이터 (KRX·DART 교차검증)
  ## 목표주가 교차검증 (PER·PBR·EV/EBITDA·RIM·SOTP)   ← valuation.py 출력 그대로(개별목표가+가중평균+보수/중립/공격)
  ## 5. 기술적 분석 (밸류 ≠ 타이밍)   (월봉·주봉: 국면·현재위치·이평이격·진입타이밍 표 + 월봉 차트)
  ## 6. 전략별 권고                 (공격/중립/보수 유형별 권고·원화 가격대 표)
  ## 7. 실패 시나리오
  ## 8. 데이터 한계·정직성 선언
  ```
  - 목표주가 섹션 헤더는 항상 위 문구 그대로. 방식이 일부 빠져도 헤더·표 구조(방법별 표 + **가중평균(중립)** 행 + 보수/중립/공격 밴드 표)는 유지한다.
  - 모든 표는 마크다운 파이프표로 작성 → notion_publish가 자동으로 Notion 표로 렌더링.
  - 기준 예시: `reports/LG전자/최종리포트.md`(캐노니컬 양식 레퍼런스).

### 4. 각 리포트 Notion 등록 (사이클별 정리)
종목별로 report.json 을 만들어 **현재 사이클 버킷**으로 발행한다.
**중간·산출 파일은 반드시 `data/kr_work/` 아래**에 종목코드 접두로 만든다(루트에 파일 생성 금지):
`data/kr_work/{코드}_report.json`, `{코드}_val.json`(밸류 설정), `{코드}_bull.json`/`{코드}_bear.json`(시나리오) 등. (`data/kr_work/`는 gitignore.)
Notion은 월이 아닌 **사이클 단위**로 페이지·DB가 묶인다(12주 사이클 = 페이지 1개, 3개월로 쪼개지지 않음):
```bash
BUCKET="$(python3 tools/kr_deep_queue.py label)"   # 예: '사이클 2 (2026-07~)'
python3 tools/notion_publish.py add data/kr_work/{코드}_report.json --bucket "$BUCKET"
```
report.json 필드: name, code, market, score(종합 5점), verdict(매수|보류|관망|제외),
one_liner, s_biz/s_fin/s_ind/s_risk(각 대가 ★), gross_margin, ocf_ni, fcf_eok,
date(YYYY-MM-DD), body_md(한국어 리포트 전문).

⚠️ **verdict는 실전 매매 신호다.** `tools/kr_publish_one.py`가 발행 시
`tools/kr_trade_signal.py`로 trade_signal sidecar를 생성하고, trading/ 실행
경계가 이를 받아 실계좌 주문을 낸다. `매수` verdict는 반드시 캐노니컬
'목표주가 교차검증' 3밴드 표(valuation.py 출력 그대로)가 본문에 있어야
신호가 되며, 확신 없는 종목에 `매수`를 쓰지 말 것. 비매수 verdict는 기존
보유분의 청산 신호가 된다.
- `--bucket` 미지정 시 `--month`(YYYY-MM) 또는 report.date의 월로 폴백(back-compat).
- 루트 미지정 시 `notion_publish.py set-root <경제분석_page_id>` 먼저. 상태는 data/notion_db.json 캐시.
- 버킷 페이지 정리: `notion_publish.py archive-bucket <버킷명>` (휴지통 이동).

### 5. 완료 표시
```bash
python3 tools/kr_deep_queue.py mark <이번주_처리한_코드들_쉼표구분>
python3 tools/kr_deep_queue.py status
```

## 비용 주의
딥리서치 1종목 = 4-Agent 병렬 ≈ 120만 토큰. 배치 5종목/일 × 주5일 = **주 25종목 ≈ ~30M 토큰/주**.
12주 사이클 = 300종목 ≈ ~360M 토큰. 일일 배치 크기(`run_kr_group.sh`의 N 인자, 기본 5)로 조절.

## 산출물
- Notion "경제 분석" → "코스피·코스닥 종목 리서치" DB에 종목별 행(점수·결론·재무·전문) 누적.
- 로컬 리포트는 `reports/{종목명}/` 에도 저장 권장.
