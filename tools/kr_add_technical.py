#!/usr/bin/env python3
"""기존 Notion 종목 페이지에 '기술적 분석(월봉)' 섹션 + 월봉 차트 이미지를 삽입.

kr_technical.py 로 월봉 분석표를 만들고, 차트 PNG를 Notion File Upload API로 업로드해
'3-시나리오 목표가' 섹션 뒤(‘실패 시나리오’ 앞)에 표+이미지+주석을 함께 넣는다.
신규 리포트는 스킬이 인라인으로 처리하므로, 이 스크립트는 '기존 페이지 소급용'이다.

사용법:
    python3 tools/kr_add_technical.py --page <page_id> --code 271560 --name 오리온
    python3 tools/kr_add_technical.py --page <page_id> --code 271560 --name 오리온 --dry-run
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

import notion_publish as npub
import notion_fix_tables as fx
import kr_technical as tech

_VER = "2022-06-28"


def _api(method, path, body=None):
    return fx._api(method, path, body)


def upload_png(path):
    """PNG를 Notion File Upload API로 업로드 → file_upload id 반환(실패 시 None)."""
    tok = npub._token()
    d = _api("POST", "/file_uploads", {"filename": os.path.basename(path), "content_type": "image/png"})
    up_id, up_url = d.get("id"), d.get("upload_url")
    if not up_id or not up_url:
        sys.stderr.write(f"❌ file_upload 생성 실패: {d}\n"); return None
    # multipart 전송 (curl -F)
    r = subprocess.run(
        ["/usr/bin/curl", "-s", "--noproxy", "*", "-X", "POST", up_url,
         "-H", f"Authorization: Bearer {tok}", "-H", f"Notion-Version: {_VER}",
         "-F", f"file=@{path};type=image/png"],
        capture_output=True, timeout=60)
    resp = json.loads(r.stdout.decode("utf-8", "replace") or "{}")
    if resp.get("status") != "uploaded":
        sys.stderr.write(f"❌ 파일 전송 실패: {resp}\n"); return None
    return up_id


def _image_block(file_upload_id, caption=""):
    img = {"type": "file_upload", "file_upload": {"id": file_upload_id}}
    if caption:
        img["caption"] = [{"type": "text", "text": {"content": caption[:2000]}}]
    return {"object": "block", "type": "image", "image": img}


def build_blocks(code, name, file_upload_id=None):
    a = tech.analyze(code, "month")
    md = tech.to_md(a)
    blocks = npub._md_to_blocks_full(md)          # heading + table + note(paragraph/quote)
    if file_upload_id:
        # 표 블록 바로 뒤에 이미지 삽입
        ins = len(blocks)
        for i, b in enumerate(blocks):
            if b.get("type") == "table":
                ins = i + 1; break
        cap = f"{name}({code}) 월봉 2017~현재 · MA12(파랑)/MA36(주황) · 박스 상/하단"
        blocks.insert(ins, _image_block(file_upload_id, cap))
    return blocks, a


def find_anchor(page_id):
    """'증권가 12개월 평균' 문단(목표가 섹션 끝) 또는 '실패 시나리오' 앞 블록 id."""
    blocks = fx.block_children(page_id)
    ids = [b["id"] for b in blocks]
    # 우선순위1: '증권가 ... 평균 목표가' 포함 문단
    for i, b in enumerate(blocks):
        t = b.get("type")
        txt = fx._plain(b.get(t, {}).get("rich_text", []))
        if "증권가" in txt and "목표가" in txt:
            return b["id"]
    # 우선순위2: '실패 시나리오' heading 바로 앞 블록
    for i, b in enumerate(blocks):
        if b.get("type", "").startswith("heading") and "실패 시나리오" in fx._plain(b[b["type"]]["rich_text"]):
            return ids[i - 1] if i > 0 else None
    return ids[-1] if ids else None   # 없으면 맨 끝에 append


def already_has(page_id):
    for b in fx.block_children(page_id):
        if b.get("type", "").startswith("heading") and "기술적 분석" in fx._plain(b[b["type"]]["rich_text"]):
            return True
    return False


def add(page_id, code, name, dry=False):
    if already_has(page_id):
        print(f"· {name}: 이미 '기술적 분석' 섹션 존재 — skip")
        return "skip"
    # 차트 생성
    tmp = os.path.join(tempfile.gettempdir(), f"{code}_month.png")
    tech.make_chart(code, "month", tmp, name)
    print(f"· 차트 생성: {tmp}")
    fid = None if dry else upload_png(tmp)
    if not dry and not fid:
        print("  ⚠️ 이미지 업로드 실패 — 표/텍스트만 삽입")
    blocks, a = build_blocks(code, name, fid)
    anchor = find_anchor(page_id)
    n_img = sum(1 for b in blocks if b.get("type") == "image")
    n_tbl = sum(1 for b in blocks if b.get("type") == "table")
    print(f"· 삽입 블록 {len(blocks)}개 (표 {n_tbl}, 이미지 {n_img}) · anchor={str(anchor)[:8]} · 국면={a['regime']}·{a['location']}"
          + (" [dry-run]" if dry else ""))
    if dry:
        return "dry"
    body = {"children": blocks}
    if anchor:
        body["after"] = anchor
    d = _api("PATCH", f"/blocks/{page_id}/children", body)
    if d.get("object") == "error":
        sys.stderr.write(f"❌ 삽입 실패: {d.get('message')}\n"); return "error"
    print(f"  ✅ {name} 기술적 분석 섹션 추가 완료")
    return "ok"


def main():
    ap = argparse.ArgumentParser(description="기존 Notion 페이지에 기술적 분석 섹션+차트 추가")
    ap.add_argument("--page", required=True)
    ap.add_argument("--code", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    add(a.page, a.code, a.name, dry=a.dry_run)


if __name__ == "__main__":
    main()
