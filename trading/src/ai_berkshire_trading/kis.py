from __future__ import annotations

from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
import time
from typing import Any, Callable

import httpx

from .broker import (
    AmbiguousOrderError, AuthenticationExpired, BrokerFill, OrderOutcome,
    OrderRejected, OrderRequest, Quote,
)
from .config import LiveConfig
from .models import KST
from .runtime_log import log_event

_PATH = "/uapi/domestic-stock/v1"
_TR = {
    "price": "FHKST01010100",
    "asking": "FHKST01010200",
    "balance": "TTTC8434R",
    "psbl_order": "TTTC8908R",
    "buy": "TTTC0802U",
    "sell": "TTTC0801U",
    "cancel": "TTTC0803U",
    "daily": "TTTC8001R",
    "revisable": "TTTC8036R",
    "holiday": "CTCA0903R",
}
_RATE_LIMIT_MSG_CD = "EGW00201"


def _int(value: Any) -> int:
    text = str(value if value is not None else "").strip().replace(",", "")
    if not text or text == "-":
        return 0
    return int(text)


def load_cached_token(path: str | Path, now: datetime) -> str:
    """Read an existing access token; token issuance is deliberately unsupported."""
    p = Path(path).expanduser()
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise AuthenticationExpired(f"token cache unreadable: {exc}")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        raw = {"access_token": text.strip()}
    if not isinstance(raw, dict):
        raw = {"access_token": str(raw).strip()}
    token = str(
        raw.get("access_token") or raw.get("token") or raw.get("authorization") or ""
    ).strip()
    token = token.removeprefix("Bearer ").strip()
    if not token:
        raise AuthenticationExpired("token cache has no access_token")
    expiry = raw.get("access_token_token_expired") or raw.get("expired_at")
    if expiry:
        parsed = datetime.strptime(str(expiry), "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
        if now.astimezone(KST) >= parsed - timedelta(minutes=5):
            raise AuthenticationExpired("cached token is expired or about to expire")
    else:
        # Caches maintained by an external refresher carry no expiry field;
        # KIS tokens live 24h, so a file this stale cannot hold a valid one.
        written = datetime.fromtimestamp(p.stat().st_mtime, tz=KST)
        if now.astimezone(KST) - written > timedelta(hours=20):
            raise AuthenticationExpired("token cache is older than 20 hours")
    return token


class KISBroker:
    """Korea Investment & Securities REST adapter for the domestic cash account.

    Read-only token policy: authentication comes exclusively from an existing
    cache file. Every 4xx/5xx surprise on an order POST is treated as
    ambiguous unless the exchange returned a definitive rt_cd rejection.
    Unfilled remainders are cancelled when ``wait_for_orders`` times out so a
    scheduled run never leaves resting orders behind.
    """

    def __init__(
        self, live: LiveConfig, runtime: logging.Logger,
        intent_recovery: Callable[[str], dict | None], *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] | None = None,
        poll_interval: float = 5.0,
        min_request_interval: float = 0.25,
    ):
        self.live = live
        self.runtime = runtime
        self.intent_recovery = intent_recovery
        self._sleep, self._clock = sleep, clock
        self._now = now or (lambda: datetime.now(KST))
        self.poll_interval = poll_interval
        self.min_request_interval = min_request_interval
        self._token = load_cached_token(live.token_cache_path, self._now())
        self._client = httpx.Client(
            base_url=live.kis_base_url, timeout=10.0, transport=transport
        )
        self._last_request = float("-inf")

    def _account(self) -> dict[str, str]:
        return {"CANO": self.live.cano, "ACNT_PRDT_CD": self.live.account_product_code}

    def _headers(self, tr_id: str) -> dict[str, str]:
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._token}",
            "appkey": self.live.app_key,
            "appsecret": self.live.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    def _pace(self) -> None:
        elapsed = self._clock() - self._last_request
        if elapsed < self.min_request_interval:
            self._sleep(self.min_request_interval - elapsed)
        self._last_request = self._clock()

    def _check_auth(self, response: httpx.Response) -> None:
        if response.status_code == 401:
            raise AuthenticationExpired("KIS returned 401; token refresh is unsupported")

    def _get(self, path: str, tr_id: str, params: dict[str, str],
             extra_headers: dict[str, str] | None = None) -> tuple[dict, httpx.Headers]:
        last_error: Exception | None = None
        for _ in range(4):
            self._pace()
            try:
                response = self._client.get(
                    path, params=params,
                    headers={**self._headers(tr_id), **(extra_headers or {})},
                )
            except httpx.HTTPError as exc:
                last_error = exc
                continue
            self._check_auth(response)
            if response.status_code != 200:
                last_error = RuntimeError(f"KIS GET {path} -> HTTP {response.status_code}")
                continue
            body = response.json()
            if body.get("rt_cd") != "0":
                if body.get("msg_cd") == _RATE_LIMIT_MSG_CD:
                    last_error = RuntimeError("KIS rate limited")
                    self._sleep(1.0)
                    continue
                raise RuntimeError(
                    f"KIS GET {path} failed: {body.get('msg_cd')} {body.get('msg1')}"
                )
            return body, response.headers
        raise RuntimeError(f"KIS GET {path} failed after retries: {last_error}")

    def _get_paged(self, path: str, tr_id: str, params: dict[str, str],
                   output_key: str) -> list[dict]:
        rows: list[dict] = []
        request = dict(params)
        tr_cont = ""
        for _ in range(50):
            body, headers = self._get(path, tr_id, request, {"tr_cont": tr_cont})
            page = body.get(output_key) or []
            rows.extend(page if isinstance(page, list) else [page])
            if headers.get("tr_cont", "") not in ("F", "M"):
                return rows
            tr_cont = "N"
            request["CTX_AREA_FK100"] = str(body.get("ctx_area_fk100", "")).strip()
            request["CTX_AREA_NK100"] = str(body.get("ctx_area_nk100", "")).strip()
        raise RuntimeError(f"KIS GET {path} pagination did not terminate")

    # --- BrokerAdapter -----------------------------------------------------

    def market_is_open(self) -> bool:
        now = self._now().astimezone(KST)
        if now.weekday() >= 5:
            return False
        minute = now.hour * 60 + now.minute
        if not (9 * 60 <= minute < 15 * 60 + 20):
            return False
        date = now.strftime("%Y%m%d")
        try:
            body, _ = self._get(
                "/uapi/domestic-stock/v1/quotations/chk-holiday", _TR["holiday"],
                {"BASS_DT": date, "CTX_AREA_NK": "", "CTX_AREA_FK": ""},
            )
        except Exception as exc:
            log_event(self.runtime, "MARKET_STATE_UNKNOWN", error=str(exc))
            return False
        for row in body.get("output") or []:
            if str(row.get("bass_dt")) == date:
                return str(row.get("opnd_yn")) == "Y"
        return False

    def quote(self, code: str) -> Quote:
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
        price_body, _ = self._get(
            f"{_PATH}/quotations/inquire-price", _TR["price"], params
        )
        current = price_body.get("output") or {}
        book_body, _ = self._get(
            f"{_PATH}/quotations/inquire-asking-price-exp-ccn", _TR["asking"], params
        )
        book = book_body.get("output1") or {}
        return Quote(
            code=code,
            price=_int(current.get("stck_prpr")),
            bid=_int(book.get("bidp1")),
            ask=_int(book.get("askp1")),
            timestamp=str(book.get("aspr_acpt_hour") or "").strip(),
            halted=str(current.get("trht_yn")) == "Y",
        )

    def account_positions(self) -> dict[str, int]:
        params = {
            **self._account(), "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02",
            "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00", "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
        }
        positions: dict[str, int] = {}
        for row in self._get_paged(
            f"{_PATH}/trading/inquire-balance", _TR["balance"], params, "output1"
        ):
            quantity = _int(row.get("hldg_qty"))
            if quantity:
                code = str(row.get("pdno"))
                positions[code] = positions.get(code, 0) + quantity
        return positions

    def orderable_cash(self) -> int:
        params = {
            **self._account(), "PDNO": "005930", "ORD_UNPR": "0", "ORD_DVSN": "01",
            "CMA_EVLU_AMT_ICLD_YN": "N", "OVRS_ICLD_YN": "N",
        }
        body, _ = self._get(
            f"{_PATH}/trading/inquire-psbl-order", _TR["psbl_order"], params
        )
        return _int((body.get("output") or {}).get("ord_psbl_cash"))

    def open_orders(self) -> list[dict]:
        params = {
            **self._account(), "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
            "INQR_DVSN_1": "0", "INQR_DVSN_2": "0",
        }
        return self._get_paged(
            f"{_PATH}/trading/inquire-psbl-rvsecncl", _TR["revisable"], params, "output"
        )

    def submit(self, order: OrderRequest) -> str:
        tr_id = _TR["buy" if order.side == "BUY" else "sell"]
        payload = {
            **self._account(), "PDNO": order.code, "ORD_DVSN": "00",
            "ORD_QTY": str(order.quantity), "ORD_UNPR": str(order.limit_price),
        }
        self._pace()
        try:
            response = self._client.post(
                f"{_PATH}/trading/order-cash", json=payload, headers=self._headers(tr_id)
            )
        except httpx.HTTPError as exc:
            raise AmbiguousOrderError(f"order POST transport failure: {exc}")
        self._check_auth(response)
        try:
            body = response.json()
        except ValueError:
            raise AmbiguousOrderError(
                f"order POST returned unparseable HTTP {response.status_code}"
            )
        if "rt_cd" in body and body["rt_cd"] != "0":
            raise OrderRejected(f"{body.get('msg_cd')} {body.get('msg1')}".strip())
        if response.status_code != 200 or body.get("rt_cd") != "0":
            raise AmbiguousOrderError(f"order POST returned HTTP {response.status_code}")
        odno = str((body.get("output") or {}).get("ODNO", "")).strip()
        if not odno:
            raise AmbiguousOrderError("order accepted without an order number")
        # ODNO is only unique within a trading day; date-qualify every ref so
        # a stale reference can never collide with a later order.
        ref = f"{self._now().astimezone(KST).strftime('%Y%m%d')}:{odno}"
        log_event(
            self.runtime, "KIS_ORDER_ACCEPTED", code=order.code, side=order.side,
            quantity=order.quantity, limit_price=order.limit_price,
            broker_order_ref=ref,
        )
        return ref

    def _recent_orders(self) -> dict[str, dict]:
        now = self._now().astimezone(KST)
        params = {
            **self._account(),
            "INQR_STRT_DT": (now - timedelta(days=7)).strftime("%Y%m%d"),
            "INQR_END_DT": now.strftime("%Y%m%d"),
            "SLL_BUY_DVSN_CD": "00", "INQR_DVSN": "00", "PDNO": "",
            "CCLD_DVSN": "00", "ORD_GNO_BRNO": "", "ODNO": "",
            "INQR_DVSN_3": "00", "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
        }
        rows = self._get_paged(
            f"{_PATH}/trading/inquire-daily-ccld", _TR["daily"], params, "output1"
        )
        today = now.strftime("%Y%m%d")
        # Amend/cancel requests carry the original number in orgn_odno; only
        # original orders represent our submissions. Keys are date-qualified
        # because ODNO restarts every trading day.
        return {
            f"{str(row.get('ord_dt') or today).strip()}:{str(row.get('odno')).strip()}": row
            for row in rows if _int(row.get("orgn_odno")) == 0
        }

    def find_order(self, idempotency_key: str) -> dict | None:
        info = self.intent_recovery(idempotency_key)
        if not info:
            return None
        side_code = "02" if info["side"] == "BUY" else "01"
        known = {str(ref) for ref in info.get("known_refs", [])}
        matches = [
            (ref, row) for ref, row in self._recent_orders().items()
            if ref not in known
            and str(row.get("pdno")) == info["code"]
            and str(row.get("sll_buy_dvsn_cd")).strip() == side_code
            and _int(row.get("ord_qty")) == info["requested_quantity"]
            and _int(row.get("ord_unpr")) == info["limit_price"]
        ]
        if len(matches) != 1:
            log_event(
                self.runtime, "ORDER_RECOVERY_UNRESOLVED",
                idempotency_key=idempotency_key, candidates=len(matches),
            )
            return None
        ref, row = matches[0]
        return {
            "broker_order_ref": ref,
            "requested_quantity": _int(row.get("ord_qty")),
        }

    @staticmethod
    def _open_quantity(row: dict) -> int:
        ordered = _int(row.get("ord_qty"))
        filled = _int(row.get("tot_ccld_qty"))
        cancelled = _int(row.get("cncl_cfrm_qty"))
        computed = max(0, ordered - filled - cancelled)
        # Some responses expose the remaining quantity directly; trust the
        # smaller figure so a missing cancel-confirmation field cannot make a
        # dead order look live forever.
        if str(row.get("rmn_qty", "")).strip():
            computed = min(computed, _int(row.get("rmn_qty")))
        return computed

    def _cancel(self, broker_order_ref: str) -> bool:
        odno = broker_order_ref.split(":")[-1]
        organisation = ""
        for row in self.open_orders():
            if str(row.get("odno")).strip() == odno:
                organisation = str(row.get("ord_gno_brno", "")).strip()
                break
        else:
            return False  # nothing left to cancel (filled or already cancelled)
        payload = {
            **self._account(), "KRX_FWDG_ORD_ORGNO": organisation,
            "ORGN_ODNO": odno, "ORD_DVSN": "00",
            "RVSE_CNCL_DVSN_CD": "02", "ORD_QTY": "0", "ORD_UNPR": "0",
            "QTY_ALL_ORD_YN": "Y",
        }
        self._pace()
        try:
            response = self._client.post(
                f"{_PATH}/trading/order-rvsecncl", json=payload,
                headers=self._headers(_TR["cancel"]),
            )
            self._check_auth(response)
            body = response.json()
        except AuthenticationExpired:
            raise
        except Exception as exc:
            log_event(
                self.runtime, "KIS_CANCEL_FAILED",
                broker_order_ref=broker_order_ref, error=str(exc),
            )
            return False
        if response.status_code != 200 or body.get("rt_cd") != "0":
            log_event(
                self.runtime, "KIS_CANCEL_FAILED",
                broker_order_ref=broker_order_ref,
                error=f"{body.get('msg_cd')} {body.get('msg1')}",
            )
            return False
        log_event(self.runtime, "KIS_ORDER_CANCELLED", broker_order_ref=broker_order_ref)
        return True

    def cancel_strategy_order(self, broker_order_ref: str) -> None:
        if not self._cancel(broker_order_ref):
            raise RuntimeError(f"cancel failed for order {broker_order_ref}")

    def wait_for_orders(self, broker_order_refs: list[str],
                        timeout_seconds: int) -> list[OrderOutcome]:
        today = self._now().astimezone(KST).strftime("%Y%m%d")
        deadline = self._clock() + timeout_seconds

        def open_refs(rows: dict[str, dict]) -> list[str]:
            return [
                ref for ref in broker_order_refs
                if ref in rows
                and str(rows[ref].get("ord_dt", today)) >= today
                and self._open_quantity(rows[ref]) > 0
            ]

        def missing_refs(rows: dict[str, dict]) -> list[str]:
            return [ref for ref in broker_order_refs if ref not in rows]

        # A just-accepted order can lag the fills feed briefly; give missing
        # refs a short grace before the caller treats them as unknown.
        grace_deadline = self._clock() + 30
        rows = self._recent_orders()
        while self._clock() < deadline and (
            open_refs(rows)
            or (missing_refs(rows) and self._clock() < grace_deadline)
        ):
            self._sleep(self.poll_interval)
            rows = self._recent_orders()

        cancelling = open_refs(rows)
        for ref in cancelling:
            self._cancel(ref)
        for _ in range(6):
            if not cancelling:
                break
            self._sleep(self.poll_interval)
            rows = self._recent_orders()
            cancelling = open_refs(rows)

        outcomes = []
        for ref in broker_order_refs:
            row = rows.get(ref)
            if row is None:
                continue  # caller halts on missing status
            ordered = _int(row.get("ord_qty"))
            filled = _int(row.get("tot_ccld_qty"))
            amount = _int(row.get("tot_ccld_amt"))
            side = "BUY" if str(row.get("sll_buy_dvsn_cd")).strip() == "02" else "SELL"
            order_date = str(row.get("ord_dt", today))
            fills: tuple[BrokerFill, ...] = ()
            if filled > 0:
                price = amount // filled if amount else _int(row.get("ord_unpr"))
                fills = (BrokerFill(
                    fill_id=ref, code=str(row.get("pdno")),
                    side=side, quantity=filled, price=price,
                    filled_at=f"{order_date}T{str(row.get('ord_tmd', '')).strip()}",
                ),)
            remaining = self._open_quantity(row)
            if filled >= ordered:
                status = "FILLED"
            elif remaining > 0 and order_date < today:
                status = "EXPIRED"
            elif remaining > 0:
                status = "SUBMITTED"
            else:
                # Terminal cancel, with or without partial fills. Never report
                # "PARTIAL" here: the ledger treats PARTIAL as still-active and
                # would wait on this dead order forever instead of reordering
                # the remainder.
                status = "CANCELLED"
            outcomes.append(OrderOutcome(ref, status, fills))
        return outcomes
