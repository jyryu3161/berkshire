#!/usr/bin/env python3
"""코스피/코스닥 去劣 2차 정밀 스크리너 — 원본 7지표 완전판 (DART 원천, 다개년).

AI Berkshire 한국 확장 깔때기 ③단계. 원본 quality-screen.md 의 7지표 + 면제규칙 3종을
DART 다개년 재무(fnlttSinglAcntAll)와 주식총수 현황(stockTotqySttus)으로 그대로 재현.

원본 7지표(하나라도 걸리면 탈락):
    ① 평균 ROE(가용 최대 6년) < 8%                (자본효율)
    ② 5년 누적 FCF < 0                            (진짜 현금)
    ③ 이자보상배율(영업이익/이자지급) < 2배        (상환안전)
    ④ 매출총이익률(최근) < 15%                     (가격결정력)
    ⑤ OCF/순이익(가용연 평균) < 0.7               (이익의 질)
    ⑥ 순이익률(최근) < 5%                          (수익성)
    ⑦ 5년(가용) 주식 총발행수 팽창 > 20%          (주주 희석)

면제규칙(원본 豁免 A/B/C):
    A) ①ROE 미달 면제: 데이터<8년(상장초기 근사) + 매출총이익률>30% + 최근 OCF>0
    B) ⑥순이익률 미달 면제: 매출총이익률>30% + 최근 순이익률 상승/5%대 근접
    C) ④매출총이익률·⑥순이익률 면제: ROE>20% + OCF/NI>1.0 (코스트코형)

데이터: 사업보고서 3개년(당해/-2/-4 → 당기+전기로 최대 6년) + 주식총수 2개년.
종목당 DART 호출 ~5회. 1차 통과분(kr_screen_result.json)에 적용.

사용법:
    python3 tools/kr_quality_screen2.py run
    python3 tools/kr_quality_screen2.py run --codes 005930,196170,259960
"""

import argparse
import json
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dart_data

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_IN = os.path.join(_ROOT, "data", "kr_screen_result.json")
_OUT = os.path.join(_ROOT, "data", "kr_screen2_result.json")

# 기준연도(당해 사업보고서). 다개년: BASE, BASE-2, BASE-4 보고서 → 당기/전기로 6년.
_BASE = 2024
_REPORT_YEARS = [_BASE, _BASE - 2, _BASE - 4]      # 2024, 2022, 2020
_DILUTION_YEARS = (_BASE, _BASE - 4)               # 2024 vs 2020 (약 4~5년)

_corp_map = None


def _corp_code(stock_code):
    global _corp_map
    if _corp_map is None:
        _corp_map = {r["stock_code"]: r["corp_code"]
                     for r in dart_data._load_corpcode() if r["stock_code"]}
    return _corp_map.get(stock_code)


def _amt(v):
    if isinstance(v, dict):
        v = v.get("value")
    if v in (None, "", "-"):
        return None
    try:
        return Decimal(str(v).replace(",", ""))
    except Exception:
        return None


def _acct(items, sj_set, names, col="thstrm_amount"):
    for n in names:  # 정확 일치 우선
        for it in items:
            if it.get("sj_div") in sj_set and (it.get("account_nm") or "").replace(" ", "") == n:
                return _amt(it.get(col))
    for n in names:  # 부분 일치
        for it in items:
            if it.get("sj_div") in sj_set and n in (it.get("account_nm") or "").replace(" ", ""):
                return _amt(it.get(col))
    return None


def _fetch_year_report(corp, year):
    """해당연도 사업보고서 전체계정 (CFS 우선, OFS 대체). 당기/전기 2개년 반환."""
    for fs in ("CFS", "OFS"):
        d = dart_data._get("fnlttSinglAcntAll.json", corp_code=corp,
                           bsns_year=str(year), reprt_code="11011", fs_div=fs)
        items = d.get("list") or []
        if items:
            return items
    return None


def _series(corp):
    """다개년 재무 시계열 구성: {연도: {지표: 값}}."""
    yr = {}
    for ry in _REPORT_YEARS:
        items = _fetch_year_report(corp, ry)
        if not items:
            continue
        for y, col in ((ry, "thstrm_amount"), (ry - 1, "frmtrm_amount")):
            rev = _acct(items, {"IS", "CIS"}, ["매출액", "수익(매출액)", "영업수익"], col)
            gp = _acct(items, {"IS", "CIS"}, ["매출총이익"], col)
            cogs = _acct(items, {"IS", "CIS"}, ["매출원가"], col)
            if gp is None and rev is not None and cogs is not None:
                gp = rev - cogs
            ni = _acct(items, {"IS", "CIS"}, ["당기순이익", "당기순이익(손실)"], col)
            op = _acct(items, {"IS", "CIS"}, ["영업이익", "영업이익(손실)"], col)
            eq = _acct(items, {"BS"}, ["자본총계"], col)
            ocf = _acct(items, {"CF"}, ["영업활동현금흐름", "영업활동으로인한현금흐름"], col)
            capt = _acct(items, {"CF"}, ["유형자산의취득"], col)
            capi = _acct(items, {"CF"}, ["무형자산의취득"], col)
            intp = _acct(items, {"CF"}, ["이자의지급", "이자지급"], col)
            row = yr.setdefault(y, {})
            for k, v in (("rev", rev), ("gp", gp), ("ni", ni), ("op", op), ("eq", eq),
                         ("ocf", ocf), ("capt", capt), ("capi", capi), ("intp", intp)):
                if v is not None and k not in row:
                    row[k] = v
    return yr


def _shares(corp, year):
    d = dart_data._get("stockTotqySttus.json", corp_code=corp,
                       bsns_year=str(year), reprt_code="11011")
    tot = None
    for it in d.get("list") or []:
        se = (it.get("se") or "").replace(" ", "")
        if se in ("합계", "보통주"):
            v = _amt(it.get("istc_totqy"))
            if se == "합계":
                return v
            tot = tot or v
    return tot


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / Decimal(len(xs)) if xs else None


def screen_one(stock):
    code, name = stock["code"], stock["name"]
    corp = _corp_code(code)
    if not corp:
        return {**stock, "status2": "no_corp"}
    try:
        yr = _series(corp)
    except Exception as e:
        return {**stock, "status2": "error", "error": str(e)[:80]}
    if not yr:
        return {**stock, "status2": "no_data"}

    years = sorted(yr)
    latest = years[-1]
    L = yr[latest]

    # ① 평균 ROE (가용 최대 6년)
    roes = [yr[y]["ni"] / yr[y]["eq"] * 100 for y in years
            if yr[y].get("ni") is not None and yr[y].get("eq")]
    roe_avg = _avg(roes)
    # ② 5년 누적 FCF
    fcf_years = [yr[y]["ocf"] - (yr[y].get("capt") or 0) - (yr[y].get("capi") or 0)
                 for y in years[-5:] if yr[y].get("ocf") is not None]
    fcf_sum = sum(fcf_years) if fcf_years else None
    # ③ 이자보상
    icr = (L["op"] / L["intp"]) if L.get("op") is not None and L.get("intp") else None
    # ④ 매출총이익률
    gm = (L["gp"] / L["rev"] * 100) if L.get("gp") is not None and L.get("rev") else None
    # ⑤ OCF/NI (가용 평균)
    ocf_ni_list = [yr[y]["ocf"] / yr[y]["ni"] for y in years
                   if yr[y].get("ocf") is not None and yr[y].get("ni") and yr[y]["ni"] > 0]
    ocf_ni = _avg(ocf_ni_list)
    # ⑥ 순이익률
    nm = (L["ni"] / L["rev"] * 100) if L.get("ni") is not None and L.get("rev") else None
    # ⑦ 주식 희석
    try:
        s_new, s_old = _shares(corp, _DILUTION_YEARS[0]), _shares(corp, _DILUTION_YEARS[1])
    except Exception:
        s_new = s_old = None
    dilution = ((s_new - s_old) / s_old * 100) if s_new and s_old else None

    # 면제규칙 판정
    n_years = len(years)
    ex_A = (gm is not None and gm > 30 and n_years < 8 and L.get("ocf", 0) and L["ocf"] > 0)
    ex_B = (gm is not None and gm > 30 and nm is not None and nm >= 4)
    ex_C = (roe_avg is not None and roe_avg > 20 and ocf_ni is not None and ocf_ni > 1.0)

    fails = []
    if roe_avg is None or roe_avg < 8:
        if not (ex_A or ex_C):
            fails.append(f"ROE평균 {_r(roe_avg)}<8")
    if fcf_sum is not None and fcf_sum <= 0:
        fails.append(f"누적FCF {_r(fcf_sum/Decimal(1e8),0)}억<=0")
    if icr is not None and icr < 2:
        fails.append(f"이자보상 {_r(icr)}<2")
    if gm is not None and gm < 15 and not ex_C:
        fails.append(f"매출총이익률 {_r(gm)}<15")
    if ocf_ni is not None and ocf_ni < Decimal("0.7"):
        fails.append(f"OCF/NI {_r(ocf_ni)}<0.7")
    if (nm is None or nm < 5) and not (ex_B or ex_C):
        fails.append(f"순이익률 {_r(nm)}<5")
    # ⑦ 희석: 20~100%는 조직적 희석으로 탈락. >100%는 상장/액면분할/합병 아티팩트로 간주(원본의 非M&A 예외) → 표기만.
    if dilution is not None and 20 < dilution <= 100:
        fails.append(f"주식희석 {_r(dilution)}%>20")

    exempt = [x for x, on in (("A", ex_A), ("B", ex_B), ("C", ex_C)) if on]
    return {**stock, "status2": "pass" if not fails else "fail",
            "roe_avg": _f(roe_avg), "fcf_sum_eok": _f(fcf_sum / Decimal(1e8)) if fcf_sum is not None else None,
            "interest_coverage": _f(icr), "gross_margin": _f(gm),
            "ocf_ni": _f(ocf_ni), "net_margin": _f(nm), "dilution_pct": _f(dilution),
            "years_used": n_years, "exemptions": exempt, "fails2": fails}


def _r(v, nd=2):
    return "NA" if v is None else round(float(v), nd)


def _f(v, nd=2):
    return None if v is None else round(float(v), nd)


def cmd_run(codes=None):
    if codes:
        pool = [{"code": c.strip(), "name": c.strip(), "market": "?", "score": None}
                for c in codes.split(",")]
    else:
        pool = json.load(open(_IN, encoding="utf-8"))["pass_list"]
    results, done = [], 0
    for s in pool:
        results.append(screen_one(s))
        done += 1
        if done % 10 == 0:
            sys.stderr.write(f"  ...{done}/{len(pool)}\n")
    passed = sorted([r for r in results if r["status2"] == "pass"],
                    key=lambda r: r.get("score") or 0, reverse=True)
    failed = [r for r in results if r["status2"] == "fail"]
    errs = [r for r in results if r["status2"] not in ("pass", "fail")]
    out = {"total": len(results), "passed": len(passed), "failed": len(failed),
           "errors": len(errs), "pass_list": passed, "fail_list": failed, "error_list": errs}
    if not codes:
        json.dump(out, open(_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n{'='*66}\n去劣 2차(7지표 완전판): {len(results)} → 통과 {len(passed)} / 탈락 {len(failed)} / 에러 {len(errs)}\n{'='*66}")
    for r in passed:
        ex = f" 면제[{','.join(r['exemptions'])}]" if r.get("exemptions") else ""
        print(f"  [{r['market']:6}] {r['name']}({r['code']})  ROE {r['roe_avg']}·GM {r['gross_margin']}·"
              f"OCF/NI {r['ocf_ni']}·FCF {r['fcf_sum_eok']}억·희석 {r['dilution_pct']}%{ex}")
    if codes:
        for r in failed:
            print(f"  ✗ {r['name']}({r['code']}): {', '.join(r['fails2'])}")
    if not codes:
        print(f"\n저장: {_OUT}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--codes")
    a = ap.parse_args()
    if a.cmd == "run":
        cmd_run(a.codes)


if __name__ == "__main__":
    main()
