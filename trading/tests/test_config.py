import json
import os
from decimal import Decimal
import pytest

from ai_berkshire_trading.config import LiveConfig, StrategyConfig


def test_live_config_requires_0600_budget_flag_and_kill_switch(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    payload = {
        "cano": "12345678", "account_product_code": "01",
        "capital_mode": "FIXED_CAP", "capital_cap_krw": 1,
        "kis_base_url": "https://example.invalid", "app_key": "x", "app_secret": "y",
        "token_cache_path": "/tmp/token", "notion_token_path": "/tmp/notion",
        "trading_signals_database_id": "s",
        "execution_log_database_id": "e", "kill_switch": False,
    }
    path.write_text(json.dumps(payload))
    os.chmod(path, 0o600)
    with pytest.raises(RuntimeError):
        LiveConfig.load(path)
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    assert LiveConfig.load(path).capital_cap_krw == 1
    payload["capital_mode"] = "AVAILABLE_BALANCE"
    payload["capital_cap_krw"] = None
    path.write_text(json.dumps(payload))
    assert LiveConfig.load(path).capital_cap_krw is None
    payload["kill_switch"] = True
    path.write_text(json.dumps(payload))
    with pytest.raises(RuntimeError):
        LiveConfig.load(path)


def test_strategy_config_is_validated(tmp_path):
    path = tmp_path / "strategy.json"
    path.write_text(json.dumps({
        "capital_mode": "FIXED_CAP", "capital_cap_krw": 1_000_000,
        "max_equity_weight": .70, "max_single_name_weight": .30,
        "max_sector_weight": .35, "rebalance_deadband": .05,
        "daily_turnover_limit": .25, "max_signal_age_days": 90,
        "min_order_krw": 50_000, "live_trading_enabled": True,
        "kill_switch": False,
    }))
    os.chmod(path, 0o600)
    config = StrategyConfig.load(path)
    assert config.capital_cap_krw == 1_000_000
    assert config.max_single_name_weight == Decimal("0.3")
