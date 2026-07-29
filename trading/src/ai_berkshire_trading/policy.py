from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import cvxportfolio as cvx

from .models import AnalysisSnapshot
from .execution import build_plan


def target_weights(signals: list[AnalysisSnapshot], prices: dict[str, int],
                   strategy_owned: dict[str, int], now: datetime, *,
                   capital: int = 1_000_000,
                   account_owned: dict[str, int] | None = None,
                   max_equity: Decimal = Decimal("0.70"),
                   max_single: Decimal = Decimal("0.30"),
                   max_sector: Decimal = Decimal("0.35"),
                   max_age_days: int = 90) -> dict[str, float]:
    """Point-in-time weights; callers must provide only then-available snapshots."""
    plan = build_plan(
        signals, prices, strategy_owned, capital, now,
        account_owned=account_owned or strategy_owned, max_equity=max_equity,
        max_single=max_single, max_sector=max_sector,
        max_age_days=max_age_days,
    )
    result = {
        item.signal.code: float(
            Decimal(item.target_qty * item.price) / Decimal(capital)
        )
        for item in plan
    }
    result["CASH"] = float(
        Decimal("1") - sum((Decimal(str(v)) for v in result.values()), Decimal("0"))
    )
    return result


def fixed_weights_policy(weights_by_time):
    """cvxportfolio v1 policy boundary used by the separate backtest process."""
    return cvx.FixedWeights(weights_by_time)
