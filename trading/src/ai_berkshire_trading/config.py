from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
import os
import re
from pathlib import Path
from decimal import Decimal


class CapitalMode(StrEnum):
    FIXED_CAP = "FIXED_CAP"
    AVAILABLE_BALANCE = "AVAILABLE_BALANCE"


@dataclass(frozen=True)
class StrategyConfig:
    capital_mode: CapitalMode
    capital_cap_krw: int | None
    max_equity_weight: Decimal
    max_single_name_weight: Decimal
    max_sector_weight: Decimal
    rebalance_deadband: Decimal
    daily_turnover_limit: Decimal
    max_signal_age_days: int
    min_order_krw: int
    live_trading_enabled: bool
    kill_switch: bool

    @classmethod
    def load(cls, path: str | Path) -> "StrategyConfig":
        p = Path(path)
        if p.stat().st_mode & 0o777 != 0o600:
            raise PermissionError("strategy config permissions must be 0600")
        raw = json.loads(p.read_text(encoding="utf-8"))
        mode = CapitalMode(raw["capital_mode"])
        cap = raw.get("capital_cap_krw")
        if mode is CapitalMode.FIXED_CAP and (not isinstance(cap, int) or cap <= 0):
            raise ValueError("FIXED_CAP requires a positive capital_cap_krw")
        if mode is CapitalMode.AVAILABLE_BALANCE:
            cap = None
        values = {
            key: Decimal(str(raw[key])) for key in (
                "max_equity_weight", "max_single_name_weight", "max_sector_weight",
                "rebalance_deadband", "daily_turnover_limit",
            )
        }
        if not Decimal("0") < values["max_single_name_weight"] <= values["max_sector_weight"] <= values["max_equity_weight"] < Decimal("1"):
            raise ValueError("weight limits must satisfy 0 < single <= sector <= equity < 1")
        if not Decimal("0") < values["daily_turnover_limit"] <= Decimal("1"):
            raise ValueError("daily_turnover_limit must be in (0, 1]")
        if not Decimal("0") <= values["rebalance_deadband"] < Decimal("1"):
            raise ValueError("rebalance_deadband must be in [0, 1)")
        return cls(
            capital_mode=mode, capital_cap_krw=cap, **values,
            max_signal_age_days=int(raw["max_signal_age_days"]),
            min_order_krw=int(raw["min_order_krw"]),
            live_trading_enabled=bool(raw["live_trading_enabled"]),
            kill_switch=bool(raw["kill_switch"]),
        )


@dataclass(frozen=True)
class LiveConfig:
    cano: str
    account_product_code: str
    capital_mode: CapitalMode
    capital_cap_krw: int | None
    kis_base_url: str
    app_key: str
    app_secret: str
    token_cache_path: str
    notion_token_path: str
    trading_signals_database_id: str
    execution_log_database_id: str
    kill_switch: bool

    @classmethod
    def load(cls, path: str | Path, require_live: bool = True) -> "LiveConfig":
        p = Path(path)
        mode = p.stat().st_mode & 0o777
        if mode != 0o600:
            raise PermissionError(f"config permissions must be 0600, got {mode:04o}")
        raw = json.loads(p.read_text(encoding="utf-8"))
        raw["capital_mode"] = CapitalMode(raw.get("capital_mode", "FIXED_CAP"))
        if raw["capital_mode"] is CapitalMode.FIXED_CAP and not raw.get("capital_cap_krw"):
            raise ValueError("FIXED_CAP requires a positive capital_cap_krw")
        if raw["capital_mode"] is CapitalMode.AVAILABLE_BALANCE:
            raw["capital_cap_krw"] = None
        result = cls(**raw)
        if not re.fullmatch(r"\d{8}", result.cano):
            raise ValueError("cano must be the eight-digit account prefix")
        if not re.fullmatch(r"\d{2}", result.account_product_code):
            raise ValueError("account_product_code must be two digits")
        if result.kill_switch:
            raise RuntimeError("kill switch is active")
        if require_live and os.environ.get("LIVE_TRADING_ENABLED", "").lower() != "true":
            raise RuntimeError("LIVE_TRADING_ENABLED=true is required")
        return result
