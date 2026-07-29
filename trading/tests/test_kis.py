from datetime import datetime
import json

import httpx
import pytest

from ai_berkshire_trading.broker import (
    AmbiguousOrderError, AuthenticationExpired, OrderRejected, OrderRequest,
)
from ai_berkshire_trading.config import CapitalMode, LiveConfig
from ai_berkshire_trading.kis import KISBroker, load_cached_token
from ai_berkshire_trading.models import KST
from ai_berkshire_trading.runtime_log import runtime_logger

NOW = datetime(2026, 7, 29, 14, 30, tzinfo=KST)
TODAY = "20260729"


class FakeTime:
    def __init__(self, on_sleep=None):
        self.t = 0.0
        self.on_sleep = on_sleep

    def clock(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds
        if self.on_sleep:
            self.on_sleep()


def _ok(payload, headers=None):
    return httpx.Response(
        200, json={"rt_cd": "0", "msg_cd": "MCA00000", "msg1": "ok", **payload},
        headers=headers or {},
    )


class FakeKIS:
    def __init__(self):
        self.today = TODAY
        self.open_market = "Y"
        self.quotes = {}
        self.cash = 900_000
        self.orders: dict[str, dict] = {}
        self.reject_orders = False
        self.balance_pages = [[]]
        self.requests = []

    def add_quote(self, code, price, bid, ask, halted="N"):
        self.quotes[code] = {"price": price, "bid": bid, "ask": ask, "halted": halted}

    def order_row(self, ref):
        return self.orders[ref.split(":")[-1]]

    def fill(self, ref, quantity, price):
        row = self.orders[ref.split(":")[-1]]
        filled = int(row["tot_ccld_qty"]) + quantity
        row["tot_ccld_qty"] = str(filled)
        row["tot_ccld_amt"] = str(int(row["tot_ccld_amt"]) + quantity * price)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append(path)
        if path.endswith("chk-holiday"):
            return _ok({"output": [{"bass_dt": self.today, "opnd_yn": self.open_market}]})
        if path.endswith("inquire-price"):
            q = self.quotes[request.url.params["FID_INPUT_ISCD"]]
            return _ok({"output": {"stck_prpr": str(q["price"]), "trht_yn": q["halted"]}})
        if path.endswith("inquire-asking-price-exp-ccn"):
            q = self.quotes[request.url.params["FID_INPUT_ISCD"]]
            return _ok({"output1": {
                "askp1": str(q["ask"]), "bidp1": str(q["bid"]), "aspr_acpt_hour": "143000",
            }})
        if path.endswith("inquire-balance"):
            page = self.balance_pages.pop(0)
            more = "F" if self.balance_pages else "D"
            return _ok(
                {"output1": page, "ctx_area_fk100": "fk", "ctx_area_nk100": "nk"},
                {"tr_cont": more},
            )
        if path.endswith("inquire-psbl-order"):
            return _ok({"output": {"ord_psbl_cash": str(self.cash)}})
        if path.endswith("order-cash"):
            if self.reject_orders:
                return httpx.Response(
                    200, json={"rt_cd": "1", "msg_cd": "APBK0919", "msg1": "insufficient"}
                )
            body = json.loads(request.content)
            odno = str(len(self.orders) + 1).zfill(10)
            side = "02" if request.headers["tr_id"] == "TTTC0802U" else "01"
            self.orders[odno] = {
                "odno": odno, "orgn_odno": "", "pdno": body["PDNO"],
                "sll_buy_dvsn_cd": side, "ord_qty": body["ORD_QTY"],
                "tot_ccld_qty": "0", "tot_ccld_amt": "0", "cncl_cfrm_qty": "0",
                "ord_unpr": body["ORD_UNPR"], "ord_dt": self.today, "ord_tmd": "143001",
            }
            return _ok({"output": {
                "ODNO": odno, "KRX_FWDG_ORD_ORGNO": "06010", "ORD_TMD": "143001",
            }})
        if path.endswith("inquire-daily-ccld"):
            return _ok({"output1": [dict(row) for row in self.orders.values()]})
        if path.endswith("inquire-psbl-rvsecncl"):
            rows = [
                {"odno": odno, "ord_gno_brno": "06010"}
                for odno, row in self.orders.items()
                if int(row["ord_qty"]) - int(row["tot_ccld_qty"]) - int(row["cncl_cfrm_qty"]) > 0
            ]
            return _ok({"output": rows})
        if path.endswith("order-rvsecncl"):
            body = json.loads(request.content)
            row = self.orders[body["ORGN_ODNO"]]
            remaining = int(row["ord_qty"]) - int(row["tot_ccld_qty"]) - int(row["cncl_cfrm_qty"])
            row["cncl_cfrm_qty"] = str(int(row["cncl_cfrm_qty"]) + remaining)
            return _ok({"output": {"ODNO": "cancel"}})
        raise AssertionError(f"unexpected path {path}")


@pytest.fixture
def kis(tmp_path):
    def make(fake=None, intent_recovery=lambda key: None, on_sleep=None,
             transport=None):
        fake = fake or FakeKIS()
        token = tmp_path / "token.json"
        token.write_text(json.dumps({
            "access_token": "tok",
            "access_token_token_expired": "2026-07-30 07:00:00",
        }))
        live = LiveConfig(
            cano="12345678", account_product_code="01",
            capital_mode=CapitalMode.FIXED_CAP, capital_cap_krw=1_000_000,
            kis_base_url="https://kis.invalid", app_key="k", app_secret="s",
            token_cache_path=str(token), notion_token_path="",
            trading_signals_database_id="s", execution_log_database_id="e",
            kill_switch=False,
        )
        fake_time = FakeTime(on_sleep)
        broker = KISBroker(
            live, runtime_logger(tmp_path / "runtime.jsonl"), intent_recovery,
            transport=transport or httpx.MockTransport(fake.handle),
            sleep=fake_time.sleep, clock=fake_time.clock, now=lambda: NOW,
            poll_interval=5.0, min_request_interval=0.0,
        )
        return broker, fake
    return make


def test_token_cache_expiry(tmp_path):
    token = tmp_path / "token.json"
    token.write_text(json.dumps({
        "access_token": "tok", "access_token_token_expired": "2026-07-29 14:32:00",
    }))
    with pytest.raises(AuthenticationExpired):
        load_cached_token(token, NOW)  # inside the 5-minute expiry margin
    token.write_text(json.dumps({}))
    with pytest.raises(AuthenticationExpired):
        load_cached_token(token, NOW)
    token.write_text("raw-token\n")
    assert load_cached_token(token, NOW) == "raw-token"


def test_quote_and_market_state(kis):
    broker, fake = kis()
    fake.add_quote("021240", 50_000, 49_900, 50_000)
    quote = broker.quote("021240")
    assert (quote.price, quote.bid, quote.ask) == (50_000, 49_900, 50_000)
    assert quote.timestamp and not quote.halted
    assert broker.market_is_open()
    fake.open_market = "N"
    assert not broker.market_is_open()


def test_positions_pagination_and_cash(kis):
    fake = FakeKIS()
    fake.balance_pages = [
        [{"pdno": "021240", "hldg_qty": "2"}],
        [{"pdno": "271560", "hldg_qty": "3"}, {"pdno": "000000", "hldg_qty": "0"}],
    ]
    broker, _ = kis(fake)
    assert broker.account_positions() == {"021240": 2, "271560": 3}
    assert broker.orderable_cash() == 900_000


def test_submit_rejection_and_transport_ambiguity(kis):
    broker, fake = kis()
    order = OrderRequest("021240", "BUY", 3, 50_000, "key")
    ref = broker.submit(order)
    assert ref.startswith(f"{TODAY}:") and ref.split(":")[1] in fake.orders
    fake.reject_orders = True
    with pytest.raises(OrderRejected):
        broker.submit(order)

    def explode(request):
        raise httpx.ConnectError("boom")

    broker_down, _ = kis(transport=httpx.MockTransport(explode))
    with pytest.raises(AmbiguousOrderError):
        broker_down.submit(order)


def test_wait_fills_then_reports_filled(kis):
    fake = FakeKIS()
    holder = {}

    def fill_on_sleep():
        if holder:
            fake.fill(holder["ref"], 3, 50_000)

    broker, _ = kis(fake, on_sleep=fill_on_sleep)
    ref = broker.submit(OrderRequest("021240", "BUY", 3, 50_000, "key"))
    holder["ref"] = ref
    outcome, = broker.wait_for_orders([ref], timeout_seconds=60)
    assert outcome.status == "FILLED"
    fill, = outcome.fills
    assert (fill.quantity, fill.price, fill.fill_id) == (3, 50_000, ref)


def test_wait_timeout_cancels_remainder(kis):
    fake = FakeKIS()
    broker, _ = kis(fake)
    ref = broker.submit(OrderRequest("021240", "SELL", 3, 50_000, "key"))
    fake.fill(ref, 1, 50_000)
    outcome, = broker.wait_for_orders([ref], timeout_seconds=10)
    assert outcome.status == "CANCELLED"
    assert outcome.fills[0].quantity == 1
    assert fake.order_row(ref)["cncl_cfrm_qty"] == "2"


def test_expired_previous_day_order(kis):
    fake = FakeKIS()
    broker, _ = kis(fake)
    ref = broker.submit(OrderRequest("021240", "BUY", 3, 50_000, "key"))
    # A ref persisted yesterday is date-qualified with yesterday's date.
    fake.order_row(ref)["ord_dt"] = "20260728"
    stale_ref = f"20260728:{ref.split(':')[1]}"
    outcome, = broker.wait_for_orders([stale_ref], timeout_seconds=10)
    assert outcome.status == "EXPIRED"


def test_same_odno_on_two_days_does_not_collide(kis):
    fake = FakeKIS()
    broker, _ = kis(fake)
    ref = broker.submit(OrderRequest("021240", "BUY", 3, 50_000, "key"))
    odno = ref.split(":")[1]
    # Yesterday used the same ODNO for a different, fully-filled order.
    fake.orders["y" + odno] = {
        "odno": odno, "orgn_odno": "", "pdno": "005930", "sll_buy_dvsn_cd": "01",
        "ord_qty": "9", "tot_ccld_qty": "9", "tot_ccld_amt": "450000",
        "cncl_cfrm_qty": "0", "ord_unpr": "50000", "ord_dt": "20260728",
        "ord_tmd": "100000",
    }
    fake.fill(ref, 3, 50_000)
    outcome, = broker.wait_for_orders([ref], timeout_seconds=10)
    assert outcome.status == "FILLED"
    assert outcome.fills[0].code == "021240"


def test_find_order_matches_unknown_ref_only(kis):
    fake = FakeKIS()
    info = {
        "code": "021240", "side": "BUY", "requested_quantity": 3,
        "limit_price": 50_000, "known_refs": [],
    }
    broker, _ = kis(fake, intent_recovery=lambda key: dict(info))
    first = broker.submit(OrderRequest("021240", "BUY", 3, 50_000, "key"))
    found = broker.find_order("key")
    assert found == {"broker_order_ref": first, "requested_quantity": 3}
    second = broker.submit(OrderRequest("021240", "BUY", 3, 50_000, "key"))
    assert broker.find_order("key") is None  # two candidates is ambiguous
    info["known_refs"] = [first]
    found = broker.find_order("key")
    assert found["broker_order_ref"] == second


def test_authorization_key_token_cache(tmp_path):
    token = tmp_path / "stock_token_real.json"
    token.write_text(json.dumps({"authorization": "Bearer eyJ-live"}))
    assert load_cached_token(token, NOW) == "eyJ-live"
    import os
    old = NOW.timestamp() - 21 * 3600
    os.utime(token, (old, old))
    with pytest.raises(AuthenticationExpired):
        load_cached_token(token, NOW)
