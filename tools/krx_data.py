#!/usr/bin/env python3
"""한국 주식 데이터 도구 — 네이버 금융(KOSPI/KOSDAQ), 외부 의존성 없음(표준 라이브러리 + curl).

AI Berkshire Skills 를 위한 코스피/코스닥 실시간 시세·밸류에이션·재무 데이터 제공.
설계 원칙: 기존 ashare_data.py(A주)와 동일한 CLI 구조를 미러링, 무키(無API-Key) 공개 엔드포인트 사용.

사용법(Skills 가 자동 호출):
    python3 tools/krx_data.py quote 005930          # 실시간 시세
    python3 tools/krx_data.py valuation 005930       # 밸류에이션(PER/PBR/EPS/BPS/배당)
    python3 tools/krx_data.py financials 005930      # 연간 핵심 재무(매출/영업이익/순이익 등)
    python3 tools/krx_data.py search 삼성전자          # 종목코드 검색

Python >= 3.8, 외부 의존성 없음.
"""

import argparse
import json
import subprocess
import sys
from decimal import Decimal
from urllib.parse import urlencode, quote

_TIMEOUT = 15
_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15"


def _curl(url, referer="https://m.stock.naver.com/"):
    """curl --noproxy 직연결로 시스템 프록시 우회."""
    result = subprocess.run(
        ["/usr/bin/curl", "-s", "--noproxy", "*",
         "-H", f"User-Agent: {_UA}",
         "-H", f"Referer: {referer}",
         url],
        capture_output=True, timeout=_TIMEOUT,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise ConnectionError(f"요청 실패: {url}")
    return result.stdout.decode("utf-8", errors="replace")


def _curl_json(url, referer="https://m.stock.naver.com/"):
    return json.loads(_curl(url, referer))


def _clean_code(code: str) -> str:
    """005930.KS / A005930 / 005930 등을 6자리 코드로 정규화."""
    code = code.strip().upper()
    for suf in (".KS", ".KQ", ".KOSPI", ".KOSDAQ"):
        code = code.replace(suf, "")
    if code.startswith("A") and code[1:].isdigit():
        code = code[1:]
    return code.zfill(6) if code.isdigit() else code


# ---------------------------------------------------------------------------
# 명령 구현
# ---------------------------------------------------------------------------

def cmd_quote(code: str):
    """실시간 시세 스냅샷 (네이버 실시간 폴링 API)."""
    c = _clean_code(code)
    url = f"https://polling.finance.naver.com/api/realtime/domestic/stock/{c}"
    try:
        data = _curl_json(url)
        d = data["datas"][0]
    except Exception:
        print(f"❌ 종목 {code} 을(를) 찾지 못했습니다 (search 로 코드 확인)")
        return

    ex = d.get("stockExchangeType", {})
    print("=" * 60)
    print(f"실시간 시세: {d.get('stockName')} ({d.get('itemCode')}) [{ex.get('name','')}]")
    print("=" * 60)
    print(f"  현재가:     {d.get('closePrice')} 원")
    print(f"  등락률:     {d.get('fluctuationsRatio')}%")
    print(f"  전일대비:   {d.get('compareToPreviousClosePrice')}")
    print(f"  시가:       {d.get('openPrice')}")
    print(f"  고가:       {d.get('highPrice')}")
    print(f"  저가:       {d.get('lowPrice')}")
    print(f"  거래량:     {d.get('accumulatedTradingVolume')} 주")
    print(f"  거래대금:   {d.get('accumulatedTradingValue')}")
    print(f"  상태:       {d.get('marketStatus')}  ({d.get('localTradedAt','')[:19]})")


def cmd_valuation(code: str):
    """밸류에이션 지표 (네이버 integration API: 시총/PER/PBR/EPS/BPS/배당)."""
    c = _clean_code(code)
    url = f"https://m.stock.naver.com/api/stock/{c}/integration"
    ref = f"https://m.stock.naver.com/domestic/stock/{c}/total"
    try:
        d = _curl_json(url, ref)
    except Exception:
        print(f"❌ 종목 {code} 밸류에이션 조회 실패")
        return

    infos = {i.get("code"): i for i in d.get("totalInfos", [])}

    def g(key):
        i = infos.get(key)
        return (i.get("value"), i.get("valueDesc", "")) if i else ("-", "")

    print("=" * 60)
    print(f"밸류에이션: {d.get('stockName')} ({d.get('itemCode')})")
    print("=" * 60)
    for label, key in [
        ("시가총액", "marketValue"), ("PER", "per"), ("추정PER", "cnsPer"),
        ("PBR", "pbr"), ("EPS", "eps"), ("추정EPS", "cnsEps"), ("BPS", "bps"),
        ("배당수익률", "dividendYieldRatio"), ("주당배당금", "dividend"),
        ("외인소진율", "foreignRate"),
        ("52주 최고", "highPriceOf52Weeks"), ("52주 최저", "lowPriceOf52Weeks"),
    ]:
        val, desc = g(key)
        desc = f"  ({desc})" if desc else ""
        print(f"  {label:<10} {val}{desc}")

    # 업종/컨센서스 보조정보 (스키마가 dict/list 로 바뀔 수 있어 방어적으로 처리)
    ind = d.get("industryCompareInfo")
    if isinstance(ind, dict) and ind.get("industryName"):
        print(f"\n  업종:       {ind.get('industryName')}")
    cns = d.get("consensusInfo")
    if isinstance(cns, dict) and cns.get("consensusOpinion"):
        print(f"  투자의견:   {cns.get('consensusOpinion')}  목표가 {cns.get('priceTarget','-')}")


def cmd_financials(code: str):
    """연간 핵심 재무 데이터 (네이버 finance/annual)."""
    c = _clean_code(code)
    url = f"https://m.stock.naver.com/api/stock/{c}/finance/annual"
    ref = f"https://m.stock.naver.com/domestic/stock/{c}/total"
    try:
        d = _curl_json(url, ref)
        fin = d["financeInfo"]
    except Exception:
        print(f"⚠️ {code} 재무 데이터 조회 실패 — WebSearch/DART 로 보완 권장")
        return

    cols = fin.get("trTitleList", [])
    keys = [c["key"] for c in cols]
    titles = {c["key"]: c["title"] + ("(E)" if c.get("isConsensus") == "Y" else "") for c in cols}

    print("=" * 60)
    print(f"연간 핵심 재무(단위: 억원): {d.get('itemCode')}")
    print("=" * 60)
    header = "  " + "항목".ljust(14) + "".join(titles[k].rjust(13) for k in keys)
    print(header)
    print("  " + "-" * (14 + 13 * len(keys)))

    want = ["매출액", "영업이익", "당기순이익", "영업이익률", "순이익률",
            "ROE(지배주주)", "부채비율", "당좌비율", "유보율", "EPS(원)", "BPS(원)",
            "주당배당금(원)", "시가배당률(%)"]
    rows = {r["title"]: r for r in fin.get("rowList", [])}
    for item in want:
        r = rows.get(item)
        if not r:
            continue
        line = "  " + item.ljust(14)
        for k in keys:
            cell = r["columns"].get(k, {})
            v = cell.get("value") if isinstance(cell, dict) else None
            line += (str(v) if v not in (None, "") else "-").rjust(13)
        print(line)


def cmd_search(keyword: str):
    """종목명으로 코드 검색 (네이버 자동완성)."""
    url = f"https://ac.stock.naver.com/ac?q={quote(keyword)}&target=stock&st=111"
    try:
        d = _curl_json(url, "https://m.stock.naver.com/")
        items = d.get("items", [])
    except Exception:
        print(f"❌ '{keyword}' 검색 실패")
        return

    kr = [i for i in items if i.get("nationCode") == "KOR"]
    if not kr:
        print(f"❌ '{keyword}' 에 해당하는 국내 종목 없음")
        return
    print("=" * 60)
    print(f"검색 결과: '{keyword}'")
    print("=" * 60)
    for i in kr[:10]:
        print(f"  {i.get('code')}  {i.get('name')}  [{i.get('typeName')}]")


# ---------------------------------------------------------------------------
# CLI 진입점
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="한국 주식 데이터 도구 — 네이버 금융(KOSPI/KOSDAQ)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")
    for name, help_ in [("quote", "실시간 시세"), ("valuation", "밸류에이션"),
                        ("financials", "연간 핵심 재무")]:
        p = sub.add_parser(name, help=help_)
        p.add_argument("code", help="종목코드, 예: 005930")
    p_s = sub.add_parser("search", help="종목코드 검색")
    p_s.add_argument("keyword", help="회사명 또는 키워드")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    {
        "quote": lambda: cmd_quote(args.code),
        "valuation": lambda: cmd_valuation(args.code),
        "financials": lambda: cmd_financials(args.code),
        "search": lambda: cmd_search(args.keyword),
    }[args.command]()


if __name__ == "__main__":
    main()
