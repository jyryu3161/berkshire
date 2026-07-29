from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
from typing import Any


_FORBIDDEN = {
    "account", "account_number", "cano", "app_key", "app_secret",
    "authorization", "access_token", "token", "broker_order_ref",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "event": getattr(record, "event", record.getMessage()),
        }
        detail = getattr(record, "detail", {})
        fields["detail"] = redact(detail)
        return json.dumps(fields, ensure_ascii=False, separators=(",", ":"))


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("[REDACTED]" if key.lower() in _FORBIDDEN else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def runtime_logger(path: str | Path) -> logging.Logger:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"ai_berkshire_trading.{target.resolve()}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = RotatingFileHandler(
            target, maxBytes=10 * 1024 * 1024, backupCount=10, encoding="utf-8"
        )
        os.chmod(target, 0o600)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    return logger


def log_event(logger: logging.Logger, event: str, **detail: Any) -> None:
    logger.info(event, extra={"event": event, "detail": detail})
