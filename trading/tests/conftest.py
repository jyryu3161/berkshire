from datetime import datetime, timezone
import pytest

from ai_berkshire_trading.models import AnalysisSnapshot, Targets, Verdict, source_digest


@pytest.fixture
def signal():
    def make(**changes):
        values = dict(
            schema_version="1", analysis_id="a-1", cycle_id="cycle-1", code="021240",
            name="코웨이", market="KOSPI", analyzed_at=datetime.now(timezone.utc),
            sector="필수소비재",
            published_at=datetime.now(timezone.utc), verdict=Verdict.BUY,
            targets_krw=Targets(50_000, 70_000, 90_000), source_page_id="page",
            source_hash=source_digest("report"), audit_status="PASS",
        )
        values.update(changes)
        return AnalysisSnapshot(**values)
    return make
