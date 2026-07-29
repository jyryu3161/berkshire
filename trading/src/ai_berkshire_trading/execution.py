from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import uuid

from .broker import (
    AmbiguousOrderError, BrokerAdapter, OrderRejected, OrderRequest,
    protected_limit,
)
from .ledger import Ledger
from .models import AnalysisSnapshot, Verdict
from .notion import ExecutionLogger
from .config import StrategyConfig
from .portfolio import (
    TradeDelta, apply_turnover_limit, capital_basis, raw_target_weight,
    target_quantity,
)


class SafetyHalt(RuntimeError):
    pass


@dataclass(frozen=True)
class PlanItem:
    signal: AnalysisSnapshot
    price: int
    current_qty: int
    target_qty: int
    frozen: bool


def _constrain_with_floors(
    weights: dict[str, Decimal], codes: list[str], floors: dict[str, Decimal],
    limit: Decimal,
) -> None:
    """Scale desired weights without pretending manual holdings can be sold."""
    if not codes:
        return
    floor_total = sum((floors.get(code, Decimal("0")) for code in codes), Decimal("0"))
    if floor_total >= limit:
        for code in codes:
            weights[code] = floors.get(code, Decimal("0"))
        return
    if sum((max(weights[code], floors.get(code, Decimal("0"))) for code in codes), Decimal("0")) <= limit:
        for code in codes:
            weights[code] = max(weights[code], floors.get(code, Decimal("0")))
        return
    originals = {code: weights[code] for code in codes}
    low, high = Decimal("0"), Decimal("1")
    for _ in range(64):
        ratio = (low + high) / Decimal("2")
        total = sum(
            (max(floors.get(code, Decimal("0")), weights[code] * ratio) for code in codes),
            Decimal("0"),
        )
        if total <= limit:
            low = ratio
        else:
            high = ratio
    for code in codes:
        weights[code] = max(floors.get(code, Decimal("0")), weights[code] * low)
    residue = limit - sum((weights[code] for code in codes), Decimal("0"))
    for code in reversed(codes):
        capacity = max(Decimal("0"), originals[code] - weights[code])
        addition = min(residue, capacity)
        weights[code] += addition
        residue -= addition
        if residue <= 0:
            break


def build_plan(
    signals: list[AnalysisSnapshot],
    prices: dict[str, int],
    owned: dict[str, int],
    capital: int,
    now: datetime,
    *,
    account_owned: dict[str, int] | None = None,
    max_equity: Decimal = Decimal("0.70"),
    max_single: Decimal = Decimal("0.30"),
    max_sector: Decimal = Decimal("0.35"),
    max_age_days: int = 90,
) -> list[PlanItem]:
    account_owned = account_owned or dict(owned)
    by_code = {signal.code: signal for signal in signals}
    weights: dict[str, Decimal] = {}
    manual_floors: dict[str, Decimal] = {}
    frozen: set[str] = set()
    for signal in signals:
        if signal.is_stale(now, max_age_days) or (signal.verdict is Verdict.BUY and not signal.sector):
            frozen.add(signal.code)
            continue
        weights[signal.code] = raw_target_weight(
            signal, prices[signal.code], owned.get(signal.code, 0), max_single
        )
        manual_qty = max(account_owned.get(signal.code, 0) - owned.get(signal.code, 0), 0)
        manual_floors[signal.code] = Decimal(manual_qty * prices[signal.code]) / Decimal(capital)

    # Manual quantities and any position without a current actionable signal
    # reserve real exposure before allocating active targets.
    reserved_total = Decimal("0")
    reserved_sector: dict[str, Decimal] = {}
    for code, account_qty in account_owned.items():
        strategy_qty = owned.get(code, 0)
        signal = by_code.get(code)
        if signal and code not in frozen:
            continue
        reserved_qty = account_qty
        if not reserved_qty:
            continue
        exposure = Decimal(reserved_qty * prices[code]) / Decimal(capital)
        reserved_total += exposure
        if signal and signal.sector:
            reserved_sector[signal.sector] = reserved_sector.get(signal.sector, Decimal("0")) + exposure

    # Enforce sector limits before the aggregate equity limit.
    sectors: dict[str, list[str]] = {}
    for signal in signals:
        if signal.code in weights and signal.sector:
            sectors.setdefault(signal.sector, []).append(signal.code)
    for sector, codes in sectors.items():
        allowance = max(Decimal("0"), max_sector - reserved_sector.get(sector, Decimal("0")))
        _constrain_with_floors(weights, codes, manual_floors, allowance)
    _constrain_with_floors(
        weights, list(weights), manual_floors,
        max(Decimal("0"), max_equity - reserved_total),
    )

    plan = []
    for signal in signals:
        is_frozen = signal.code in frozen
        current = owned.get(signal.code, 0)
        if is_frozen:
            target = current
        else:
            desired_account_qty = target_quantity(weights[signal.code], capital, prices[signal.code])
            manual_qty = max(account_owned.get(signal.code, 0) - current, 0)
            target = max(0, desired_account_qty - manual_qty)
        plan.append(PlanItem(signal, prices[signal.code], current, target, is_frozen))
    return plan


class ExecutionEngine:
    """One-shot fail-closed planner/submission boundary.

    Unfilled remainders are cancelled by the broker adapter when
    ``wait_for_orders`` reaches its timeout, so a single scheduled run is
    self-contained. All cancellation calls must use broker references
    persisted by this strategy.
    """

    def __init__(self, broker: BrokerAdapter, ledger: Ledger, logger: ExecutionLogger,
                 config: StrategyConfig):
        self.broker, self.ledger, self.logger = broker, ledger, logger
        self.config = config

    def _calculate(self, signals: list[AnalysisSnapshot], run_id: str):
        account = self.broker.account_positions()
        if not self.ledger.reconcile(run_id, account):
            raise SafetyHalt("account quantity is below strategy-owned quantity")
        owned = self.ledger.strategy_positions()
        quote_codes = set(account) | set(owned) | {s.code for s in signals}
        quotes = {code: self.broker.quote(code) for code in quote_codes}
        if any(
            q.halted or not q.timestamp or q.price <= 0 or q.bid <= 0 or q.ask <= 0
            for q in quotes.values()
        ):
            raise SafetyHalt("quote is halted, stale, or missing a positive price")
        owned_value = sum(owned[c] * quotes[c].price for c in owned)
        capital = capital_basis(
            self.config.capital_cap_krw, owned_value, self.broker.orderable_cash()
        )
        plan = build_plan(
            signals, {c: q.price for c, q in quotes.items()}, owned, capital,
            datetime.now(timezone.utc), account_owned=account,
            max_equity=self.config.max_equity_weight,
            max_single=self.config.max_single_name_weight,
            max_sector=self.config.max_sector_weight,
            max_age_days=self.config.max_signal_age_days,
        )
        deltas = [
            TradeDelta(
                p.signal.code, p.current_qty, p.target_qty,
                p.signal.verdict is not Verdict.BUY and not p.frozen,
            )
            for p in plan if not p.frozen
        ]
        deltas = [
            d for d in deltas if d.should_trade(
                quotes[d.code].price, capital, self.config.rebalance_deadband,
                self.config.min_order_krw,
            )
        ]
        return account, owned, quotes, capital, plan, deltas

    def _execute_side(
        self, side: str, deltas: list[TradeDelta], plan: list[PlanItem],
        quotes: dict, run_id: str,
    ) -> tuple[bool, int]:
        pending: dict[str, tuple[str, str, int]] = {}
        submitted_value = 0
        for delta in deltas:
            actual_side = "BUY" if delta.target_qty > delta.current_qty else "SELL"
            if actual_side != side:
                continue
            qty = abs(delta.target_qty - delta.current_qty)
            if side == "SELL":
                qty = min(qty, self.ledger.strategy_quantity(delta.code))
            if not qty:
                continue
            quote = quotes[delta.code]
            best = quote.ask if side == "BUY" else quote.bid
            limit = protected_limit(side, quote.price, best)
            signal = next(p.signal for p in plan if p.signal.code == delta.code)
            key, _ = self.ledger.create_intent(
                run_id=run_id, analysis_id=signal.analysis_id, code=delta.code,
                side=side, target_quantity=delta.target_qty,
                requested_quantity=qty, limit_price=limit,
            )
            active = self.ledger.active_order(key)
            if active:
                ref = str(active["broker_order_ref"])
                requested = int(active["requested_quantity"])
            else:
                if self.ledger.intent_status(key) == "SUBMITTING":
                    found = self.broker.find_order(key)
                    if not found:
                        raise SafetyHalt(
                            "previous submission outcome is unknown; manual reconciliation required"
                        )
                    ref = str(found["broker_order_ref"])
                    requested = int(found.get("requested_quantity", qty))
                else:
                    self.ledger.mark_intent(key, "SUBMITTING")
                    request = OrderRequest(delta.code, side, qty, limit, key)
                    try:
                        ref = self.broker.submit(request)
                        requested = qty
                    except AmbiguousOrderError:
                        found = self.broker.find_order(key)
                        if not found:
                            raise SafetyHalt(
                                "ambiguous order response; submission frozen pending reconciliation"
                            )
                        ref = str(found["broker_order_ref"])
                        requested = int(found.get("requested_quantity", qty))
                    except OrderRejected as exc:
                        self.ledger.mark_intent(key, "REJECTED")
                        raise SafetyHalt(f"order rejected by broker: {exc}")
                self.ledger.record_broker_order(key, ref, requested)
                self.ledger.mark_intent(key, "SUBMITTED")
                self.logger.event(run_id, "ORDER_SUBMITTED", {
                    "code": delta.code, "side": side, "quantity": qty,
                    "target_quantity": delta.target_qty,
                })
            pending[ref] = (key, delta.code, requested)
            submitted_value += requested * limit
        if not pending:
            return True, 0
        outcomes = self.broker.wait_for_orders(list(pending), timeout_seconds=900)
        by_ref = {outcome.broker_order_ref: outcome for outcome in outcomes}
        complete = True
        for ref, (key, code, requested) in pending.items():
            outcome = by_ref.get(ref)
            if outcome is None:
                raise SafetyHalt("order status missing after wait")
            for fill in outcome.fills:
                self.ledger.record_fill(
                    fill.fill_id, key, fill.code, fill.side, fill.quantity,
                    fill.price, fill.filled_at,
                )
            attempt_filled = sum(fill.quantity for fill in outcome.fills)
            status = (
                "FILLED"
                if attempt_filled >= requested and outcome.status == "FILLED"
                else outcome.status
            )
            self.ledger.mark_broker_order(ref, status)
            self.ledger.mark_intent(key, status)
            if status != "FILLED":
                complete = False
                self.logger.event(run_id, "ORDER_INCOMPLETE", {
                    "code": code, "side": side, "filled_quantity": attempt_filled,
                    "requested_quantity": requested, "status": status,
                })
        return complete, submitted_value

    def run(self, signals: list[AnalysisSnapshot]) -> str:
        if not self.config.live_trading_enabled or self.config.kill_switch:
            raise SafetyHalt("live trading disabled or kill switch active")
        codes = [signal.code for signal in signals]
        if len(set(codes)) != len(codes):
            raise SafetyHalt("duplicate codes in signal batch")
        run_id = uuid.uuid4().hex
        self.ledger.db.execute(
            "INSERT INTO runs VALUES(?,?,?,NULL)",
            (run_id, datetime.now(timezone.utc).isoformat(), "STARTED"),
        )
        self.ledger.db.commit()
        try:
            return self._run(signals, run_id)
        except SafetyHalt as exc:
            self._finish(run_id, "HALTED", str(exc))
            raise
        except Exception as exc:
            self._finish(run_id, "ERROR", repr(exc))
            raise

    def _finish(self, run_id: str, status: str, reason: str | None) -> None:
        self.ledger.db.execute(
            "UPDATE runs SET status=?, reason=? WHERE run_id=?",
            (status, reason, run_id),
        )
        self.ledger.db.commit()

    def _run(self, signals: list[AnalysisSnapshot], run_id: str) -> str:
        if not self.logger.preflight(run_id, "signal validation started"):
            raise SafetyHalt("Notion preflight log failed")
        for signal in signals:
            signal.validate()
            self.ledger.add_signal(signal)
        if not self.broker.market_is_open():
            raise SafetyHalt("market state is closed or unknown")
        account, owned, quotes, capital, plan, deltas = self._calculate(signals, run_id)
        owned_value = sum(owned[c] * quotes[c].price for c in owned)
        self.logger.event(run_id, "CAPITAL_BASIS", {
            "mode": self.config.capital_mode.value,
            "strategy_owned_value_krw": owned_value,
            "capital_basis_krw": capital,
        })
        sell_deltas = apply_turnover_limit(
            [d for d in deltas if d.target_qty < d.current_qty],
            {c: q.price for c, q in quotes.items()}, capital,
            self.config.daily_turnover_limit,
        )
        sells_complete, _ = self._execute_side(
            "SELL", sell_deltas, plan, quotes, run_id
        )
        if not sells_complete:
            self._finish(run_id, "AWAITING_SELL_COMPLETION", "buy phase blocked")
            return run_id

        # Cash and positions are authoritative only after sell fills.
        account, owned, quotes, capital, plan, deltas = self._calculate(signals, run_id)
        normal_sell_value = sum(
            abs(d.target_qty - d.current_qty) * quotes[d.code].price
            for d in sell_deltas if not d.explicit_exit and d.target_qty < d.current_qty
        )
        remaining_fraction = max(
            Decimal("0"),
            self.config.daily_turnover_limit - Decimal(normal_sell_value) / Decimal(capital),
        )
        buy_deltas = apply_turnover_limit(
            [d for d in deltas if d.target_qty > d.current_qty],
            {c: q.price for c, q in quotes.items()}, capital, remaining_fraction,
        )
        buys_complete, _ = self._execute_side("BUY", buy_deltas, plan, quotes, run_id)
        self._finish(run_id, "COMPLETED" if buys_complete else "PARTIAL", None)
        return run_id
