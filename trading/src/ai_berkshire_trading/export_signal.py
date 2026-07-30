from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from datetime import datetime

from .models import AnalysisSnapshot, KST


def export(report_path: str, source_page_id: str) -> int:
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    sidecar = report.get("trade_signal")
    if not sidecar:
        return 0
    payload = dict(sidecar)
    payload["source_page_id"] = source_page_id
    payload["published_at"] = datetime.now(KST).replace(microsecond=0).isoformat()
    signal = AnalysisSnapshot.from_dict(payload)
    outbox = Path(os.environ.get("TRADING_SIGNAL_OUTBOX", Path(__file__).parents[3] / "local/trading/signals"))
    outbox.mkdir(parents=True, exist_ok=True)
    target = outbox / f"{signal.analysis_id}.json"
    if not target.exists():
        target.write_text(signal.canonical_json() + "\n", encoding="utf-8")
    return 0


def main() -> None:
    try:
        raise SystemExit(export(sys.argv[1], sys.argv[2]))
    except SystemExit:
        raise
    except Exception as exc:
        print(f"signal export rejected: {exc}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
