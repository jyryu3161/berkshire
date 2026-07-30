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
