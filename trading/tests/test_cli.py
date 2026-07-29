import json
import os

from ai_berkshire_trading.cli import _configure


class Args:
    capital_cap_krw = 2_000_000
    max_equity_weight = None
    max_single_name_weight = None
    max_sector_weight = None
    rebalance_deadband = None
    daily_turnover_limit = None
    max_signal_age_days = None
    min_order_krw = None


def test_configure_changes_cap_atomically_and_logs(tmp_path):
    strategy = tmp_path / "strategy.json"
    strategy.write_text(json.dumps({
        "capital_mode": "FIXED_CAP", "capital_cap_krw": 1_000_000,
        "max_equity_weight": .70, "max_single_name_weight": .30,
        "max_sector_weight": .35, "rebalance_deadband": .05,
        "daily_turnover_limit": .25, "max_signal_age_days": 90,
        "min_order_krw": 50_000, "live_trading_enabled": True,
        "kill_switch": False,
    }))
    os.chmod(strategy, 0o600)
    args = Args()
    args.strategy_config = str(strategy)
    args.runtime_log = str(tmp_path / "runtime.jsonl")
    _configure(args)
    assert json.loads(strategy.read_text())["capital_cap_krw"] == 2_000_000
    assert os.stat(strategy).st_mode & 0o777 == 0o600
    assert "STRATEGY_CONFIG_CHANGED" in (tmp_path / "runtime.jsonl").read_text()


def test_run_wires_engine_end_to_end(tmp_path, monkeypatch, signal):
    import json as _json
    from ai_berkshire_trading import cli
    from tests.test_execution import _Broker, _Logger

    live = tmp_path / "trading.json"
    live.write_text(_json.dumps({
        "cano": "12345678", "account_product_code": "01",
        "capital_mode": "FIXED_CAP", "capital_cap_krw": 1_000_000,
        "kis_base_url": "https://kis.invalid", "app_key": "k", "app_secret": "s",
        "token_cache_path": str(tmp_path / "token.json"),
        "notion_token_path": str(tmp_path / "notion.token"),
        "trading_signals_database_id": "s", "execution_log_database_id": "e",
        "kill_switch": False,
    }))
    os.chmod(live, 0o600)
    strategy = tmp_path / "strategy.json"
    strategy.write_text(_json.dumps({
        "capital_mode": "FIXED_CAP", "capital_cap_krw": 1_000_000,
        "max_equity_weight": .70, "max_single_name_weight": .30,
        "max_sector_weight": .35, "rebalance_deadband": .05,
        "daily_turnover_limit": .25, "max_signal_age_days": 90,
        "min_order_krw": 50_000, "live_trading_enabled": True,
        "kill_switch": False,
    }))
    os.chmod(strategy, 0o600)
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")

    signals_dir = tmp_path / "signals"
    signals_dir.mkdir()
    snapshot = signal()
    (signals_dir / "a-1.json").write_text(snapshot.canonical_json())

    broker = _Broker(partial_sell=False)
    broker.positions = {}
    appended = []

    class _Sink:
        def append(self, s):
            appended.append(s.analysis_id)
            return "page"

    monkeypatch.setattr(cli, "KISBroker", lambda live, runtime, recovery: broker)
    monkeypatch.setattr(cli, "NotionExecutionLogger", lambda token, db, runtime: _Logger())
    monkeypatch.setattr(cli, "NotionSignalSink", lambda token, db: _Sink())
    monkeypatch.setattr(cli, "_read_notion_token", lambda path: "tok")

    class Args:
        config = str(live)
        strategy_config = str(strategy)
        db = str(tmp_path / "ledger.sqlite3")
        runtime_log = str(tmp_path / "runtime.jsonl")
        signals_dir = str(tmp_path / "signals")

    assert cli._run(Args()) == 0
    assert appended == ["a-1"]
    assert [request.side for request in broker.requests] == ["BUY"]

    strategy.write_text(strategy.read_text().replace("1000000", "2000000"))
    os.chmod(strategy, 0o600)
    try:
        cli._run(Args())
    except RuntimeError as exc:
        assert "split-brain" in str(exc)
    else:
        raise AssertionError("capital mismatch must refuse to run")
