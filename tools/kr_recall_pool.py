#!/usr/bin/env python3
"""코스피/코스닥 召回池(recall pool) 빌더 — 시총 상위 프록시로 KOSPI200/KOSDAQ150 구성.

AI Berkshire 한국 확장의 깔때기 ①단계. 네이버 금융 시총순 API(무키)로 보통주만
필터링해 우량 후보 풀을 만든다. 去劣 스크리너(kr_quality_screen.py)의 입력.

정확한 지수 구성종목은 KRX 독점이므로, 재현성·자동화가 쉬운 '시총 상위 N 프록시'를 사용한다
(KOSPI200 ≈ 코스피 시총 상위 200 보통주, KOSDAQ150 ≈ 코스닥 시총 상위 150 보통주).

사용법:
    python3 tools/kr_recall_pool.py build --kospi 200 --kosdaq 150   # data/kr_recall_pool.json 생성
    python3 tools/kr_recall_pool.py show                             # 저장된 풀 요약

Python >= 3.8, 외부 의존성 없음(표준 라이브러리 + curl).
"""

import argparse
import json
import os
import subprocess
import sys

_TIMEOUT = 20
_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15"
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OUT = os.path.join(_ROOT, "data", "kr_recall_pool.json")

# 보통주가 아닌 것(스팩/리츠/우선주/ETF성)을 이름으로 배제
_NAME_EXCLUDE = ("스팩", "리츠", "우B", "우C", "1우", "2우")


def _curl_json(url):
    r = subprocess.run(
        ["/usr/bin/curl", "-s", "--noproxy", "*",
         "-H", f"User-Agent: {_UA}",
         "-H", "Referer: https://m.stock.naver.com/", url],
        capture_output=True, timeout=_TIMEOUT,
    )
    if r.returncode != 0 or not r.stdout.strip():
        raise ConnectionError(f"요청 실패: {url}")
    return json.loads(r.stdout.decode("utf-8", errors="replace"))


def _is_common(stock):
    """보통주만 True. 우선주(코드 끝!='0')·ETF·스팩·리츠 배제."""
    code = stock.get("itemCode", "")
    name = stock.get("stockName", "")
    if stock.get("stockEndType") != "stock":
        return False
    if not (len(code) == 6 and code.isdigit() and code.endswith("0")):
        return False  # 우선주는 보통 끝자리 5/7 → 배제
    if name.endswith("우") or any(x in name for x in _NAME_EXCLUDE):
        return False
    return True


def _fetch_market(market: str, top_n: int):
    """네이버 시총순 API로 보통주 top_n 수집."""
    out, page, page_size = [], 1, 100
    while len(out) < top_n and page <= 40:
        url = (f"https://m.stock.naver.com/api/stocks/marketValue/{market}"
               f"?page={page}&pageSize={page_size}")
        data = _curl_json(url)
        stocks = data.get("stocks", [])
        if not stocks:
            break
        for s in stocks:
            if _is_common(s):
                mv = s.get("marketValue", "0").replace(",", "")
                out.append({
                    "code": s["itemCode"],
                    "name": s["stockName"],
                    "market": market,
                    "market_cap_eok": int(mv) if mv.isdigit() else None,
                })
                if len(out) >= top_n:
                    break
        page += 1
    return out[:top_n]


def cmd_build(kospi_n: int, kosdaq_n: int):
    kospi = _fetch_market("KOSPI", kospi_n)
    kosdaq = _fetch_market("KOSDAQ", kosdaq_n)
    pool = kospi + kosdaq
    os.makedirs(os.path.dirname(_OUT), exist_ok=True)
    with open(_OUT, "w", encoding="utf-8") as f:
        json.dump({"kospi_n": len(kospi), "kosdaq_n": len(kosdaq),
                   "total": len(pool), "pool": pool},
                  f, ensure_ascii=False, indent=2)
    print(f"✅ 召回池 생성: KOSPI {len(kospi)} + KOSDAQ {len(kosdaq)} = {len(pool)}종목")
    print(f"   저장: {_OUT}")
    print("   KOSPI 상위5:", ", ".join(f"{x['name']}({x['code']})" for x in kospi[:5]))
    print("   KOSDAQ 상위5:", ", ".join(f"{x['name']}({x['code']})" for x in kosdaq[:5]))


def cmd_show():
    if not os.path.exists(_OUT):
        print("召回池이 없습니다. 먼저 build 하세요.", file=sys.stderr)
        sys.exit(1)
    d = json.load(open(_OUT, encoding="utf-8"))
    print(f"召回池: KOSPI {d['kospi_n']} + KOSDAQ {d['kosdaq_n']} = {d['total']}종목")
    print("샘플:", ", ".join(f"{x['name']}({x['code']})" for x in d["pool"][:10]))


def main():
    ap = argparse.ArgumentParser(description="코스피/코스닥 召回池 빌더")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="召回池 생성")
    b.add_argument("--kospi", type=int, default=200)
    b.add_argument("--kosdaq", type=int, default=150)
    sub.add_parser("show", help="저장된 召回池 요약")
    args = ap.parse_args()
    if args.cmd == "build":
        cmd_build(args.kospi, args.kosdaq)
    elif args.cmd == "show":
        cmd_show()


if __name__ == "__main__":
    main()
