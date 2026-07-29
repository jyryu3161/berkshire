from datetime import datetime, timedelta, timezone
import sqlite3

from ai_berkshire_trading.ledger import Ledger


def test_duplicate_signal_and_same_time_conflict(tmp_path, signal):
    ledger = Ledger(tmp_path / "ledger.db")
    first = signal()
    assert ledger.add_signal(first)
    assert not ledger.add_signal(first)
    other = signal(analysis_id="different", analyzed_at=first.analyzed_at)
    try:
        ledger.add_signal(other)
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("same code/analyzed_at must conflict")


def test_latest_uses_analysis_time_not_publish_time(tmp_path, signal):
    ledger = Ledger(tmp_path / "ledger.db")
    now = datetime.now(timezone.utc)
    ledger.add_signal(signal(analysis_id="new-analysis", analyzed_at=now, published_at=now))
    ledger.add_signal(signal(analysis_id="late-upload", analyzed_at=now-timedelta(days=1), published_at=now+timedelta(days=1)))
    assert "new-analysis" in ledger.latest_signal_json("021240")


def test_idempotent_intent_and_strategy_ownership(tmp_path, signal):
    ledger = Ledger(tmp_path / "ledger.db")
    ledger.db.execute("INSERT INTO runs VALUES('r','now','STARTED',NULL)")
    args = dict(run_id="r", analysis_id="a", code="021240", side="BUY",
                target_quantity=5, requested_quantity=5, limit_price=50_000)
    key, created = ledger.create_intent(**args)
    assert created
    assert ledger.create_intent(**args) == (key, False)
    assert ledger.record_fill("f1", key, "021240", "BUY", 3, 49_900, "now")
    assert not ledger.record_fill("f1", key, "021240", "BUY", 3, 49_900, "now")
    assert ledger.strategy_quantity("021240") == 3
    assert ledger.reconcile("r", {"021240": 10})
    assert not ledger.reconcile("r", {"021240": 2})


def test_cumulative_fill_applies_only_the_increase(tmp_path, signal):
    ledger = Ledger(tmp_path / "ledger.db")
    ledger.db.execute("INSERT INTO runs VALUES('r','now','STARTED',NULL)")
    key, _ = ledger.create_intent(
        run_id="r", analysis_id="a", code="021240", side="BUY",
        target_quantity=5, requested_quantity=5, limit_price=50_000,
    )
    assert ledger.record_fill("agg", key, "021240", "BUY", 2, 50_000, "t1")
    assert ledger.record_fill("agg", key, "021240", "BUY", 5, 50_100, "t2")
    assert not ledger.record_fill("agg", key, "021240", "BUY", 5, 50_100, "t2")
    assert ledger.strategy_quantity("021240") == 5
    try:
        ledger.record_fill("agg", key, "021240", "BUY", 4, 50_000, "t3")
    except ValueError:
        pass
    else:
        raise AssertionError("cumulative fills must not shrink")


def test_intent_recovery_info_includes_known_refs(tmp_path):
    ledger = Ledger(tmp_path / "ledger.db")
    ledger.db.execute("INSERT INTO runs VALUES('r','now','STARTED',NULL)")
    key, _ = ledger.create_intent(
        run_id="r", analysis_id="a", code="021240", side="SELL",
        target_quantity=0, requested_quantity=2, limit_price=49_800,
    )
    ledger.record_broker_order(key, "0000000007", 2)
    info = ledger.intent_recovery_info(key)
    assert info == {
        "code": "021240", "side": "SELL", "requested_quantity": 2,
        "limit_price": 49_800, "known_refs": ["0000000007"],
    }
    assert ledger.intent_recovery_info("missing") is None
