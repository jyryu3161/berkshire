from decimal import Decimal

from ai_berkshire_trading.models import Targets, Verdict
from ai_berkshire_trading.portfolio import (
    TradeDelta, apply_turnover_limit, bands_reliable, capital_basis,
    price_multiplier, raw_target_weight, scale_equity_weights,
)


def test_boundaries_and_linear_multiplier():
    assert price_multiplier(50, 50, 70, 90) == Decimal("1")
    assert price_multiplier(60, 50, 70, 90) == Decimal("0.75")
    assert price_multiplier(70, 50, 70, 90) == Decimal("0.5")
    assert price_multiplier(80, 50, 70, 90) == Decimal("0.25")
    assert price_multiplier(90, 50, 70, 90) == 0


def test_entry_gate_with_band_quality(signal):
    s = signal()  # bear 50k / base 70k / bull 90k — bear ≥ base×0.5 → 신뢰 가능
    assert bands_reliable(50_000, 70_000)
    assert raw_target_weight(s, 70_000, 0) == 0                  # base 이상 신규 진입 금지
    assert raw_target_weight(s, 60_000, 0) == Decimal("0.225")   # base 미만 분할 진입(0.75×30%)
    assert raw_target_weight(s, 50_000, 0) == Decimal("0.30")    # bear 이하 풀비중
    assert raw_target_weight(s, 60_000, 1) == Decimal("0.225")   # 보유 시 곡선 그대로

    wide = signal(targets_krw=Targets(20_000, 70_000, 90_000))   # bear < base/2 — 밴드 불신
    assert not bands_reliable(20_000, 70_000)
    assert raw_target_weight(wide, 60_000, 0) == 0               # base 미만이어도 진입 금지
    assert raw_target_weight(wide, 20_000, 0) == Decimal("0.30") # bear 이하만 진입
    assert raw_target_weight(wide, 60_000, 1) > 0                # 보유 중이면 곡선 적용

    assert raw_target_weight(signal(verdict=Verdict.HOLD, targets_krw=None), 50_000, 3) == 0


def test_caps_deadband_turnover_and_budget():
    scaled = scale_equity_weights({"a": Decimal(".4"), "b": Decimal(".4"), "c": Decimal(".4")})
    assert sum(scaled.values()) == Decimal(".70")
    assert scale_equity_weights({"a": Decimal(".4")})["a"] == Decimal(".4")
    assert capital_basis(10_000_000, 2_000_000, 5_000_000) == 7_000_000
    assert capital_basis(None, 2_000_000, 5_000_000) == 7_000_000
    d = TradeDelta("a", 10, 14)
    assert not d.should_trade(100, 10_000)
    exit_delta = TradeDelta("a", 10, 0, True)
    assert exit_delta.should_trade(100, 10_000)
    limited = apply_turnover_limit([TradeDelta("a", 0, 100)], {"a": 100}, 10_000)
    assert limited[0].target_qty == 25


def test_turnover_budget_greedy_topup_uses_leftover():
    # 오늘(7/30) 실사례: 한도 25만원, 비례 내림이면 코웨이 1주(9.07만)만 사고
    # 오리온이 0으로 잘려 16만원이 낭비된다. 탐욕 충전으로 둘 다 사야 한다.
    deltas = [TradeDelta("021240", 0, 2), TradeDelta("271560", 0, 1)]
    prices = {"021240": 90_700, "271560": 135_300}
    limited = apply_turnover_limit(deltas, prices, 1_000_000)
    by = {d.target_qty: d.code for d in limited}
    alloc = {d.code: d.target_qty for d in limited}
    assert alloc == {"021240": 1, "271560": 1}
    assert sum(alloc[c] * prices[c] for c in alloc) <= 250_000


def test_turnover_greedy_respects_limit_and_sell_direction():
    # 매도 방향(음수 델타)에서도 부호가 유지되고 한도를 넘지 않는다
    deltas = [TradeDelta("a", 10, 0), TradeDelta("b", 4, 0)]
    prices = {"a": 30_000, "b": 40_000}
    limited = apply_turnover_limit(deltas, prices, 1_000_000)  # 한도 250k, 수요 460k
    alloc = {d.code: d.current_qty - d.target_qty for d in limited}
    spent = sum(alloc[c] * prices[c] for c in alloc)
    assert spent <= 250_000
    assert spent > 250_000 - min(prices.values())   # 남은 예산에 1주도 더 못 들어감
    assert all(d.target_qty <= d.current_qty for d in limited)


def test_watch_entry_tier_rules(signal):
    # 관망 3.5+: bear 이하 진입만, 상한 15%
    w = signal(verdict=Verdict.WATCH, score=3.6)   # bear 50k/base 70k/bull 90k
    assert raw_target_weight(w, 60_000, 0) == 0                     # bear 위 신규 진입 금지
    assert raw_target_weight(w, 50_000, 0) == Decimal("0.15")       # bear 이하, 15% 캡
    assert raw_target_weight(w, 60_000, 1) == Decimal("0.15")       # 보유 시 곡선(22.5%)이나 캡 적용
    assert raw_target_weight(w, 80_000, 1) == Decimal("0.075")      # base 위 곡선 축소는 그대로
    # 계단식: 점수 미달/점수 없음/보류는 보유 중이어도 0(전량 청산)
    assert raw_target_weight(signal(verdict=Verdict.WATCH, score=3.4), 50_000, 2) == 0
    assert raw_target_weight(signal(verdict=Verdict.WATCH), 50_000, 2) == 0
    assert raw_target_weight(signal(verdict=Verdict.HOLD, targets_krw=None), 50_000, 2) == 0
    # 밴드 불신 관망은 진입 불가
    wide = signal(verdict=Verdict.WATCH, score=4.0,
                  targets_krw=Targets(20_000, 70_000, 90_000))
    assert raw_target_weight(wide, 20_000, 0) == 0
