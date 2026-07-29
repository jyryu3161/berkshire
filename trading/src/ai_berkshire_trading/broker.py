from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class AmbiguousOrderError(RuntimeError):
    """POST outcome is unknown; caller must query before any further submission."""


class OrderRejected(RuntimeError):
    """Broker returned a definitive rejection; no order exists for the request."""


class AuthenticationExpired(RuntimeError):
    """Cached authentication is invalid; token issuance is deliberately unsupported."""


@dataclass(frozen=True)
class Quote:
    code: str
    price: int
    bid: int
    ask: int
    timestamp: str
    halted: bool = False


@dataclass(frozen=True)
class OrderRequest:
    code: str
    side: str
    quantity: int
    limit_price: int
    idempotency_key: str


@dataclass(frozen=True)
class BrokerFill:
    fill_id: str
    code: str
    side: str
    quantity: int
    price: int
    filled_at: str


@dataclass(frozen=True)
class OrderOutcome:
    broker_order_ref: str
    status: str
    fills: tuple[BrokerFill, ...] = ()


class BrokerAdapter(Protocol):
    def market_is_open(self) -> bool: ...
    def quote(self, code: str) -> Quote: ...
    def account_positions(self) -> dict[str, int]: ...
    def orderable_cash(self) -> int: ...
    def open_orders(self) -> list[dict]: ...
    def submit(self, order: OrderRequest) -> str: ...
    def find_order(self, idempotency_key: str) -> dict | None: ...
    def wait_for_orders(self, broker_order_refs: list[str], timeout_seconds: int) -> list[OrderOutcome]: ...
    def cancel_strategy_order(self, broker_order_ref: str) -> None: ...


def krx_tick(price: int) -> int:
    if price < 2_000: return 1
    if price < 5_000: return 5
    if price < 20_000: return 10
    if price < 50_000: return 50
    if price < 200_000: return 100
    if price < 500_000: return 500
    return 1_000


def protected_limit(side: str, decision_price: int, best_price: int) -> int:
    tick = krx_tick(best_price)
    if side == "BUY":
        ceiling = decision_price * 1005 // 1000
        value = min(best_price, ceiling)
        return value - value % tick
    floor = (decision_price * 995 + 999) // 1000
    value = max(best_price, floor)
    return ((value + tick - 1) // tick) * tick
