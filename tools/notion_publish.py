#!/usr/bin/env python3
"""Notion 발행기 — "경제 분석" 페이지 하위에 종목 리서치 DB를 만들고 보고서를 등록.

AI Berkshire 한국 파이프라인 ⑤단계. 주간 딥리서치 결과를 Notion DB 행으로 등록.
헤드리스 크론 대비: MCP가 아닌 Notion REST API를 curl로 직접 호출.

인증: ~/.notion_token (또는 $NOTION_TOKEN). 인테그레이션을 대상 페이지에 '연결'해야 함.

사용법:
    python3 tools/notion_publish.py find-page "경제 분석"        # 페이지 id 검색
    python3 tools/notion_publish.py ensure-db <page_id>          # DB 생성/재사용 → data/notion_db.json
    python3 tools/notion_publish.py add report.json              # 보고서 1건 등록
        report.json 예: {"name":"삼성전자","code":"005930","market":"KOSPI",
                          "score":3.25,"verdict":"보류","one_liner":"...",
                          "s_biz":3.5,"s_fin":3.0,"s_ind":3.0,"s_risk":3.5,
                          "gross_margin":38.0,"ocf_ni":2.12,"fcf_eok":28442,
                          "body_md":"# ...전체 한국어 리포트 마크다운..."}

Python >= 3.8, 외부 의존성 없음.
"""

import argparse
import json
import os
import subprocess
import sys

_TIMEOUT = 30
_VER = "2022-06-28"
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DBFILE = os.path.join(_ROOT, "data", "notion_db.json")


def _token():
    t = os.environ.get("NOTION_TOKEN", "").strip()
    if not t:
        p = os.path.expanduser("~/.notion_token")
        if os.path.exists(p):
            t = open(p).read().strip()
    if not t:
        sys.stderr.write("❌ Notion 토큰 없음. ~/.notion_token 또는 $NOTION_TOKEN.\n")
        sys.exit(2)
    return t


def _api(method, path, body=None):
    args = ["/usr/bin/curl", "-s", "--noproxy", "*", "-X", method,
            f"https://api.notion.com/v1{path}",
            "-H", f"Authorization: Bearer {_token()}",
            "-H", f"Notion-Version: {_VER}",
            "-H", "Content-Type: application/json"]
    if body is not None:
        args += ["-d", json.dumps(body, ensure_ascii=False)]
    r = subprocess.run(args, capture_output=True, timeout=_TIMEOUT)
    data = json.loads(r.stdout.decode("utf-8", errors="replace") or "{}")
    if data.get("object") == "error":
        sys.stderr.write(f"⚠️ Notion API 오류 {data.get('status')}: {data.get('message')}\n")
    return data


# ---------------------------------------------------------------------------

def _title_of(r):
    props = r.get("properties", {})
    for v in props.values():
        if v.get("type") == "title":
            return "".join(x.get("plain_text", "") for x in v.get("title", []))
    return "".join(x.get("plain_text", "") for x in r.get("title", [])) or "(제목없음)"


def cmd_find_page(query):
    d = _api("POST", "/search", {"query": query,
             "filter": {"property": "object", "value": "page"}, "page_size": 20})
    res = d.get("results", [])
    if not res:
        print("⚠️ 접근 가능한 페이지 0개 — 인테그레이션을 대상 페이지에 '연결'했는지 확인하세요.")
        return
    for r in res:
        print(f"  page  {_title_of(r)!r}  id={r.get('id')}")


_SCHEMA = {
    "종목명": {"title": {}},
    "종목코드": {"rich_text": {}},
    "시장": {"select": {"options": [{"name": "KOSPI"}, {"name": "KOSDAQ"}]}},
    "종합점수": {"number": {"format": "number"}},
    "투자결론": {"select": {"options": [
        {"name": "매수", "color": "green"}, {"name": "보류", "color": "yellow"},
        {"name": "관망", "color": "orange"}, {"name": "제외", "color": "red"}]}},
    "사업모델(段)": {"number": {}}, "재무(버핏)": {"number": {}},
    "산업(멍거)": {"number": {}}, "리스크(리루)": {"number": {}},
    "매출총이익률": {"number": {}}, "OCF/NI": {"number": {}}, "FCF(억)": {"number": {}},
    "한줄결론": {"rich_text": {}},
    "분석일": {"date": {}},
}


def cmd_ensure_db(page_id):
    # 이미 만든 DB가 있으면 재사용
    if os.path.exists(_DBFILE):
        saved = json.load(open(_DBFILE))
        if saved.get("parent") == page_id:
            print(f"기존 DB 재사용: {saved['db_id']}")
            return saved["db_id"]
    d = _api("POST", "/databases", {
        "parent": {"type": "page_id", "page_id": page_id},
        "title": [{"type": "text", "text": {"content": "코스피·코스닥 종목 리서치"}}],
        "properties": _SCHEMA,
    })
    db_id = d.get("id")
    if not db_id:
        sys.stderr.write("❌ DB 생성 실패.\n"); sys.exit(1)
    os.makedirs(os.path.dirname(_DBFILE), exist_ok=True)
    json.dump({"db_id": db_id, "parent": page_id}, open(_DBFILE, "w"))
    print(f"✅ DB 생성: {db_id}\n   저장: {_DBFILE}")
    return db_id


def _rt(text):
    return [{"type": "text", "text": {"content": (text or "")[:2000]}}]


def _md_to_blocks(md):
    """마크다운을 Notion 블록으로(제목/불릿/표는 단순 문단, 2000자 제한)."""
    blocks = []
    for line in (md or "").splitlines():
        s = line.rstrip()
        if not s:
            continue
        if s.startswith("### "):
            blocks.append({"heading_3": {"rich_text": _rt(s[4:])}})
        elif s.startswith("## "):
            blocks.append({"heading_2": {"rich_text": _rt(s[3:])}})
        elif s.startswith("# "):
            blocks.append({"heading_1": {"rich_text": _rt(s[2:])}})
        elif s.startswith(("- ", "* ")):
            blocks.append({"bulleted_list_item": {"rich_text": _rt(s[2:])}})
        else:
            blocks.append({"paragraph": {"rich_text": _rt(s)}})
        if len(blocks) >= 95:  # Notion children 100개 제한 여유
            break
    return [{"object": "block", "type": list(b)[0], **b} for b in blocks]


def cmd_add(report_path):
    r = json.load(open(report_path, encoding="utf-8")) if report_path != "-" \
        else json.load(sys.stdin)
    if not os.path.exists(_DBFILE):
        sys.stderr.write("❌ DB가 없습니다. ensure-db 먼저.\n"); sys.exit(1)
    db_id = json.load(open(_DBFILE))["db_id"]

    def num(k):
        v = r.get(k)
        return {"number": float(v)} if v is not None else {"number": None}

    props = {
        "종목명": {"title": _rt(r.get("name", "?"))},
        "종목코드": {"rich_text": _rt(r.get("code", ""))},
        "시장": {"select": {"name": r.get("market", "KOSPI")}},
        "종합점수": num("score"),
        "투자결론": {"select": {"name": r.get("verdict", "보류")}},
        "사업모델(段)": num("s_biz"), "재무(버핏)": num("s_fin"),
        "산업(멍거)": num("s_ind"), "리스크(리루)": num("s_risk"),
        "매출총이익률": num("gross_margin"), "OCF/NI": num("ocf_ni"), "FCF(억)": num("fcf_eok"),
        "한줄결론": {"rich_text": _rt(r.get("one_liner", ""))},
        "분석일": {"date": {"start": r["date"]}} if r.get("date") else {"date": None},
    }
    body = {"parent": {"database_id": db_id}, "properties": props}
    blocks = _md_to_blocks(r.get("body_md", ""))
    if blocks:
        body["children"] = blocks
    d = _api("POST", "/pages", body)
    if d.get("id"):
        print(f"✅ 등록: {r.get('name')} → {d['id']}")
    else:
        sys.stderr.write("❌ 등록 실패.\n"); sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description="Notion 발행기")
    sub = ap.add_subparsers(dest="cmd", required=True)
    fp = sub.add_parser("find-page"); fp.add_argument("query")
    ed = sub.add_parser("ensure-db"); ed.add_argument("page_id")
    ad = sub.add_parser("add"); ad.add_argument("report", help="report.json 경로 또는 '-'(stdin)")
    args = ap.parse_args()
    if args.cmd == "find-page":
        cmd_find_page(args.query)
    elif args.cmd == "ensure-db":
        cmd_ensure_db(args.page_id)
    elif args.cmd == "add":
        cmd_add(args.report)


if __name__ == "__main__":
    main()
