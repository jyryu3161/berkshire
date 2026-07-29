import json

import httpx

from ai_berkshire_trading.notion import NotionExecutionLogger, NotionSignalSink
from ai_berkshire_trading.runtime_log import runtime_logger


class FakeNotion:
    def __init__(self, fail=False):
        self.fail = fail
        self.pages = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.fail:
            return httpx.Response(503, json={"message": "down"})
        if request.method == "GET" and "/v1/databases/" in request.url.path:
            return httpx.Response(200, json={
                "properties": {"이벤트": {"type": "title"}, "메모": {"type": "rich_text"}},
            })
        if request.method == "POST" and request.url.path == "/v1/pages":
            self.pages.append(json.loads(request.content))
            return httpx.Response(200, json={"id": f"page-{len(self.pages)}"})
        raise AssertionError(f"unexpected {request.method} {request.url.path}")


def test_preflight_strict_and_event_best_effort(tmp_path):
    fake = FakeNotion()
    runtime = runtime_logger(tmp_path / "runtime.jsonl")
    logger = NotionExecutionLogger(
        "tok", "db", runtime, transport=httpx.MockTransport(fake.handle)
    )
    assert logger.preflight("run-1", "hello")
    title = fake.pages[0]["properties"]["이벤트"]["title"][0]["text"]["content"]
    assert "PREFLIGHT" in title
    logger.event("run-1", "ORDER_SUBMITTED", {
        "code": "021240", "broker_order_ref": "0000000001",
    })
    body = fake.pages[1]["children"][0]["paragraph"]["rich_text"][0]["text"]["content"]
    assert "[REDACTED]" in body and "0000000001" not in body
    local = (tmp_path / "runtime.jsonl").read_text()
    assert "ORDER_SUBMITTED" in local


def test_notion_outage_blocks_preflight_but_not_events(tmp_path):
    fake = FakeNotion(fail=True)
    runtime = runtime_logger(tmp_path / "runtime.jsonl")
    logger = NotionExecutionLogger(
        "tok", "db", runtime, transport=httpx.MockTransport(fake.handle)
    )
    assert not logger.preflight("run-1", "hello")
    logger.event("run-1", "ORDER_SUBMITTED", {"code": "021240"})  # must not raise
    local = (tmp_path / "runtime.jsonl").read_text()
    assert "NOTION_PREFLIGHT_FAILED" in local and "NOTION_EVENT_FAILED" in local


def test_signal_sink_appends(tmp_path, signal):
    fake = FakeNotion()
    sink = NotionSignalSink("tok", "db", transport=httpx.MockTransport(fake.handle))
    assert sink.append(signal()) == "page-1"
    assert fake.pages[0]["properties"]["Verdict"]["select"]["name"] == "BUY"
