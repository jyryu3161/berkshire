import json
import os

from ai_berkshire_trading.runtime_log import log_event, runtime_logger


def test_json_log_redacts_secrets(tmp_path):
    path = tmp_path / "runtime.jsonl"
    logger = runtime_logger(path)
    log_event(logger, "BALANCE_READ", total_krw=1_000_000, account_number="12345678")
    row = json.loads(path.read_text())
    assert row["event"] == "BALANCE_READ"
    assert row["detail"]["total_krw"] == 1_000_000
    assert row["detail"]["account_number"] == "[REDACTED]"
    assert os.stat(path).st_mode & 0o777 == 0o600
