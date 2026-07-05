#!/usr/bin/env python3
"""DART(전자공시) OpenAPI 도구 — 한국 기업 공시·재무제표 원천 데이터.

AI Berkshire Skills 를 위한 DART 데이터 제공. A주의 巨潮资讯(cninfo) / 美股의 SEC EDGAR
에 해당하는 한국의 1차 공시 원천. 재무제표·기업개황·정기/수시공시 목록을 조회.

인증키(무료): https://opendart.fss.or.kr → 인증키 신청/관리 → 발급(즉시).
설정 방법(둘 중 하나):
    export DART_API_KEY=발급받은키            # 환경변수, 또는
    echo "발급받은키" > ~/.dart_api_key        # 파일

사용법(Skills 가 자동 호출):
    python3 tools/dart_data.py corpcode 005930         # 종목코드/회사명 → DART corp_code
    python3 tools/dart_data.py company 00126380        # 기업개황
    python3 tools/dart_data.py financials 00126380 2024        # 재무제표(연결, 사업보고서)
    python3 tools/dart_data.py disclosures 00126380 90         # 최근 90일 공시목록

reprt_code: 11011=사업보고서 11012=반기 11013=1분기 11014=3분기
fs_div: CFS=연결  OFS=개별
Python >= 3.8, 외부 의존성 없음.
"""

import argparse
import io
import json
import os
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timedelta
from urllib.parse import urlencode
from xml.etree import ElementTree as ET

_TIMEOUT = 30
_CACHE = os.path.expanduser("~/.cache/ai-berkshire")
_CORP_CACHE = os.path.join(_CACHE, "dart_corpcode.xml")
_CORP_TTL = 7 * 24 * 3600  # 7일

_REPRT = {"11011": "사업보고서", "11012": "반기보고서", "11013": "1분기", "11014": "3분기"}


def _api_key() -> str:
    key = os.environ.get("DART_API_KEY", "").strip()
    if not key:
        path = os.path.expanduser("~/.dart_api_key")
        if os.path.exists(path):
            with open(path) as f:
                key = f.read().strip()
    if not key:
        sys.stderr.write(
            "❌ DART 인증키가 없습니다.\n"
            "   1) https://opendart.fss.or.kr 에서 무료 발급(즉시)\n"
            "   2) export DART_API_KEY=키   또는   echo 키 > ~/.dart_api_key\n")
        sys.exit(2)
    return key


def _curl_bytes(url: str) -> bytes:
    r = subprocess.run(
        ["/usr/bin/curl", "-s", "--noproxy", "*", "-H", "User-Agent: Mozilla/5.0", url],
        capture_output=True, timeout=_TIMEOUT)
    if r.returncode != 0 or not r.stdout:
        raise ConnectionError(f"요청 실패: {url}")
    return r.stdout


def _get(endpoint: str, **params) -> dict:
    params["crtfc_key"] = _api_key()
    url = f"https://opendart.fss.or.kr/api/{endpoint}?{urlencode(params)}"
    raw = _curl_bytes(url).decode("utf-8", errors="replace")
    data = json.loads(raw)
    status = data.get("status")
    if status not in ("000", None):
        sys.stderr.write(f"⚠️ DART API status {status}: {data.get('message')}\n")
    return data


# ---------------------------------------------------------------------------
# 기업코드(CORPCODE) — 종목코드/회사명 → DART corp_code
# ---------------------------------------------------------------------------

def _load_corpcode() -> list:
    os.makedirs(_CACHE, exist_ok=True)
    if os.path.exists(_CORP_CACHE) and time.time() - os.path.getmtime(_CORP_CACHE) < _CORP_TTL:
        with open(_CORP_CACHE, "rb") as f:
            xml = f.read()
    else:
        key = _api_key()
        blob = _curl_bytes(f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={key}")
        # 정상 응답은 ZIP, 오류는 XML(status 010 등)
        if blob[:2] != b"PK":
            sys.stderr.write("❌ 기업코드 다운로드 실패: " + blob.decode("utf-8", "replace")[:200] + "\n")
            sys.exit(2)
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            xml = z.read(z.namelist()[0])
        with open(_CORP_CACHE, "wb") as f:
            f.write(xml)
    root = ET.fromstring(xml)
    out = []
    for e in root.iter("list"):
        out.append({
            "corp_code": (e.findtext("corp_code") or "").strip(),
            "corp_name": (e.findtext("corp_name") or "").strip(),
            "stock_code": (e.findtext("stock_code") or "").strip(),
        })
    return out


def cmd_corpcode(query: str):
    q = query.strip()
    corps = _load_corpcode()
    listed = [c for c in corps if c["stock_code"]]
    if q.isdigit() and len(q) <= 6:
        q6 = q.zfill(6)
        hits = [c for c in listed if c["stock_code"] == q6]
    else:
        hits = [c for c in listed if q in c["corp_name"]]
    print("=" * 60)
    print(f"DART 기업코드 검색: '{query}'  (상장사 {len(listed):,}개 중)")
    print("=" * 60)
    if not hits:
        print("  ❌ 일치하는 상장사 없음")
        return
    for c in hits[:15]:
        print(f"  corp_code={c['corp_code']}  종목={c['stock_code']}  {c['corp_name']}")


# ---------------------------------------------------------------------------
# 기업개황
# ---------------------------------------------------------------------------

def cmd_company(corp_code: str):
    d = _get("company.json", corp_code=corp_code)
    if d.get("status") != "000":
        return
    print("=" * 60)
    print(f"기업개황: {d.get('corp_name')} ({d.get('stock_code') or '비상장'})")
    print("=" * 60)
    for label, k in [("영문명", "corp_name_eng"), ("대표자", "ceo_nm"),
                     ("설립일", "est_dt"), ("상장일", "list_dt"),
                     ("업종코드", "induty_code"), ("결산월", "acc_mt"),
                     ("홈페이지", "hm_url"), ("주소", "adres")]:
        v = d.get(k)
        if v:
            print(f"  {label:<8} {v}")


# ---------------------------------------------------------------------------
# 재무제표(단일회사 전체 계정)
# ---------------------------------------------------------------------------

def _fmt_won(s):
    try:
        v = int(str(s).replace(",", ""))
    except (ValueError, TypeError):
        return str(s)
    if abs(v) >= 1_0000_0000_0000:
        return f"{v/1_0000_0000_0000:.2f}조원"
    if abs(v) >= 1_0000_0000:
        return f"{v/1_0000_0000:.1f}억원"
    return f"{v:,}"


def cmd_financials(corp_code: str, year: str, reprt: str = "11011", fs_div: str = "CFS"):
    d = _get("fnlttSinglAcntAll.json", corp_code=corp_code, bsns_year=year,
             reprt_code=reprt, fs_div=fs_div)
    if d.get("status") != "000":
        if fs_div == "CFS":
            sys.stderr.write("   → 연결(CFS) 없음, 개별(OFS) 재시도\n")
            return cmd_financials(corp_code, year, reprt, "OFS")
        return
    rows = d.get("list", [])
    div = "연결" if fs_div == "CFS" else "개별"
    print("=" * 60)
    print(f"재무제표 [{div}] {year}년 {_REPRT.get(reprt, reprt)}  corp={corp_code}")
    print("=" * 60)

    # 핵심 계정만 추림 (재무상태표 BS + 손익계산서 IS/CIS)
    want = {
        "매출액", "영업수익", "수익(매출액)", "영업이익", "영업이익(손실)",
        "당기순이익", "당기순이익(손실)", "법인세비용차감전순이익",
        "자산총계", "부채총계", "자본총계", "이익잉여금",
    }
    seen = set()
    for r in rows:
        nm = (r.get("account_nm") or "").strip()
        if nm not in want or nm in seen:
            continue
        seen.add(nm)
        cur = r.get("thstrm_amount", "")
        prv = r.get("frmtrm_amount", "")
        sj = r.get("sj_nm", "")
        print(f"  [{sj}] {nm:<16} 당기 {_fmt_won(cur):>12}   전기 {_fmt_won(prv):>12}")
    if not seen:
        print("  ⚠️ 핵심 계정 매칭 실패 — 전체 계정 수:", len(rows))


# ---------------------------------------------------------------------------
# 공시목록 (정기 + 수시)
# ---------------------------------------------------------------------------

_PBLNTF = {"A": "정기공시", "B": "주요사항", "C": "발행공시", "D": "지분공시",
           "E": "기타", "F": "외부감사", "G": "펀드", "H": "자산유동화", "I": "거래소", "J": "공정위"}


def cmd_disclosures(corp_code: str, days: int = 90):
    end = datetime.now()
    bgn = end - timedelta(days=int(days))
    d = _get("list.json", corp_code=corp_code, bgn_de=bgn.strftime("%Y%m%d"),
             end_de=end.strftime("%Y%m%d"), page_count="30", page_no="1")
    if d.get("status") != "000":
        return
    items = d.get("list", [])
    print("=" * 60)
    print(f"최근 {days}일 공시목록: corp={corp_code}  (총 {d.get('total_count','?')}건)")
    print("=" * 60)
    for it in items[:30]:
        date = it.get("rcept_dt", "")
        nm = it.get("report_nm", "")
        flr = it.get("flr_nm", "")
        rcp = it.get("rcept_no", "")
        link = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcp}"
        print(f"  {date}  {nm}  ({flr})")
        print(f"           {link}")


# ---------------------------------------------------------------------------
# CLI 진입점
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="DART OpenAPI 도구 — 한국 공시·재무제표 원천",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("corpcode", help="종목코드/회사명 → DART corp_code")
    p.add_argument("query")
    p = sub.add_parser("company", help="기업개황")
    p.add_argument("corp_code")
    p = sub.add_parser("financials", help="재무제표(단일회사 전체계정)")
    p.add_argument("corp_code")
    p.add_argument("year")
    p.add_argument("reprt", nargs="?", default="11011", help="11011사업/11012반기/11013 1Q/11014 3Q")
    p.add_argument("fs_div", nargs="?", default="CFS", help="CFS연결/OFS개별")
    p = sub.add_parser("disclosures", help="공시목록")
    p.add_argument("corp_code")
    p.add_argument("days", nargs="?", default="90")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "corpcode":
        cmd_corpcode(args.query)
    elif args.command == "company":
        cmd_company(args.corp_code)
    elif args.command == "financials":
        cmd_financials(args.corp_code, args.year, args.reprt, args.fs_div)
    elif args.command == "disclosures":
        cmd_disclosures(args.corp_code, args.days)


if __name__ == "__main__":
    main()
