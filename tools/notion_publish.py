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
import re
import subprocess
import sys
import time

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
    """마크다운 인라인(**굵게**)을 Notion rich_text로 변환. 짝지은 '**'를 굵게 런으로.
    (과거엔 별표를 리터럴로 저장 → 재발행 시 진짜 굵게로 정규화)."""
    text = (text or "")[:1990]
    segs = text.split("**")
    rts, bold = [], False
    for seg in segs:
        if seg:
            rt = {"type": "text", "text": {"content": seg[:2000]}}
            if bold:
                rt["annotations"] = {"bold": True}
            rts.append(rt)
        bold = not bold
    return rts or [{"type": "text", "text": {"content": ""}}]


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


def _ensure_bucket(bucket):
    """버킷(사이클 또는 월) 페이지 + DB 보장. (page_id, db_id) 반환.

    bucket 문자열이 그대로 라우팅 키·제목에 쓰인다.
    예) '사이클 2 (2026-07~)' 또는 back-compat 월 '2026-07'.
    (상태파일 dict 키는 back-compat 위해 'months'를 그대로 재사용 — 임의 문자열 키 허용.)
    """
    c = _cfg()
    if not c.get("root"):
        sys.stderr.write("❌ 루트 미지정. set-root <경제분석 page_id> 먼저.\n"); sys.exit(1)
    if bucket in c["months"]:
        return c["months"][bucket]["page_id"], c["months"][bucket]["db_id"]
    # 버킷 페이지 생성
    pg = _api("POST", "/pages", {
        "parent": {"type": "page_id", "page_id": c["root"]},
        "properties": {"title": {"title": _rt(f"{bucket} 종목 분석")}},
    })
    page_id = pg.get("id")
    if not page_id:
        sys.stderr.write("❌ 버킷 페이지 생성 실패.\n"); sys.exit(1)
    # DB 생성
    db = _api("POST", "/databases", {
        "parent": {"type": "page_id", "page_id": page_id},
        "title": _rt(f"코스피·코스닥 종목 리서치 · {bucket}"),
        "properties": _SCHEMA,
    })
    db_id = db.get("id")
    if not db_id:
        sys.stderr.write("❌ DB 생성 실패.\n"); sys.exit(1)
    c["months"][bucket] = {"page_id": page_id, "db_id": db_id}
    _save_cfg(c)
    print(f"✅ 버킷 구조 생성: {bucket} (page {page_id[:8]}…, db {db_id[:8]}…)")
    return page_id, db_id


def cmd_ensure_bucket(bucket):
    _ensure_bucket(bucket)


def cmd_archive_bucket(bucket):
    """버킷 페이지를 아카이브(휴지통 이동, 하위 DB·행 포함)하고 설정에서 제거."""
    c = _cfg()
    if bucket not in c["months"]:
        sys.stderr.write(f"❌ 버킷 없음: {bucket}\n"); sys.exit(1)
    page_id = c["months"][bucket]["page_id"]
    d = _api("PATCH", f"/pages/{page_id}", {"archived": True})
    if d.get("object") == "error":
        sys.stderr.write("❌ 아카이브 실패.\n"); sys.exit(1)
    del c["months"][bucket]
    _save_cfg(c)
    print(f"✅ 아카이브: {bucket} (page {page_id[:8]}… 휴지통 이동)")


# ── 버킷 병합 (Notion→Notion 행+본문 복사) ─────────────────────────
_CONTAINER = {"table", "column_list", "column", "toggle", "callout",
              "quote", "bulleted_list_item", "numbered_list_item",
              "paragraph", "synced_block", "template", "to_do"}


def _clean_rt(rts):
    """rich_text 배열을 생성 가능한 최소형으로 정리(읽기전용 필드 제거)."""
    out = []
    for x in rts or []:
        if x.get("type", "text") == "text" and x.get("text"):
            item = {"type": "text",
                    "text": {"content": x["text"].get("content", "")}}
            if x["text"].get("link"):
                item["text"]["link"] = x["text"]["link"]
        else:  # mention/equation → plain_text로 대체
            item = {"type": "text", "text": {"content": x.get("plain_text", "")}}
        ann = x.get("annotations")
        if ann:
            item["annotations"] = {k: ann[k] for k in
                ("bold", "italic", "strikethrough", "underline", "code", "color")
                if k in ann}
        out.append(item)
    return out


def _strip_nulls(o):
    """Notion 생성 API는 명시적 null을 거부 → dict에서 None 값 키 재귀 제거."""
    if isinstance(o, dict):
        return {k: _strip_nulls(v) for k, v in o.items() if v is not None}
    if isinstance(o, list):
        return [_strip_nulls(x) for x in o]
    return o


def _get_children(block_id):
    """블록 자식 전체(페이지네이션)."""
    kids, cur = [], None
    while True:
        q = f"/blocks/{block_id}/children?page_size=100"
        if cur:
            q += f"&start_cursor={cur}"
        d = _api("GET", q)
        kids += d.get("results", [])
        if not d.get("has_more"):
            break
        cur = d.get("next_cursor")
        time.sleep(0.2)
    return kids


def _sanitize_block(b, depth=0):
    """GET 블록 → 생성 가능한 블록으로 정리(자식은 depth<2까지 재귀 임베드)."""
    t = b.get("type")
    if not t or t in ("unsupported", "child_page", "child_database"):
        return None
    p = dict(b.get(t, {}) or {})
    p.pop("children", None)
    # 미디어(image/file/…): 외부 URL만 재생성 가능. Notion 호스팅(만료 URL)은 스킵.
    if t in ("image", "file", "video", "pdf", "audio"):
        if p.get("type") == "external" and p.get("external", {}).get("url"):
            p = {"type": "external", "external": {"url": p["external"]["url"]}}
        else:
            return None
    if "rich_text" in p:
        p["rich_text"] = _clean_rt(p["rich_text"])
    if t == "table_row" and "cells" in p:
        p["cells"] = [_clean_rt(c) for c in p["cells"]]
    if b.get("has_children") and t in _CONTAINER and depth < 2:
        kids = [_sanitize_block(k, depth + 1) for k in _get_children(b["id"])]
        kids = [k for k in kids if k]
        if kids:
            p["children"] = kids
    return {"object": "block", "type": t, t: _strip_nulls(p)}


def _props_for_copy(page):
    P = page.get("properties", {})
    def _txt(name, kind):
        return "".join(x.get("plain_text", "") for x in P.get(name, {}).get(kind, []))
    out = {"종목명": {"title": _rt(_txt("종목명", "title") or "?")},
           "종합점수": {"number": P.get("종합점수", {}).get("number")},
           "한줄의견": {"rich_text": _rt(_txt("한줄의견", "rich_text"))}}
    sel = P.get("종합의견", {}).get("select")
    if sel and sel.get("name"):
        out["종합의견"] = {"select": {"name": sel["name"]}}
    dt = P.get("분석일", {}).get("date")
    if dt and dt.get("start"):
        out["분석일"] = {"date": {"start": dt["start"]}}
    return out


def cmd_migrate_bucket(src, dst):
    """src 버킷 DB의 모든 행(속성+본문)을 dst 버킷 DB로 복사. src는 남겨둠(별도 archive)."""
    c = _cfg()
    if src not in c["months"]:
        sys.stderr.write(f"❌ 원본 버킷 없음: {src}\n"); sys.exit(1)
    src_db = c["months"][src]["db_id"]
    _, dst_db = _ensure_bucket(dst)
    # src DB 행 수집(페이지네이션)
    rows, cur = [], None
    while True:
        body = {"page_size": 100}
        if cur:
            body["start_cursor"] = cur
        d = _api("POST", f"/databases/{src_db}/query", body)
        rows += d.get("results", [])
        if not d.get("has_more"):
            break
        cur = d.get("next_cursor"); time.sleep(0.3)
    print(f"  원본 {src}: {len(rows)}행 → {dst} 로 복사")
    ok = 0
    for i, page in enumerate(rows, 1):
        props = _props_for_copy(page)
        name = "".join(x.get("plain_text", "")
                       for x in page.get("properties", {}).get("종목명", {}).get("title", []))
        children = [b for b in
                    (_sanitize_block(k) for k in _get_children(page["id"])) if b]
        first, rest = children[:100], children[100:]
        pg = _api("POST", "/pages", {"parent": {"database_id": dst_db},
                                     "properties": props, "children": first})
        pid = pg.get("id")
        if not pid:
            sys.stderr.write(f"  ⚠️ 실패: {name}\n"); continue
        j = 0
        while j < len(rest):
            _api("PATCH", f"/blocks/{pid}/children", {"children": rest[j:j+100]})
            j += 100; time.sleep(0.35)
        ok += 1
        print(f"  [{i}/{len(rows)}] ✅ {name} ({len(children)}블록)")
        time.sleep(0.4)
    print(f"✅ 병합 완료: {ok}/{len(rows)}행 → {dst}")


# ── 마크다운 표 → Notion table 블록 ─────────────────────────────
def _is_table_row(s):
    """'| a | b |' 형태의 표 행인가. 셀 구분 파이프가 2개 이상."""
    s = s.strip()
    return s.startswith("|") and s.count("|") >= 2


def _is_sep_row(s):
    """'|---|:--:|' 같은 구분선인가."""
    body = s.strip().strip("|")
    cells = body.split("|")
    return bool(cells) and all(("-" in c and set(c.strip()) <= set("-: ")) for c in cells if c.strip() != "") \
        and any("-" in c for c in cells)


def _split_row(s):
    """표 행을 셀 리스트로. 앞뒤 파이프 제거 후 분리."""
    s = s.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    # 이스케이프된 파이프(\|)는 셀 내부 문자로 복원
    parts, buf, i = [], [], 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            buf.append("|"); i += 2; continue
        if s[i] == "|":
            parts.append("".join(buf)); buf = []; i += 1; continue
        buf.append(s[i]); i += 1
    parts.append("".join(buf))
    return [c.strip() for c in parts]


def _table_block(rows, has_header=True):
    """rows: list[list[str]] → Notion table 블록(행은 자식으로 인라인)."""
    width = max((len(r) for r in rows), default=1) or 1

    def cell(txt):
        return [{"type": "text", "text": {"content": (txt or "")[:2000]}}]

    def row(cells):
        cells = (list(cells) + [""] * width)[:width]
        return {"object": "block", "type": "table_row",
                "table_row": {"cells": [cell(c) for c in cells]}}

    return {"object": "block", "type": "table", "table": {
        "table_width": width,
        "has_column_header": bool(has_header),
        "has_row_header": False,
        "children": [row(r) for r in rows],
    }}


def _md_to_blocks(md, cap=95):
    lines = (md or "").splitlines()
    blocks = []
    i, n = 0, len(lines)
    while i < n:
        s = lines[i].rstrip()
        if not s:
            i += 1
            continue
        # 표 감지: 현재 줄이 표 행이고, (다음 줄이 구분선) 또는 (다음 줄도 표 행)
        if _is_table_row(s) and i + 1 < n and (
                _is_sep_row(lines[i + 1]) or _is_table_row(lines[i + 1])):
            rows = []
            while i < n and _is_table_row(lines[i]):
                if not _is_sep_row(lines[i]):
                    rows.append(_split_row(lines[i]))
                i += 1
            if rows:
                blocks.append(_table_block(rows, has_header=True))
                if cap and len(blocks) >= cap:
                    break
            continue
        # 코드펜스(``` ... ```) — 여러 줄을 하나의 code 블록으로
        if s.startswith("```"):
            buf = []
            i += 1
            while i < n and not lines[i].rstrip().startswith("```"):
                buf.append(lines[i]); i += 1
            i += 1  # 닫는 펜스 소비
            blocks.append({"object": "block", "type": "code",
                           "code": {"rich_text": _rt("\n".join(buf)),
                                    "language": "plain text"}})
            if cap and len(blocks) >= cap:
                break
            continue
        m_num = re.match(r"^(\d+)\.\s+(.*)$", s)
        if s.startswith("### "):
            b = {"heading_3": {"rich_text": _rt(s[4:])}}
        elif s.startswith("## "):
            b = {"heading_2": {"rich_text": _rt(s[3:])}}
        elif s.startswith("# "):
            b = {"heading_1": {"rich_text": _rt(s[2:])}}
        elif s.startswith("> "):
            b = {"quote": {"rich_text": _rt(s[2:])}}
        elif s.startswith(("- ", "* ")):
            b = {"bulleted_list_item": {"rich_text": _rt(s[2:])}}
        elif m_num:
            b = {"numbered_list_item": {"rich_text": _rt(m_num.group(2))}}
        else:
            b = {"paragraph": {"rich_text": _rt(s)}}
        blocks.append({"object": "block", "type": list(b)[0], **b})
        i += 1
        if cap and len(blocks) >= cap:
            break
    return blocks


def _md_to_blocks_full(md):
    """캡 없이 전체 변환(retrofit·재구성용)."""
    return _md_to_blocks(md, cap=None)


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


def _append_chunked(page_id, blocks):
    """블록 리스트를 100개씩 나눠 append(Notion children 상한 100 우회)."""
    j = 0
    while j < len(blocks):
        _api("PATCH", f"/blocks/{page_id}/children", {"children": blocks[j:j + 100]})
        j += 100
        time.sleep(0.35)


def _rt_to_md(rts):
    """rich_text 배열 → 마크다운 인라인(굵게만 보존)."""
    out = []
    for x in rts or []:
        t = x.get("plain_text", x.get("text", {}).get("content", "") if x.get("text") else "")
        if not t:
            continue
        if x.get("annotations", {}).get("bold"):
            t = f"**{t}**"
        out.append(t)
    return "".join(out)


def _block_to_md(b, blocks_by_parent=None):
    """단일 블록 → 마크다운 줄(들). 표는 자식 table_row로 파이프표 생성."""
    t = b.get("type")
    p = b.get(t, {}) or {}
    if t == "heading_1":
        return "# " + _rt_to_md(p.get("rich_text"))
    if t == "heading_2":
        return "## " + _rt_to_md(p.get("rich_text"))
    if t == "heading_3":
        return "### " + _rt_to_md(p.get("rich_text"))
    if t == "bulleted_list_item":
        return "- " + _rt_to_md(p.get("rich_text"))
    if t == "numbered_list_item":
        return "1. " + _rt_to_md(p.get("rich_text"))
    if t == "quote":
        return "> " + _rt_to_md(p.get("rich_text"))
    if t == "code":
        code = _rt_to_md(p.get("rich_text"))
        return f"```\n{code}\n```"
    if t == "divider":
        return "---"
    if t == "table":
        rows = [k for k in _get_children(b["id"]) if k.get("type") == "table_row"]
        lines = []
        for ri, row in enumerate(rows):
            cells = [_rt_to_md(c) for c in row.get("table_row", {}).get("cells", [])]
            lines.append("| " + " | ".join(cells) + " |")
            if ri == 0 and p.get("has_column_header"):
                lines.append("|" + "|".join(["---"] * len(cells)) + "|")
        return "\n".join(lines)
    if t == "paragraph":
        return _rt_to_md(p.get("rich_text"))
    return ""  # unsupported/media → 생략


def _page_to_md(page_id):
    """페이지 본문 블록 → 마크다운 재구성(retrofit 재포맷 입력용)."""
    out = []
    for b in _get_children(page_id):
        md = _block_to_md(b)
        if md is not None and md != "":
            out.append(md)
    return "\n\n".join(out)


def cmd_list_bucket(bucket):
    """버킷 DB의 모든 행 → [{page_id, name, score, verdict}] JSON."""
    c = _cfg()
    if bucket not in c["months"]:
        sys.stderr.write(f"❌ 버킷 없음: {bucket}\n"); sys.exit(1)
    db_id = c["months"][bucket]["db_id"]
    rows, cur = [], None
    while True:
        body = {"page_size": 100}
        if cur:
            body["start_cursor"] = cur
        d = _api("POST", f"/databases/{db_id}/query", body)
        rows += d.get("results", [])
        if not d.get("has_more"):
            break
        cur = d.get("next_cursor"); time.sleep(0.3)
    out = []
    for pg in rows:
        P = pg.get("properties", {})
        name = "".join(x.get("plain_text", "") for x in P.get("종목명", {}).get("title", []))
        out.append({"page_id": pg["id"], "name": name,
                    "score": P.get("종합점수", {}).get("number"),
                    "verdict": (P.get("종합의견", {}).get("select") or {}).get("name")})
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_get_md(page_id):
    """페이지 본문을 마크다운으로 출력(stdout)."""
    sys.stdout.write(_page_to_md(page_id))


def cmd_replace_body(page_id, md_path):
    """페이지의 기존 본문 블록을 모두 삭제하고 md_path 내용으로 재작성."""
    md = (open(md_path, encoding="utf-8").read() if md_path != "-" else sys.stdin.read())
    # 1) 기존 자식 블록 삭제
    old = _get_children(page_id)
    for b in old:
        _api("DELETE", f"/blocks/{b['id']}")
        time.sleep(0.2)
    # 2) 새 블록 append(청크)
    blocks = _md_to_blocks_full(md)
    _append_chunked(page_id, blocks)
    print(f"✅ 본문 교체: {page_id} ({len(old)}블록 삭제 → {len(blocks)}블록)")


def cmd_add(report_path, bucket=None, month=None):
    r = json.load(open(report_path, encoding="utf-8")) if report_path != "-" else json.load(sys.stdin)
    # 라우팅 키: --bucket(사이클 등) 우선 → --month → report.date의 월
    if not bucket:
        bucket = month or (r.get("date") or "")[:7]
        if not bucket or len(bucket) != 7:
            sys.stderr.write("❌ 버킷을 결정할 수 없음. --bucket(사이클) 또는 --month(YYYY-MM) 또는 report.date 필요.\n"); sys.exit(1)
    _, db_id = _ensure_bucket(bucket)

    props = {
        "종목명": {"title": _rt(r.get("name", "?"))},
        "종합점수": {"number": float(r["score"]) if r.get("score") is not None else None},
        "종합의견": {"select": {"name": r.get("verdict", "보류")}},
        "한줄의견": {"rich_text": _rt(r.get("one_liner", ""))},
        # 분석일 = 실제 분석완료·업로드 시각(KST, 시:분:초 포함)
        "분석일": {"date": {"start": datetime.datetime.now(_KST).replace(microsecond=0).isoformat()}},
    }
    body_md = _detail_header(r) + "\n" + (r.get("body_md", "") or "")
    blocks = _md_to_blocks_full(body_md)          # cap 없이 전체(뒷 섹션 절삭 방지)
    first, rest = blocks[:100], blocks[100:]       # 최초 100 + 나머지는 청크 append
    d = _api("POST", "/pages", {"parent": {"database_id": db_id},
                                "properties": props, "children": first})
    if d.get("id"):
        if rest:
            _append_chunked(d["id"], rest)
        print(f"✅ 등록: {r.get('name')} [{bucket}] → {d['id']} ({len(blocks)}블록)")
    else:
        sys.stderr.write("❌ 등록 실패.\n"); sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description="Notion 발행기 (사이클/월 버킷 페이지 + 최소 컬럼)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    fp = sub.add_parser("find-page"); fp.add_argument("query")
    sr = sub.add_parser("set-root"); sr.add_argument("page_id")
    # ensure-bucket(신규) + ensure-month(back-compat 별칭)
    eb = sub.add_parser("ensure-bucket"); eb.add_argument("bucket")
    em = sub.add_parser("ensure-month"); em.add_argument("month")
    ab = sub.add_parser("archive-bucket"); ab.add_argument("bucket")
    mb = sub.add_parser("migrate-bucket"); mb.add_argument("src"); mb.add_argument("dst")
    ad = sub.add_parser("add"); ad.add_argument("report")
    ad.add_argument("--bucket"); ad.add_argument("--month")
    lb = sub.add_parser("list-bucket"); lb.add_argument("bucket")
    gm = sub.add_parser("get-md"); gm.add_argument("page_id")
    rb = sub.add_parser("replace-body"); rb.add_argument("page_id"); rb.add_argument("md")
    a = ap.parse_args()
    {"find-page": lambda: cmd_find_page(a.query),
     "set-root": lambda: cmd_set_root(a.page_id),
     "ensure-bucket": lambda: cmd_ensure_bucket(a.bucket),
     "ensure-month": lambda: cmd_ensure_bucket(a.month),
     "archive-bucket": lambda: cmd_archive_bucket(a.bucket),
     "migrate-bucket": lambda: cmd_migrate_bucket(a.src, a.dst),
     "add": lambda: cmd_add(a.report, a.bucket, a.month),
     "list-bucket": lambda: cmd_list_bucket(a.bucket),
     "get-md": lambda: cmd_get_md(a.page_id),
     "replace-body": lambda: cmd_replace_body(a.page_id, a.md)}[a.cmd]()


if __name__ == "__main__":
    main()
