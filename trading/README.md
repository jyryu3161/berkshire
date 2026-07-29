# AI Berkshire Trading

한국 주식 연구 결과와 실계좌 주문 사이의 독립적인, fail-closed 실행 경계다.
기존 보고서 본문을 파싱하지 않으며 명시적인 `trade_signal` JSON만 받는다.
sidecar에는 `published_at`과 `source_page_id`를 넣지 않는다. 발행 성공 뒤
발행기가 실제 값으로 채운다.

```json
{
  "trade_signal": {
    "schema_version": "1",
    "analysis_id": "cycle-3-021240-20260729",
    "cycle_id": "cycle-3",
    "code": "021240",
    "name": "코웨이",
    "market": "KOSPI",
    "sector": "필수소비재",
    "analyzed_at": "2026-07-29T14:00:00+09:00",
    "verdict": "BUY",
    "targets_krw": {"bear": 70000, "base": 90000, "bull": 110000},
    "source_hash": "0000000000000000000000000000000000000000000000000000000000000000",
    "audit_status": "PASS"
  }
}
```

## 안전 기본값

- `LIVE_TRADING_ENABLED=true`와 외부 `0600` 설정 파일이 없으면 주문 실행을
  거부한다. `FIXED_CAP` 모드는 `capital_cap_krw`가 필수이며 현재 운용 설정은
  1,000,000원이다. 실전 설정(`config.example.json`)과 전략 설정
  (`strategy.json`)의 자본 설정이 다르면 split-brain 방지를 위해 실행을
  거부한다.
- `AVAILABLE_BALANCE` 모드는 매 실행마다 전략 소유 평가액과 KIS 주문가능
  원화를 합산해 기준자본을 다시 계산한다. 입출금·손익으로 잔액이 변하면
  다음 리밸런싱 목표수량도 함께 변한다.
- 초기 실전 한도는 종목 30%, 업종 35%, 전체 주식 70%, 현금 최소 30%,
  일일 회전율 25%, 신호 유효기간 90일이다.
- 토큰은 기존 캐시에서 읽기만 한다. 만료 또는 401이면 실행을 중단한다.
- SQLite는 `../local/trading/` 아래에 두며 전략이 체결한 수량만 매도한다.
- 불완전하거나 90일이 지난 신호는 매도 신호가 아니라 해당 종목 동결이다.
- 주문 응답이 불명확하면 같은 주문을 다시 내지 않고 조회/reconciliation한다.
- 호가가 정지·비정상(0원 호가 포함)이거나, 같은 배치에 동일 종목 신호가
  중복이면 실행 전체를 중단한다.
- 체결 대기(매도·매수 각 15분)가 끝나면 남은 미체결 수량은 어댑터가 즉시
  취소한다. 하루 실행이 자체 완결되며 장에 남는 주문이 없다.
- 런타임 로그는 기본적으로 `../local/trading/runtime.jsonl`에 JSON Lines로
  기록하고 10 MiB 단위로 10개까지 회전한다. 계좌번호, 토큰, 키, 전체
  KIS 주문번호는 redaction 대상이다. Notion 실행 로그는 preflight만 엄격하고
  (실패 시 주문 전 중단), 주문 이후 이벤트는 best-effort로 전송한다.

설치와 검증:

```sh
uv sync --all-groups
uv run pytest
uv run ai-berkshire-trading validate-signal signal.json
uv run ai-berkshire-trading show-strategy
uv run ai-berkshire-trading configure --capital-cap-krw 1000000
```

## 실전 실행 절차

1. `config.example.json`을 `~/.config/ai-berkshire/trading.json`으로 복사해
   계좌(`cano`, `account_product_code`), 앱키, 토큰 캐시 경로,
   Notion 토큰 경로(`notion_token_path`, 기본 `~/.notion_token`),
   신호·실행로그 데이터베이스 ID를 채우고 `chmod 600` 한다.
2. 연구 파이프라인이 발행 시 내보낸 sidecar 신호는
   `../local/trading/signals/*.json`에 쌓이며, `run`이 시작할 때 원장에
   수집하고 Notion 신호 DB에 미러링한다. sidecar 자체는
   `../tools/kr_trade_signal.py`가 리포트의 verdict + 캐노니컬 목표주가
   3밴드 표에서 기계적으로 생성한다(불완전하면 신호 생략).
3. 수동 실행: `LIVE_TRADING_ENABLED=true uv run ai-berkshire-trading run`.
   예약 실행: `systemd/` 유닛을 `~/.config/systemd/user/`에 복사하고
   서비스 파일의 `LIVE_TRADING_ENABLED`를 `true`로 바꾼 뒤
   `systemctl --user enable --now ai-berkshire-trading.timer`.
4. 종료 코드: 0 정상, 2 설정 거부, 3 안전 중단(SafetyHalt), 4 토큰 만료.
   모든 실행 결과는 `runs` 테이블에 상태·사유로 남는다.

실전 설정 예시는 `config.example.json`, 스케줄 예시는 `systemd/`에 있다. 설정
파일은 저장소 밖에 만들고 권한을 `0600`으로 제한한다.

## 라이선스 경계

이 서브프로젝트는 GPLv3인 `cvxportfolio==1.5.1`을 사용하므로 GPL-3.0-only로
분리한다. 상위 연구 도구는 이 패키지를 import하지 않는다. 공개 배포 전
의존성과 상위 프로젝트의 결합 방식에 대한 별도 라이선스 검토가 필요하다.
이는 법률 자문이 아니다.
