#!/usr/bin/env python3
"""Notion 발행기 — "경제 분석" 하위에 월별 페이지 + 종목 리서치 DB, 최소 컬럼.

구조:
  경제 분석(루트) → 2026-06(월 페이지) → "종목 리서치" DB → 종목별 행
                  → 2026-07(월 페이지) → DB ...        (월 바뀌면 자동 새 페이지)

DB 컬럼(최소): 종목명(제목) · 종합점수 · 종합의견 · 한줄의견 · 분석일.
그 외 상세(시장·4대가 점수·재무·전문)는 각 행(페이지) 본문 안에 들어간다.

인증: ~/.notion_token (또는 $NOTION_TOKEN). 인테그레이션을 루트 페이지에 '연결'해야 함.
상태: data/notion_db.json = {"root": <page_id>, "months": {"YYYY-MM": {"page_id","db_id"}}}

사용법:
    python3 tools/notion_publish.py find-page "경제 분석"     # page_id 검색
    python3 tools/notion_publish.py set-root <page_id>       # 루트(경제 분석) 지정
    python3 tools/notion_publish.py ensure-month 2026-06     # 월 페이지+DB 생성/재사용
    python3 tools/notion_publish.py add report.json          # report.date의 월로 라우팅
"""

import argparse
import datetime
import json
import os
import subprocess
import sys

_KST = datetime.timezone(datetime.timedelta(hours=9))

_TIMEOUT = 30
_VER = "2022-06-28"
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CFG = os.path.join(_ROOT, "data", "notion_db.json")


def _token():
    t = os.environ.get("NOTION_TOKEN", "").strip()
    if not t:
        p = os.path.expanduser("~/.notion_token")
        if os.path.exists(p):
            t = open(p).read().strip()
    if not t:
        sys.stderr.write("❌ Notion 토큰 없음.\n"); sys.exit(2)
    return t


def _api(method, path, body=None):
    args = ["/usr/bin/curl", "-s", "--noproxy", "*", "-X", method,
            f"https://api.notion.com/v1{path}",
            "-H", f"Authorization: Bearer {_token()}",
            "-H", f"Notion-Version: {_VER}", "-H", "Content-Type: application/json"]
    if body is not None:
        args += ["-d", json.dumps(body, ensure_ascii=False)]
    r = subprocess.run(args, capture_output=True, timeout=_TIMEOUT)
    d = json.loads(r.stdout.decode("utf-8", errors="replace") or "{}")
    if d.get("object") == "error":
        sys.stderr.write(f"⚠️ Notion {d.get('status')}: {d.get('message')}\n")
    return d


def _cfg():
    return json.load(open(_CFG, encoding="utf-8")) if os.path.exists(_CFG) else {"root": None, "months": {}}


def _save_cfg(c):
    os.makedirs(os.path.dirname(_CFG), exist_ok=True)
    json.dump(c, open(_CFG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def _rt(text):
    return [{"type": "text", "text": {"content": (text or "")[:2000]}}]


def _title_of(r):
    for v in r.get("properties", {}).values():
        if v.get("type") == "title":
            return "".join(x.get("plain_text", "") for x in v.get("title", []))
    return "".join(x.get("plain_text", "") for x in r.get("title", [])) or "(제목없음)"


def cmd_find_page(query):
    d = _api("POST", "/search", {"query": query,
             "filter": {"property": "object", "value": "page"}, "page_size": 20})
    res = d.get("results", [])
    if not res:
        print("⚠️ 접근 가능한 페이지 0개 — 인테그레이션 '연결' 확인.")
    for r in res:
        print(f"  page  {_title_of(r)!r}  id={r.get('id')}")


def cmd_set_root(page_id):
    c = _cfg(); c["root"] = page_id; _save_cfg(c)
    print(f"✅ 루트(경제 분석) 지정: {page_id}")


# 최소 컬럼 스키마
_SCHEMA = {
    "종목명": {"title": {}},
    "종합점수": {"number": {"format": "number"}},
    "종합의견": {"select": {"options": [
        {"name": "매수", "color": "green"}, {"name": "보류", "color": "yellow"},
        {"name": "관망", "color": "orange"}, {"name": "제외", "color": "red"}]}},
    "한줄의견": {"rich_text": {}},
    "분석일": {"date": {}},
}


def _ensure_month(month):
    """월 페이지 + DB 보장. (page_id, db_id) 반환."""
    c = _cfg()
    if not c.get("root"):
        sys.stderr.write("❌ 루트 미지정. set-root <경제분석 page_id> 먼저.\n"); sys.exit(1)
    if month in c["months"]:
        return c["months"][month]["page_id"], c["months"][month]["db_id"]
    # 월 페이지 생성
    pg = _api("POST", "/pages", {
        "parent": {"type": "page_id", "page_id": c["root"]},
        "properties": {"title": {"title": _rt(f"{month} 종목 분석")}},
    })
    page_id = pg.get("id")
    if not page_id:
        sys.stderr.write("❌ 월 페이지 생성 실패.\n"); sys.exit(1)
    # DB 생성
    db = _api("POST", "/databases", {
        "parent": {"type": "page_id", "page_id": page_id},
        "title": _rt(f"코스피·코스닥 종목 리서치 ({month})"),
        "properties": _SCHEMA,
    })
    db_id = db.get("id")
    if not db_id:
        sys.stderr.write("❌ DB 생성 실패.\n"); sys.exit(1)
    c["months"][month] = {"page_id": page_id, "db_id": db_id}
    _save_cfg(c)
    print(f"✅ 월 구조 생성: {month} (page {page_id[:8]}…, db {db_id[:8]}…)")
    return page_id, db_id


def cmd_ensure_month(month):
    _ensure_month(month)


def _md_to_blocks(md):
    blocks = []
    for line in (md or "").splitlines():
        s = line.rstrip()
        if not s:
            continue
        if s.startswith("### "):
            b = {"heading_3": {"rich_text": _rt(s[4:])}}
        elif s.startswith("## "):
            b = {"heading_2": {"rich_text": _rt(s[3:])}}
        elif s.startswith("# "):
            b = {"heading_1": {"rich_text": _rt(s[2:])}}
        elif s.startswith(("- ", "* ")):
            b = {"bulleted_list_item": {"rich_text": _rt(s[2:])}}
        else:
            b = {"paragraph": {"rich_text": _rt(s)}}
        blocks.append({"object": "block", "type": list(b)[0], **b})
        if len(blocks) >= 95:
            break
    return blocks


def _detail_header(r):
    """행 본문 맨 위에 넣을 요약(시장·4대가·재무) 마크다운."""
    def s(x):
        return "-" if x is None else x
    return (
        f"## 요약\n"
        f"- 시장/코드: {s(r.get('market'))} {s(r.get('code'))}\n"
        f"- 4대가 점수 — 돤융핑(사업) {s(r.get('s_biz'))} · 버핏(재무) {s(r.get('s_fin'))} · "
        f"멍거(산업) {s(r.get('s_ind'))} · 리루(리스크) {s(r.get('s_risk'))}\n"
        f"- 재무 — 매출총이익률 {s(r.get('gross_margin'))}% · OCF/NI {s(r.get('ocf_ni'))} · "
        f"누적FCF {s(r.get('fcf_eok'))}억\n"
    )


def cmd_add(report_path, month=None):
    r = json.load(open(report_path, encoding="utf-8")) if report_path != "-" else json.load(sys.stdin)
    month = month or (r.get("date") or "")[:7]
    if not month or len(month) != 7:
        sys.stderr.write("❌ 월(YYYY-MM)을 결정할 수 없음. report.date 또는 --month 필요.\n"); sys.exit(1)
    _, db_id = _ensure_month(month)

    props = {
        "종목명": {"title": _rt(r.get("name", "?"))},
        "종합점수": {"number": float(r["score"]) if r.get("score") is not None else None},
        "종합의견": {"select": {"name": r.get("verdict", "보류")}},
        "한줄의견": {"rich_text": _rt(r.get("one_liner", ""))},
        # 분석일 = 실제 분석완료·업로드 시각(KST, 시:분:초 포함)
        "분석일": {"date": {"start": datetime.datetime.now(_KST).replace(microsecond=0).isoformat()}},
    }
    body_md = _detail_header(r) + "\n" + (r.get("body_md", "") or "")
    d = _api("POST", "/pages", {"parent": {"database_id": db_id},
                                "properties": props, "children": _md_to_blocks(body_md)})
    if d.get("id"):
        print(f"✅ 등록: {r.get('name')} [{month}] → {d['id']}")
    else:
        sys.stderr.write("❌ 등록 실패.\n"); sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description="Notion 발행기 (월별 페이지 + 최소 컬럼)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    fp = sub.add_parser("find-page"); fp.add_argument("query")
    sr = sub.add_parser("set-root"); sr.add_argument("page_id")
    em = sub.add_parser("ensure-month"); em.add_argument("month")
    ad = sub.add_parser("add"); ad.add_argument("report"); ad.add_argument("--month")
    a = ap.parse_args()
    {"find-page": lambda: cmd_find_page(a.query),
     "set-root": lambda: cmd_set_root(a.page_id),
     "ensure-month": lambda: cmd_ensure_month(a.month),
     "add": lambda: cmd_add(a.report, a.month)}[a.cmd]()


if __name__ == "__main__":
    main()
