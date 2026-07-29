from datetime import datetime, timedelta, timezone
import pytest

from ai_berkshire_trading.models import AnalysisSnapshot, Targets


def test_buy_requires_ordered_integer_targets(signal):
    signal().validate()
    with pytest.raises(ValueError):
        signal(targets_krw=Targets(70_000, 50_000, 90_000)).validate()
    with pytest.raises(ValueError):
        signal(targets_krw=None).validate()
    with pytest.raises(ValueError):
        signal(sector=None).validate()


def test_code_audit_and_age(signal):
    with pytest.raises(ValueError):
        signal(code="21240").validate()
    with pytest.raises(ValueError):
        signal(audit_status="UNKNOWN").validate()
    old = signal(analyzed_at=datetime.now(timezone.utc) - timedelta(days=121))
    assert old.is_stale(datetime.now(timezone.utc))


def test_nonbuy_targets_optional(signal):
    raw = {
        **__import__("json").loads(signal().canonical_json()),
        "analysis_id": "a-2", "verdict": "HOLD", "targets_krw": None,
    }
    AnalysisSnapshot.from_dict(raw).validate()
