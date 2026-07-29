from datetime import datetime, timezone

from ai_berkshire_trading.policy import target_weights


def test_cash_floor_and_single_name_cap(signal):
    weights = target_weights([signal()], {"021240": 50_000}, {"021240": 0}, datetime.now(timezone.utc))
    assert weights["021240"] == .3
    assert weights["CASH"] == .7
