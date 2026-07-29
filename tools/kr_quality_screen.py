#!/usr/bin/env python3
"""코스피/코스닥 去劣(quality-screen) 1차 스크리너 — 네이버 3년 재무(무키).

AI Berkshire 한국 확장 깔때기 ②단계. 召回池(kr_recall_pool.py)의 350종목에
값싼 1차 去劣 지표를 적용해 대부분을 걸러낸다. 통과분은 2차 정밀 去劣(DART) 및
딥리서치(/investment-team)의 입력이 된다.

1차 지표(네이버 최근 3개 실적연도 평균, 추정(E) 컬럼 제외):
    ① ROE 평균        >= 8%      (자본효율)
    ② 순이익률 평균    >= 5%      (수익성)
    ③ 영업이익률 평균  >  0%      (영업적자 배제)
    ④ 부채비율(최근)   <= 200%    (재무건전성)
  하나라도 미달 → 탈락(사유 기록). 데이터 풍부도 A(3년)/B(2년)/C(1년) 태그.

주의: 원본 7지표(5년FCF·이자보상·매출총이익률·OCF/NI·주식희석)는 현금흐름표가 필요해
2차 정밀 去劣(DART, kr_quality_screen 통과분 대상)에서 검증한다. 이건 값싼 1차 컷이다.

사용법:
    python3 tools/kr_quality_screen.py run            # 召回池 전체 스크리닝 → data/kr_screen_result.json
    python3 tools/kr_quality_screen.py run --codes 005930,247540   # 특정 종목만(테스트)

Python >= 3.8, 외부 의존성 없음.
"""

import argparse
import json
import os
import subprocess
import sys
from decimal import Decimal, InvalidOperation

_TIMEOUT = 20
_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15"
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_POOL = os.path.join(_ROOT, "data", "kr_recall_pool.json")
_OUT = os.path.join(_ROOT, "data", "kr_screen_result.json")

# 去劣 1차 임계값
_MIN_ROE = Decimal("8")
_MIN_NET_MARGIN = Decimal("5")
_MIN_OP_MARGIN = Decimal("0")
_MAX_DEBT = Decimal("200")


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


def _dec(x):
    if isinstance(x, dict):  # 네이버 컬럼은 {'value': '...', 'cx': None} 형태
        x = x.get("value")
    if x in (None, "", "-"):
        return None
    try:
        return Decimal(str(x).replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None


def _actual_periods(columns):
    """추정(E) 컬럼(최신 기간) 제외한 실적 기간 키를 오름차순 반환."""
    periods = sorted(columns.keys())
    return periods[:-1] if len(periods) >= 2 else periods  # 최신=추정 제외


def _fetch_metrics(code):
    """네이버 연간 재무에서 ROE/순이익률/영업이익률/부채비율의 실적연도 값 추출."""
    d = _curl_json(f"https://m.stock.naver.com/api/stock/{code}/finance/annual")
    rows = {r["title"]: r.get("columns", {}) for r in d["financeInfo"]["rowList"]}
    want = ("ROE", "순이익률", "영업이익률", "부채비율")
    if not all(k in rows for k in want):
        return None
    periods = _actual_periods(rows["ROE"])
    out = {}
    for key in want:
        vals = []
        for p in periods:
            v = _dec(rows[key].get(p, "-"))
            if v is not None:
                vals.append(v)
        out[key] = vals
    out["_years"] = len(out["ROE"])
    return out


def _avg(vals):
    return sum(vals) / Decimal(len(vals)) if vals else None


def screen_one(stock):
    code, name = stock["code"], stock["name"]
    try:
        m = _fetch_metrics(code)
    except Exception as e:
        return {**stock, "status": "error", "error": str(e)[:80]}
    if not m or m["_years"] == 0:
        return {**stock, "status": "no_data"}

    roe = _avg(m["ROE"])
    net = _avg(m["순이익률"])
    op = _avg(m["영업이익률"])
    debt = m["부채비율"][-1] if m["부채비율"] else None

    fails = []
    if roe is None or roe < _MIN_ROE:
        fails.append(f"ROE {roe if roe is not None else 'NA'}<8")
    if net is None or net < _MIN_NET_MARGIN:
        fails.append(f"순이익률 {net if net is not None else 'NA'}<5")
    if op is None or op <= _MIN_OP_MARGIN:
        fails.append(f"영업이익률 {op if op is not None else 'NA'}<=0")
    if debt is not None and debt > _MAX_DEBT:
        fails.append(f"부채비율 {debt}>200")

    richness = "A" if m["_years"] >= 3 else ("B" if m["_years"] == 2 else "C")
    rnd = lambda x: round(float(x), 2) if x is not None else None
    # 통과 종목 점수: ROE + 순이익률 (높을수록 우량) — 딥리서치 우선순위용
    score = rnd((roe or 0) + (net or 0)) if not fails else None
    return {
        **stock, "status": "pass" if not fails else "fail",
        "roe_avg": rnd(roe), "net_margin_avg": rnd(net),
        "op_margin_avg": rnd(op), "debt_ratio": rnd(debt),
        "years": m["_years"], "richness": richness,
        "score": score, "fails": fails,
    }


def cmd_run(codes=None):
    if codes:
        pool = [{"code": c.strip(), "name": c.strip(), "market": "?"} for c in codes.split(",")]
    else:
        if not os.path.exists(_POOL):
            print("召回池이 없습니다. kr_recall_pool.py build 먼저.", file=sys.stderr)
            sys.exit(1)
        pool = json.load(open(_POOL, encoding="utf-8"))["pool"]

    results, done = [], 0
    for s in pool:
        results.append(screen_one(s))
        done += 1
        if done % 25 == 0:
            sys.stderr.write(f"  ...{done}/{len(pool)} 처리\n")

    passed = [r for r in results if r["status"] == "pass"]
    passed.sort(key=lambda r: r.get("score") or 0, reverse=True)
    failed = [r for r in results if r["status"] == "fail"]
    errs = [r for r in results if r["status"] in ("error", "no_data")]

    out = {"total": len(results), "passed": len(passed),
           "failed": len(failed), "errors": len(errs),
           "pass_list": passed, "fail_list": failed, "error_list": errs}
    if not codes:
        os.makedirs(os.path.dirname(_OUT), exist_ok=True)
        json.dump(out, open(_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"去劣 1차 결과: {len(results)}종목 → 통과 {len(passed)} / 탈락 {len(failed)} / 데이터없음·에러 {len(errs)}")
    print(f"{'='*60}")
    print("통과 종목(ROE+순이익률 점수순):")
    for r in passed[:40]:
        print(f"  [{r['market']:6}] {r['name']}({r['code']})  "
              f"ROE {r['roe_avg']}%·순이익률 {r['net_margin_avg']}%·부채 {r['debt_ratio']}%  "
              f"({r['richness']},{r['years']}yr)")
    if not codes:
        print(f"\n저장: {_OUT}")


def main():
    ap = argparse.ArgumentParser(description="코스피/코스닥 去劣 1차 스크리너")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="스크리닝 실행")
    r.add_argument("--codes", help="특정 종목코드(쉼표구분)만 테스트")
    args = ap.parse_args()
    if args.cmd == "run":
        cmd_run(args.codes)


if __name__ == "__main__":
    main()
