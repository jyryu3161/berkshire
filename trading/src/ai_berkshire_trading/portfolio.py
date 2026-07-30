from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN

from .models import AnalysisSnapshot, Verdict

D = Decimal


def price_multiplier(price: int, bear: int, base: int, bull: int) -> Decimal:
    if price <= bear:
        return D("1")
    if price < base:
        return D("1") - D("0.5") * D(price - bear) / D(base - bear)
    if price < bull:
        return D("0.5") * D(bull - price) / D(bull - base)
    return D("0")


# 밴드 신뢰 조건: bear가 base의 절반 이상(밴드 폭 2배 이내)이어야
# 방법 간 불일치가 없다고 보고 base 아래 분할 진입을 허용한다.
_BAND_QUALITY_MIN = D("0.5")


def bands_reliable(bear: int, base: int) -> bool:
    return D(bear) >= D(base) * _BAND_QUALITY_MIN


def raw_target_weight(signal: AnalysisSnapshot, price: int, strategy_owned: int,
                      max_single: Decimal = D("0.30")) -> Decimal:
    """매수 신호의 계좌 목표 비중.

    신규 진입 게이트: 밴드가 신뢰 가능하면 base 미만에서 분할 진입
    (base 부근 절반 비중 → bear에서 풀비중), 밴드 폭이 과도하면
    (방법 간 불일치) 종전대로 bear 이하에서만 진입한다. 보유 중이면
    게이트 없이 곡선 비중을 그대로 따른다(가격 회복 시 점진 축소).
    """
    if signal.verdict is not Verdict.BUY:
        return D("0")
    assert signal.targets_krw
    t = signal.targets_krw
    if strategy_owned == 0:
        if bands_reliable(t.bear, t.base):
            if price >= t.base:
                return D("0")
        elif price > t.bear:
            return D("0")
    return max_single * price_multiplier(price, t.bear, t.base, t.bull)


def scale_equity_weights(weights: dict[str, Decimal], max_equity: Decimal = D("0.70")) -> dict[str, Decimal]:
    total = sum(weights.values(), D("0"))
    if total <= max_equity:
        return dict(weights)
    ratio = max_equity / total
    result: dict[str, Decimal] = {}
    for code, weight in weights.items():
        result[code] = weight * ratio
    # Decimal repeating fractions can leave a tiny residue. Assign it to the
    # last asset so the public cash-floor contract remains exact.
    if result:
        last = next(reversed(result))
        result[last] += max_equity - sum(result.values(), D("0"))
    return result


def capital_basis(capital_cap_krw: int | None, owned_market_value: int, orderable_cash: int) -> int:
    available = owned_market_value + orderable_cash
    if available <= 0:
        raise ValueError("available strategy capital must be positive")
    if capital_cap_krw is None:
        return available
    if capital_cap_krw <= 0:
        raise ValueError("capital_cap_krw must be positive")
    return min(capital_cap_krw, available)


def target_quantity(weight: Decimal, capital: int, price: int) -> int:
    if price <= 0:
        raise ValueError("price must be positive")
    return int((weight * D(capital) / D(price)).to_integral_value(rounding=ROUND_DOWN))


@dataclass(frozen=True)
class TradeDelta:
    code: str
    current_qty: int
    target_qty: int
    explicit_exit: bool = False

    def should_trade(self, price: int, capital: int, deadband: Decimal = D("0.05"),
                     min_order_krw: int = 0) -> bool:
        if self.explicit_exit and self.target_qty == 0 and self.current_qty:
            return True
        value = abs(self.target_qty - self.current_qty) * price
        return value >= max(min_order_krw, int(deadband * D(capital)))


def apply_turnover_limit(deltas: list[TradeDelta], prices: dict[str, int], capital: int,
                         turnover_limit: Decimal = D("0.25")) -> list[TradeDelta]:
    """일일 회전율 한도 내 배분. 위험 이탈(explicit exit)은 면제.

    비례 내림만 쓰면 소수 주식이 0으로 잘리고 예산이 남는 낭비가 생기므로
    (예: 한도 25만원에 9만원만 쓰고 13.5만원짜리 1주를 포기), 내림 배분 뒤
    남은 예산을 잔여 수요가 있는 종목에 소수점 잔여가 큰 순서로 1주씩
    채워 넣는다.
    """
    exempt = [d for d in deltas if d.explicit_exit and d.target_qty == 0]
    normal = [d for d in deltas if d not in exempt]
    turnover = sum(abs(d.target_qty - d.current_qty) * prices[d.code] for d in normal)
    limit = int(D(capital) * turnover_limit)
    if turnover <= limit or turnover == 0:
        return deltas
    ratio = D(limit) / D(turnover)
    alloc: dict[str, int] = {}
    remainders = []
    for d in normal:
        desired = abs(d.target_qty - d.current_qty)
        exact = D(desired) * ratio
        alloc[d.code] = int(exact)
        remainders.append((exact - int(exact), d.code, d))
    budget = limit - sum(alloc[d.code] * prices[d.code] for d in normal)
    progress = True
    while progress:
        progress = False
        for _, _, d in sorted(remainders, key=lambda t: (-t[0], t[1])):
            desired = abs(d.target_qty - d.current_qty)
            if alloc[d.code] < desired and prices[d.code] <= budget:
                alloc[d.code] += 1
                budget -= prices[d.code]
                progress = True
    limited = []
    for d in normal:
        sign = 1 if d.target_qty >= d.current_qty else -1
        limited.append(
            TradeDelta(d.code, d.current_qty, d.current_qty + sign * alloc[d.code], False)
        )
    return exempt + limited
