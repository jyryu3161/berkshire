from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import hashlib
import json
import re
from typing import Any

KST = timezone(timedelta(hours=9))


class Verdict(StrEnum):
    BUY = "BUY"
    HOLD = "HOLD"
    WATCH = "WATCH"
    EXCLUDE = "EXCLUDE"


@dataclass(frozen=True)
class Targets:
    bear: int
    base: int
    bull: int

    def validate(self) -> None:
        if isinstance(self.bear, bool) or not all(
            isinstance(v, int) for v in (self.bear, self.base, self.bull)
        ):
            raise ValueError("targets_krw values must be integer KRW")
        if not 0 < self.bear < self.base < self.bull:
            raise ValueError("targets_krw must satisfy 0 < bear < base < bull")


@dataclass(frozen=True)
class AnalysisSnapshot:
    schema_version: str
    analysis_id: str
    cycle_id: str
    code: str
    name: str
    market: str
    sector: str | None
    analyzed_at: datetime
    published_at: datetime
    verdict: Verdict
    targets_krw: Targets | None
    source_page_id: str
    source_hash: str
    audit_status: str
    score: float | None = None  # 4대가 종합점수(5점 만점) — 관망 진입 자격 판정용

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AnalysisSnapshot":
        targets = value.get("targets_krw")
        result = cls(
            schema_version=str(value["schema_version"]),
            analysis_id=str(value["analysis_id"]),
            cycle_id=str(value["cycle_id"]),
            code=str(value["code"]),
            name=str(value["name"]),
            market=str(value["market"]),
            sector=str(value["sector"]) if value.get("sector") else None,
            analyzed_at=_datetime(value["analyzed_at"]),
            published_at=_datetime(value["published_at"]),
            verdict=Verdict(value["verdict"]),
            targets_krw=Targets(**targets) if targets is not None else None,
            source_page_id=str(value["source_page_id"]),
            source_hash=str(value["source_hash"]),
            audit_status=str(value["audit_status"]),
            score=float(value["score"]) if value.get("score") is not None else None,
        )
        result.validate()
        return result

    def validate(self) -> None:
        if self.schema_version != "1":
            raise ValueError("unsupported schema_version")
        if not re.fullmatch(r"\d{6}", self.code):
            raise ValueError("code must contain exactly six digits")
        if not self.analysis_id or not self.cycle_id or not self.name:
            raise ValueError("analysis_id, cycle_id and name are required")
        if self.analyzed_at.tzinfo is None or self.published_at.tzinfo is None:
            raise ValueError("timestamps must include a timezone")
        if self.audit_status != "PASS":
            raise ValueError("audit_status must be PASS")
        if not re.fullmatch(r"[0-9a-f]{64}", self.source_hash):
            raise ValueError("source_hash must be a sha256 hex digest")
        if self.verdict is Verdict.BUY and self.targets_krw is None:
            raise ValueError("BUY requires targets_krw")
        if self.verdict is Verdict.BUY and not self.sector:
            raise ValueError("BUY requires sector for portfolio concentration limits")
        if self.targets_krw:
            self.targets_krw.validate()
        if self.score is not None and not 0 <= self.score <= 5:
            raise ValueError("score must be within 0..5")

    def is_stale(self, now: datetime, max_age_days: int = 90) -> bool:
        return now.astimezone(timezone.utc) - self.analyzed_at.astimezone(timezone.utc) > timedelta(days=max_age_days)

    def canonical_json(self) -> str:
        payload = {
            "schema_version": self.schema_version, "analysis_id": self.analysis_id,
            "cycle_id": self.cycle_id, "code": self.code, "name": self.name,
            "market": self.market, "sector": self.sector,
            "analyzed_at": self.analyzed_at.isoformat(),
            "published_at": self.published_at.isoformat(), "verdict": self.verdict.value,
            "targets_krw": vars(self.targets_krw) if self.targets_krw else None,
            "source_page_id": self.source_page_id, "source_hash": self.source_hash,
            "audit_status": self.audit_status,
        }
        # 구버전 신호와의 payload 호환을 위해 score는 있을 때만 싣는다.
        if self.score is not None:
            payload["score"] = self.score
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def source_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _datetime(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed
