from decimal import Decimal

from ai_berkshire_trading.models import Verdict
from ai_berkshire_trading.portfolio import (
    TradeDelta, apply_turnover_limit, capital_basis, price_multiplier,
    raw_target_weight, scale_equity_weights,
)


def test_boundaries_and_linear_multiplier():
    assert price_multiplier(50, 50, 70, 90) == Decimal("1")
    assert price_multiplier(60, 50, 70, 90) == Decimal("0.75")
    assert price_multiplier(70, 50, 70, 90) == Decimal("0.5")
    assert price_multiplier(80, 50, 70, 90) == Decimal("0.25")
    assert price_multiplier(90, 50, 70, 90) == 0


def test_entry_hysteresis_and_owned_rebalance(signal):
    s = signal()
    assert raw_target_weight(s, 60_000, 0) == 0
    assert raw_target_weight(s, 60_000, 1) == Decimal("0.225")
    assert raw_target_weight(s, 50_000, 0) == Decimal("0.30")
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
