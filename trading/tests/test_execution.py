from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from ai_berkshire_trading.broker import BrokerFill, OrderOutcome, Quote
from ai_berkshire_trading.config import CapitalMode, StrategyConfig
from ai_berkshire_trading.execution import ExecutionEngine, build_plan
from ai_berkshire_trading.ledger import Ledger
from ai_berkshire_trading.models import Targets, Verdict


def test_stale_signal_freezes_instead_of_selling(signal):
    old = signal(analyzed_at=datetime.now(timezone.utc) - timedelta(days=121))
    plan = build_plan([old], {"021240": 80_000}, {"021240": 7}, 10_000_000, datetime.now(timezone.utc))
    assert plan[0].frozen
    assert plan[0].target_qty == 7


def test_explicit_nonbuy_targets_zero(signal):
    hold = signal(verdict=Verdict.HOLD, targets_krw=None)
    plan = build_plan([hold], {"021240": 50_000}, {"021240": 7}, 10_000_000, datetime.now(timezone.utc))
    assert not plan[0].frozen
    assert plan[0].target_qty == 0


def test_target_quantity_tracks_available_balance(signal):
    now = datetime.now(timezone.utc)
    small = build_plan([signal()], {"021240": 50_000}, {"021240": 0}, 1_000_000, now)
    large = build_plan([signal()], {"021240": 50_000}, {"021240": 0}, 2_000_000, now)
    reduced = build_plan([signal()], {"021240": 50_000}, {"021240": 0}, 500_000, now)
    assert (reduced[0].target_qty, small[0].target_qty, large[0].target_qty) == (3, 6, 12)


def test_manual_same_name_reduces_strategy_target(signal):
    plan = build_plan(
        [signal()], {"021240": 50_000}, {"021240": 0}, 1_000_000,
        datetime.now(timezone.utc), account_owned={"021240": 2},
    )
    # 30% account target is 6 shares; two manual shares are retained.
    assert plan[0].target_qty == 4


def test_sector_cap_scales_two_names(signal):
    second = signal(
        analysis_id="a-2", code="271560", name="오리온", sector="필수소비재"
    )
    plan = build_plan(
        [signal(), second], {"021240": 50_000, "271560": 50_000},
        {}, 1_000_000, datetime.now(timezone.utc),
    )
    # Combined raw 60% is reduced to the 35% sector ceiling.
    assert [item.target_qty for item in plan] == [3, 3]


def test_frozen_and_missing_positions_reserve_equity_budget(signal):
    active = signal(analysis_id="a-2", code="271560", name="오리온")
    plan = build_plan(
        [active], {"271560": 50_000, "005930": 50_000},
        {"005930": 10}, 1_000_000, datetime.now(timezone.utc),
        account_owned={"005930": 10},
    )
    # A missing-signal 50% holding leaves only 20% under the 70% equity cap.
    assert plan[0].target_qty == 4


class _Logger:
    def __init__(self):
        self.events = []

    def preflight(self, run_id, message):
        return True

    def event(self, run_id, event, detail):
        self.events.append((event, detail))


class _Broker:
    def __init__(self, partial_sell, partial_status="PARTIAL"):
        self.positions = {"021240": 2}
        self.cash = 900_000
        self.partial_sell = partial_sell
        self.partial_status = partial_status
        self.requests = []

    def market_is_open(self): return True
    def account_positions(self): return dict(self.positions)
    def orderable_cash(self): return self.cash
    def open_orders(self): return []
    def find_order(self, idempotency_key): return None
    def cancel_strategy_order(self, broker_order_ref): raise AssertionError
    def quote(self, code): return Quote(code, 50_000, 49_900, 50_000, "now")

    def submit(self, order):
        self.requests.append(order)
        return f"ref-{len(self.requests)}"

    def wait_for_orders(self, refs, timeout_seconds):
        outcomes = []
        for ref in refs:
            request = self.requests[int(ref.split("-")[1]) - 1]
            qty = 1 if request.side == "SELL" and self.partial_sell else request.quantity
            status = self.partial_status if qty < request.quantity else "FILLED"
            if request.side == "SELL":
                self.positions[request.code] -= qty
                self.cash += qty * 50_000
            else:
                self.positions[request.code] = self.positions.get(request.code, 0) + qty
                self.cash -= qty * 50_000
            fill = BrokerFill(
                f"fill-{ref}", request.code, request.side, qty, 50_000, "now"
            )
            outcomes.append(OrderOutcome(ref, status, (fill,)))
        return outcomes


def _config():
    return StrategyConfig(
        capital_mode=CapitalMode.FIXED_CAP, capital_cap_krw=1_000_000,
        max_equity_weight=Decimal(".70"), max_single_name_weight=Decimal(".30"),
        max_sector_weight=Decimal(".35"), rebalance_deadband=Decimal(".05"),
        daily_turnover_limit=Decimal(".25"), trailing_stop_pct=Decimal(".10"),
        watch_entry_min_score=Decimal("3.5"), watch_entry_weight=Decimal(".15"),
        max_positions=6, max_signal_age_days=90,
        min_order_krw=50_000, live_trading_enabled=True, kill_switch=False,
    )


def test_partial_sell_blocks_buy_phase(tmp_path, signal):
    ledger = Ledger(tmp_path / "ledger.db")
    ledger.db.execute(
        "INSERT INTO strategy_positions VALUES('021240',2,'now')"
    )
    ledger.db.commit()
    sell = signal(verdict=Verdict.HOLD, targets_krw=None)
    buy = signal(analysis_id="a-2", code="271560", name="오리온")
    broker = _Broker(partial_sell=True)
    run_id = ExecutionEngine(broker, ledger, _Logger(), _config()).run([sell, buy])
    assert [request.side for request in broker.requests] == ["SELL"]
    status = ledger.db.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()[0]
    assert status == "AWAITING_SELL_COMPLETION"
    assert ledger.strategy_quantity("021240") == 1


def test_full_sell_reconciles_before_buy(tmp_path, signal):
    ledger = Ledger(tmp_path / "ledger.db")
    ledger.db.execute(
        "INSERT INTO strategy_positions VALUES('021240',2,'now')"
    )
    ledger.db.commit()
    sell = signal(verdict=Verdict.HOLD, targets_krw=None)
    buy = signal(
        analysis_id="a-2", code="271560", name="오리온", sector="커뮤니케이션"
    )
    broker = _Broker(partial_sell=False)
    ExecutionEngine(broker, ledger, _Logger(), _config()).run([sell, buy])
    assert [request.side for request in broker.requests] == ["SELL", "BUY"]
    assert ledger.strategy_quantity("021240") == 0
    assert ledger.strategy_quantity("271560") > 0


def test_cancelled_partial_sell_retries_only_remaining_quantity(tmp_path, signal):
    ledger = Ledger(tmp_path / "ledger.db")
    ledger.db.execute(
        "INSERT INTO strategy_positions VALUES('021240',2,'now')"
    )
    ledger.db.commit()
    sell = signal(verdict=Verdict.HOLD, targets_krw=None)
    buy = signal(
        analysis_id="a-2", code="271560", name="오리온", sector="커뮤니케이션"
    )
    broker = _Broker(partial_sell=True, partial_status="CANCELLED")
    engine = ExecutionEngine(broker, ledger, _Logger(), _config())
    engine.run([sell, buy])
    broker.partial_sell = False
    engine.run([sell, buy])
    sell_requests = [request for request in broker.requests if request.side == "SELL"]
    assert [request.quantity for request in sell_requests] == [2, 1]
    assert ledger.strategy_quantity("021240") == 0


def test_duplicate_codes_halt(tmp_path, signal):
    from ai_berkshire_trading.execution import SafetyHalt
    ledger = Ledger(tmp_path / "ledger.db")
    engine = ExecutionEngine(_Broker(False), ledger, _Logger(), _config())
    with pytest.raises(SafetyHalt):
        engine.run([signal(), signal(analysis_id="a-2")])


def test_zero_ask_halts_and_records_reason(tmp_path, signal):
    from ai_berkshire_trading.broker import Quote
    from ai_berkshire_trading.execution import SafetyHalt

    class _NoAsk(_Broker):
        def quote(self, code):
            return Quote(code, 50_000, 49_900, 0, "now")

    ledger = Ledger(tmp_path / "ledger.db")
    engine = ExecutionEngine(_NoAsk(False), ledger, _Logger(), _config())
    with pytest.raises(SafetyHalt):
        engine.run([signal()])
    row = ledger.db.execute("SELECT status, reason FROM runs").fetchone()
    assert row["status"] == "HALTED"
    assert "price" in row["reason"]


def test_rejected_order_marks_intent_and_halts(tmp_path, signal):
    from ai_berkshire_trading.broker import OrderRejected
    from ai_berkshire_trading.execution import SafetyHalt

    class _Rejecting(_Broker):
        def submit(self, order):
            raise OrderRejected("APBK0919 insufficient cash")

    ledger = Ledger(tmp_path / "ledger.db")
    broker = _Rejecting(False)
    broker.positions = {}
    engine = ExecutionEngine(broker, ledger, _Logger(), _config())
    with pytest.raises(SafetyHalt):
        engine.run([signal()])
    intent = ledger.db.execute("SELECT status FROM order_intents").fetchone()
    assert intent["status"] == "REJECTED"
    run_row = ledger.db.execute("SELECT status, reason FROM runs").fetchone()
    assert run_row["status"] == "HALTED" and "rejected" in run_row["reason"]


class _PricedBroker(_Broker):
    def __init__(self, price):
        super().__init__(partial_sell=False)
        self.price = price

    def quote(self, code):
        return Quote(code, self.price, self.price - 100, self.price, "now")


def test_trailing_stop_arms_holds_then_exits(tmp_path, signal):
    ledger = Ledger(tmp_path / "ledger.db")
    ledger.db.execute("INSERT INTO strategy_positions VALUES('021240',2,'now')")
    ledger.db.commit()
    buy = signal()  # bear 50k / base 70k / bull 90k

    # ① bull(90k) 도달 → 트레일링 개시, 곡선 매도(목표 0) 대신 보유 유지
    broker = _PricedBroker(95_000)
    broker.positions = {"021240": 2}
    logger = _Logger()
    ExecutionEngine(broker, ledger, logger, _config()).run([buy])
    assert broker.requests == []                      # 매도 없음
    assert ledger.trailing_peak("021240") == 95_000
    assert any(e == "TRAILING_ARMED" for e, _ in logger.events)

    # ② 고점 갱신
    broker2 = _PricedBroker(100_000)
    broker2.positions = {"021240": 2}
    ExecutionEngine(broker2, ledger, _Logger(), _config()).run([buy])
    assert broker2.requests == []
    assert ledger.trailing_peak("021240") == 100_000

    # ③ 고점 대비 10% 하락 → 전량 청산 + 고점 기록 삭제
    broker3 = _PricedBroker(90_000)                   # 100k×0.9 = 90k → 발동
    broker3.positions = {"021240": 2}
    logger3 = _Logger()
    ExecutionEngine(broker3, ledger, logger3, _config()).run([buy])
    assert [(r.side, r.quantity) for r in broker3.requests] == [("SELL", 2)]
    assert ledger.strategy_quantity("021240") == 0
    assert ledger.trailing_peak("021240") is None     # 재진입 오염 방지
    assert any(e == "TRAILING_STOP_TRIGGERED" for e, _ in logger3.events)


def test_trailing_regime_ignores_curve_below_bull(tmp_path, signal):
    ledger = Ledger(tmp_path / "ledger.db")
    ledger.db.execute("INSERT INTO strategy_positions VALUES('021240',2,'now')")
    ledger.db.commit()
    ledger.raise_trailing_peak("021240", 95_000)      # 국면 이미 개시됨
    buy = signal()
    # 가격이 bull 아래(88k)로 내려왔지만 발동가(85.5k) 미달 → 곡선 무시하고 보유
    broker = _PricedBroker(88_000)
    broker.positions = {"021240": 2}
    ExecutionEngine(broker, ledger, _Logger(), _config()).run([buy])
    assert broker.requests == []
    assert ledger.trailing_peak("021240") == 95_000


def test_entry_gate_blocks_new_entries_not_holdings(signal):
    now = datetime.now(timezone.utc)
    buy_new = signal()                                    # 미보유 매수 후보
    buy_held = signal(analysis_id="a-2", code="271560", name="오리온",
                      sector="커뮤니케이션")
    prices = {"021240": 50_000, "271560": 50_000}
    owned = {"271560": 2}
    # 게이트 없음(None) → 통제 안 함
    plan = build_plan([buy_new, buy_held], prices, owned, 1_000_000, now)
    assert {p.signal.code: p.target_qty > 0 for p in plan} == {"021240": True, "271560": True}
    # 게이트 빈 집합 → 신규 진입 차단, 보유분 리밸런싱은 유지
    plan = build_plan([buy_new, buy_held], prices, owned, 1_000_000, now, entry_gate=set())
    by = {p.signal.code: p.target_qty for p in plan}
    assert by["021240"] == 0 and by["271560"] > 0
    # 허용 목록에 있으면 진입
    plan = build_plan([buy_new, buy_held], prices, owned, 1_000_000, now,
                      entry_gate={"021240"})
    assert {p.signal.code: p.target_qty > 0 for p in plan} == {"021240": True, "271560": True}


def test_max_positions_fills_slots_by_attractiveness(signal):
    now = datetime.now(timezone.utc)
    # 보유 1 + 슬롯 1 남음, 신규 후보 2 — 현재가/base 할인 깊은 쪽만 진입
    held = signal(analysis_id="h", code="000001", name="보유주", sector="업1")
    cheap = signal(analysis_id="c", code="000002", name="깊은할인", sector="업2",
                   targets_krw=Targets(50_000, 70_000, 90_000))
    rich = signal(analysis_id="r", code="000003", name="얕은할인", sector="업3",
                  targets_krw=Targets(50_000, 70_000, 90_000))
    prices = {"000001": 50_000, "000002": 55_000, "000003": 65_000}  # 0.786 vs 0.929
    plan = build_plan([held, cheap, rich], prices, {"000001": 2}, 10_000_000, now,
                      max_positions=2)
    by = {p.signal.code: p.target_qty for p in plan}
    assert by["000002"] > 0 and by["000003"] == 0 and by["000001"] > 0
