#!/usr/bin/env python3
r"""기존 Notion 종목 페이지의 '이스케이프-파이프 문단'을 실제 Notion 표로 변환(retrofit).

배경: 과거 notion_publish._md_to_blocks 는 마크다운 표 줄(| a | b |)을 그냥 문단으로
넣어 Notion이 파이프를 \| 로 이스케이프 → 표로 렌더링되지 않았다. 이 스크립트는 DB 내
각 종목 페이지의 최상위 블록을 읽어 마크다운으로 복원한 뒤, 수정된 변환기(_md_to_blocks,
표 지원)로 재생성해 페이지 끝에 append 하고 옛 블록을 삭제한다.

안전성:
- 무손실: 새 블록을 먼저 append 한 뒤 옛 블록을 삭제(중간 실패해도 중복만 생기고 소실 없음).
- 멱등: 이미 table 블록이면 표 마크다운으로 복원 후 재생성하므로 재실행해도 결과 동일.
- 표가 없거나 변환 결과가 원본과 동일한 페이지는 SKIP.

사용법:
    python3 tools/notion_fix_tables.py list                 # DB 종목 페이지 목록
    python3 tools/notion_fix_tables.py fix --page <id>      # 단일 페이지 변환
    python3 tools/notion_fix_tables.py fix --page <id> --dry-run
    python3 tools/notion_fix_tables.py fix-all [--dry-run]  # DB 전체 변환
"""

import argparse
import json
import os
import subprocess
import sys
import time

import notion_publish as npub  # 같은 tools/ 디렉터리

_VER = "2022-06-28"
_TIMEOUT = 30
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 2026-06 종목 리서치 DB
_DB_ID = "39401fb8-9743-81e0-ac80-c998b6e520d1"


def _api(method, path, body=None, retries=6):
    """Notion REST 호출. 429/5xx/네트워크 오류는 지수 백오프로 재시도.
    레이트리밋 미처리 시 DELETE 실패 → 옛 블록 잔존(중복) 사고가 나므로 재시도 필수."""
    args = ["/usr/bin/curl", "-s", "--noproxy", "*", "-X", method,
            f"https://api.notion.com/v1{path}",
            "-H", f"Authorization: Bearer {npub._token()}",
            "-H", f"Notion-Version: {_VER}", "-H", "Content-Type: application/json"]
    if body is not None:
        args += ["-d", json.dumps(body, ensure_ascii=False)]
    backoff = 1.0
    last = {}
    for attempt in range(retries):
        try:
            r = subprocess.run(args, capture_output=True, timeout=_TIMEOUT)
            d = json.loads(r.stdout.decode("utf-8", errors="replace") or "{}")
        except Exception as e:
            d = {"object": "error", "status": 0, "message": f"curl/parse 실패: {e}"}
        last = d
        st = d.get("status")
        if d.get("object") == "error" and (st in (429, 409, 500, 502, 503, 0)):
            time.sleep(backoff)
            backoff = min(backoff * 2, 20)
            continue
        if d.get("object") == "error":
            sys.stderr.write(f"⚠️ Notion {st}: {d.get('message')}\n")
        return d
    sys.stderr.write(f"⚠️ Notion 재시도 초과 {last.get('status')}: {last.get('message')}\n")
    return last


# ── 조회 ────────────────────────────────────────────────
def db_pages(db_id=_DB_ID):
    """DB의 모든 종목 페이지 (id, name) 반환."""
    out, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        d = _api("POST", f"/databases/{db_id}/query", body)
        for r in d.get("results", []):
            out.append((r["id"], npub._title_of(r)))
        if d.get("has_more"):
            cursor = d.get("next_cursor")
        else:
            break
    return out


def block_children(block_id):
    """블록의 최상위 자식 전체(페이지네이션)."""
    out, cursor = [], None
    while True:
        q = f"/blocks/{block_id}/children?page_size=100"
        if cursor:
            q += f"&start_cursor={cursor}"
        d = _api("GET", q)
        out.extend(d.get("results", []))
        if d.get("has_more"):
            cursor = d.get("next_cursor")
        else:
            break
    return out


# ── 블록 → 마크다운 복원 ──────────────────────────────────
def _plain(rich):
    return "".join(x.get("plain_text", x.get("text", {}).get("content", "")) for x in (rich or []))


def _cell_text(cell):
    # cell = list[rich_text]
    return "".join(x.get("plain_text", x.get("text", {}).get("content", "")) for x in (cell or []))


def blocks_to_md(blocks):
    """최상위 블록 리스트를 마크다운 문자열로 복원."""
    lines = []
    for b in blocks:
        t = b.get("type")
        if t == "heading_1":
            lines.append("# " + _plain(b[t]["rich_text"]))
        elif t == "heading_2":
            lines.append("## " + _plain(b[t]["rich_text"]))
        elif t == "heading_3":
            lines.append("### " + _plain(b[t]["rich_text"]))
        elif t == "bulleted_list_item":
            lines.append("- " + _plain(b[t]["rich_text"]))
        elif t == "numbered_list_item":
            lines.append("- " + _plain(b[t]["rich_text"]))
        elif t == "table":
            # 이미 표 → 마크다운 표로 복원(멱등)
            rows = b.get("_rows")  # fix 경로에서 미리 채워 넣음
            if rows is None:
                rows = []
            if rows:
                width = max(len(r) for r in rows)
                header = rows[0] + [""] * (width - len(rows[0]))
                lines.append("| " + " | ".join(header) + " |")
                lines.append("|" + "|".join(["---"] * width) + "|")
                for r in rows[1:]:
                    r = r + [""] * (width - len(r))
                    lines.append("| " + " | ".join(r) + " |")
        elif t == "paragraph":
            lines.append(_plain(b[t]["rich_text"]))
        else:
            # 알 수 없는 타입은 텍스트만 최대한 보존
            rich = b.get(t, {}).get("rich_text")
            if rich:
                lines.append(_plain(rich))
        # 주의: 블록 간 빈 줄을 넣지 않는다. 빈 줄을 넣으면 연속된 표 행이
        # 분리돼 _md_to_blocks 가 표로 인식하지 못한다(_md_to_blocks는 빈 줄을 무시).
    return "\n".join(lines)


def _fetch_table_rows(table_block_id):
    """table 블록의 table_row 자식 → list[list[str]]."""
    rows = []
    for r in block_children(table_block_id):
        if r.get("type") == "table_row":
            rows.append([_cell_text(c) for c in r["table_row"]["cells"]])
    return rows


# ── 변환 ────────────────────────────────────────────────
def _has_pseudo_table(blocks):
    """이스케이프-파이프 문단(표 행)이 있는지."""
    for b in blocks:
        if b.get("type") == "paragraph":
            s = _plain(b["paragraph"]["rich_text"]).strip()
            if s.startswith("|") and s.count("|") >= 2:
                return True
    return False


def _append_chunked(page_id, new_blocks, chunk=50, dry=False):
    for i in range(0, len(new_blocks), chunk):
        part = new_blocks[i:i + chunk]
        if dry:
            continue
        d = _api("PATCH", f"/blocks/{page_id}/children", {"children": part})
        if d.get("object") == "error":
            return False
        time.sleep(0.35)
    return True


def _delete_blocks(ids, dry=False):
    """블록 삭제(재시도 내장). 실패 id 리스트 반환(빈 리스트면 전부 성공)."""
    failed = []
    for bid in ids:
        if dry:
            continue
        d = _api("DELETE", f"/blocks/{bid}")
        if d.get("object") == "error":
            failed.append(bid)
        time.sleep(0.34)  # Notion ~3req/s 준수
    return failed


def fix_page(page_id, name="", dry=False):
    blocks = block_children(page_id)
    if not blocks:
        print(f"  · {name or page_id[:8]}: 블록 없음, skip")
        return "skip"
    has_pseudo = _has_pseudo_table(blocks)
    has_real_table = any(b.get("type") == "table" for b in blocks)
    if not has_pseudo and not has_real_table:
        print(f"  · {name or page_id[:8]}: 표 없음, skip")
        return "skip"
    if not has_pseudo and has_real_table:
        print(f"  · {name or page_id[:8]}: 이미 실제 표만 존재, skip")
        return "skip"
    if has_pseudo and has_real_table:
        # 이전 실행이 중간에 끊긴 DUP 상태 → 재구성하면 표가 두 배가 됨. clean-dup로 복구.
        print(f"  · {name or page_id[:8]}: DUP(중복) 상태 — clean-dup 로 복구 필요, skip")
        return "dup"

    # 이미 존재하는 table 블록의 행을 미리 채워 넣어 복원에 사용
    for b in blocks:
        if b.get("type") == "table":
            b["_rows"] = _fetch_table_rows(b["id"])

    md = blocks_to_md(blocks)
    new_blocks = npub._md_to_blocks_full(md)
    n_tables = sum(1 for b in new_blocks if b.get("type") == "table")
    print(f"  · {name or page_id[:8]}: 옛 {len(blocks)}블록 → 새 {len(new_blocks)}블록 (표 {n_tables}개)"
          + (" [dry-run]" if dry else ""))
    if dry:
        for b in new_blocks:
            if b.get("type") == "table":
                w = b["table"]["table_width"]; rn = len(b["table"]["children"])
                head = [c[0]["text"]["content"] for c in b["table"]["children"][0]["table_row"]["cells"]]
                print(f"      TABLE w={w} rows={rn} header={head}")
        return "dry"

    old_ids = [b["id"] for b in blocks]
    if not _append_chunked(page_id, new_blocks, dry=False):
        sys.stderr.write(f"  ❌ {name}: append 실패 — 옛 블록 보존(중복 상태), 수동 확인 필요\n")
        return "error"
    failed = _delete_blocks(old_ids, dry=False)
    if failed:
        # 남은 옛 블록 재삭제 1회
        failed = _delete_blocks(failed, dry=False)
    if failed:
        sys.stderr.write(f"  ⚠️ {name}: 옛 블록 {len(failed)}개 삭제 실패 — clean-dup 필요\n")
        return "dup"
    print(f"    ✅ {name or page_id[:8]} 변환 완료")
    return "ok"


def clean_dup(page_id, name="", dry=False):
    """중복(DUP) 페이지 복구: 마지막 '요약' heading_2(= 새 재구성 시작) 앞의
    옛 블록을 모두 삭제. 완전한 새 본문(표 포함)만 남긴다."""
    blocks = block_children(page_id)
    # '요약' heading_2 위치들
    idxs = [i for i, b in enumerate(blocks)
            if b.get("type") == "heading_2"
            and _plain(b["heading_2"]["rich_text"]).strip() == "요약"]
    if len(idxs) == 0:
        print(f"  · {name or page_id[:8]}: '요약' 헤더 없음 — 수동 확인 필요, skip")
        return "skip"
    start = idxs[-1]                       # 새 재구성 시작
    if start == 0:
        print(f"  · {name or page_id[:8]}: 이미 정상(선행 잔존 없음), skip")
        return "skip"
    leftover = [b["id"] for b in blocks[:start]]
    # 새 본문 쪽에 표가 실제로 있는지 확인(안전장치)
    new_has_table = any(b.get("type") == "table" for b in blocks[start:])
    if not new_has_table:
        print(f"  · {name or page_id[:8]}: 새 본문에 표 없음 — 위험, skip(수동)")
        return "skip"
    print(f"  · {name or page_id[:8]}: 선행 잔존 {len(leftover)}블록 삭제 → 새 본문 {len(blocks)-start}블록 유지"
          + (" [dry-run]" if dry else ""))
    if dry:
        return "dry"
    failed = _delete_blocks(leftover, dry=False)
    if failed:
        failed = _delete_blocks(failed, dry=False)
    if failed:
        sys.stderr.write(f"  ⚠️ {name}: {len(failed)}개 삭제 실패\n")
        return "error"
    print(f"    ✅ {name or page_id[:8]} 중복 복구 완료")
    return "ok"


def cmd_list():
    pages = db_pages()
    print(f"총 {len(pages)}개 종목 페이지")
    for pid, name in pages:
        print(f"  {name}\t{pid}")


def cmd_fix(page_id, dry):
    fix_page(page_id, dry=dry)


def cmd_fix_all(dry):
    pages = db_pages()
    print(f"총 {len(pages)}개 종목 페이지 {'[dry-run]' if dry else ''}")
    tally = {}
    for pid, name in pages:
        r = fix_page(pid, name=name, dry=dry)
        tally[r] = tally.get(r, 0) + 1
    print("결과:", tally)


def main():
    ap = argparse.ArgumentParser(description="Notion 종목 페이지 표 retrofit")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    f = sub.add_parser("fix"); f.add_argument("--page", required=True); f.add_argument("--dry-run", action="store_true")
    fa = sub.add_parser("fix-all"); fa.add_argument("--dry-run", action="store_true")
    cd = sub.add_parser("clean-dup"); cd.add_argument("--page", required=True); cd.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.cmd == "list":
        cmd_list()
    elif a.cmd == "fix":
        cmd_fix(a.page, a.dry_run)
    elif a.cmd == "fix-all":
        cmd_fix_all(a.dry_run)
    elif a.cmd == "clean-dup":
        clean_dup(a.page, dry=a.dry_run)


if __name__ == "__main__":
    main()
