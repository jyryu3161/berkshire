from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from typing import Protocol

import httpx

from .models import AnalysisSnapshot
from .runtime_log import log_event, redact


class ExecutionLogger(Protocol):
    def preflight(self, run_id: str, message: str) -> bool: ...
    def event(self, run_id: str, event: str, detail: dict) -> None: ...


class SignalSink(Protocol):
    def append(self, signal: AnalysisSnapshot) -> str: ...


def signal_properties(signal: AnalysisSnapshot) -> dict:
    """Notion payload without account, credentials, or full broker order numbers."""
    targets = signal.targets_krw
    return {
        "Analysis ID": {"title": [{"text": {"content": signal.analysis_id}}]},
        "Code": {"rich_text": [{"text": {"content": signal.code}}]},
        "Name": {"rich_text": [{"text": {"content": signal.name}}]},
        "Sector": {"rich_text": [{"text": {"content": signal.sector or ""}}]},
        "Verdict": {"select": {"name": signal.verdict.value}},
        "Analyzed At": {"date": {"start": signal.analyzed_at.isoformat()}},
        "Published At": {"date": {"start": signal.published_at.isoformat()}},
        "Bear": {"number": targets.bear if targets else None},
        "Base": {"number": targets.base if targets else None},
        "Bull": {"number": targets.bull if targets else None},
        "Source Page": {"rich_text": [{"text": {"content": signal.source_page_id}}]},
    }


_NOTION_VERSION = "2022-06-28"


class NotionClient:
    def __init__(self, token: str, transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(
            base_url="https://api.notion.com", timeout=15.0, transport=transport,
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": _NOTION_VERSION,
                "Content-Type": "application/json",
            },
        )

    def title_property(self, database_id: str) -> str:
        response = self._client.get(f"/v1/databases/{database_id}")
        response.raise_for_status()
        for name, spec in (response.json().get("properties") or {}).items():
            if spec.get("type") == "title":
                return name
        raise RuntimeError("database has no title property")

    def create_page(self, database_id: str, properties: dict,
                    children: list[dict] | None = None) -> str:
        payload: dict = {"parent": {"database_id": database_id}, "properties": properties}
        if children:
            payload["children"] = children
        response = self._client.post("/v1/pages", json=payload)
        response.raise_for_status()
        return str(response.json().get("id", ""))


def _paragraph(text: str) -> dict:
    return {
        "object": "block", "type": "paragraph",
        "paragraph": {"rich_text": [{"text": {"content": text[:1900]}}]},
    }


class NotionExecutionLogger:
    """ExecutionLogger backed by a Notion database.

    ``preflight`` is strict — a failure blocks the run before any order is
    sent. ``event`` is best-effort — once orders exist, a Notion outage must
    never interrupt execution, so failures degrade to the local runtime log,
    which receives every event regardless.
    """

    def __init__(self, token: str, database_id: str, runtime: logging.Logger,
                 transport: httpx.BaseTransport | None = None):
        self.database_id = database_id
        self.runtime = runtime
        self._client = NotionClient(token, transport)
        self._title_property: str | None = None

    def _write(self, run_id: str, event: str, detail: dict) -> None:
        if self._title_property is None:
            self._title_property = self._client.title_property(self.database_id)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        title = f"{stamp} {event} {run_id[:8]}"
        body = json.dumps(redact(detail), ensure_ascii=False, sort_keys=True)
        self._client.create_page(
            self.database_id,
            {self._title_property: {"title": [{"text": {"content": title}}]}},
            [_paragraph(body)],
        )

    def preflight(self, run_id: str, message: str) -> bool:
        try:
            self._write(run_id, "PREFLIGHT", {"message": message})
            return True
        except Exception as exc:
            log_event(self.runtime, "NOTION_PREFLIGHT_FAILED", run_id=run_id, error=str(exc))
            return False

    def event(self, run_id: str, event: str, detail: dict) -> None:
        log_event(self.runtime, event, **{**detail, "run_id": run_id})
        try:
            self._write(run_id, event, detail)
        except Exception as exc:
            log_event(self.runtime, "NOTION_EVENT_FAILED", run_id=run_id,
                      event_name=event, error=str(exc))


class NotionSignalSink:
    """Mirror validated trade signals into the Notion signals database."""

    def __init__(self, token: str, database_id: str,
                 transport: httpx.BaseTransport | None = None):
        self.database_id = database_id
        self._client = NotionClient(token, transport)

    def append(self, signal: AnalysisSnapshot) -> str:
        return self._client.create_page(self.database_id, signal_properties(signal))
