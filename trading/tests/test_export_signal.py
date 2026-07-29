import json

from ai_berkshire_trading.export_signal import export
from ai_berkshire_trading.models import source_digest


def test_explicit_sidecar_export_only(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_SIGNAL_OUTBOX", str(tmp_path / "out"))
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"name": "legacy"}))
    assert export(str(report), "page") == 0
    assert not (tmp_path / "out").exists()
    report.write_text(json.dumps({"trade_signal": {
        "schema_version": "1", "analysis_id": "a1", "cycle_id": "c1",
        "code": "271560", "name": "오리온", "market": "KOSPI", "sector": "필수소비재",
        "analyzed_at": "2026-07-29T12:00:00+09:00", "verdict": "BUY",
        "targets_krw": {"bear": 90000, "base": 110000, "bull": 130000},
        "source_hash": source_digest("audited report"), "audit_status": "PASS",
    }}))
    assert export(str(report), "notion-page") == 0
    payload = json.loads((tmp_path / "out/a1.json").read_text())
    assert payload["source_page_id"] == "notion-page"
    assert payload["published_at"]
