#!/usr/bin/env python3
"""코스피/코스닥 去劣 2차 정밀 스크리너 — DART 전체 재무제표(원천, 무료키).

AI Berkshire 한국 확장 깔때기 ③단계. 1차 통과분(kr_screen_result.json)에
원본 7지표 중 현금흐름·원가 기반 지표를 DART fnlttSinglAcntAll(전체계정)로 적용해
'일류 기업'만 남긴다. 통과분이 딥리서치(/investment-team) 대상.

2차 지표(DART 원천, 최근 사업보고서의 당기/전기):
    ① 매출총이익률       >= 15%     (매출총이익/매출액)
    ② 이자보상배율        >= 2배     (영업이익/이자지급, 산출 가능 시)
    ③ OCF/NI            >= 0.7     (영업활동현금흐름/당기순이익 — 이익의 질)
    ④ 누적 FCF(가용연수)   >  0       (Σ 영업활동CF − 유형·무형자산 취득)
  하나라도 미달 → 탈락(사유 기록).

주의: 원본 '5년 누적 FCF'·'5년 주식희석'은 완전한 10년 히스토리 대신 DART 단일보고서의
당기/전기(2년)로 근사한다(각 종목 1콜, 비용 절감). 필요 시 다개년 보고서로 확장 가능.

사용법:
    python3 tools/kr_quality_screen2.py run                       # 1차 통과분 전체
    python3 tools/kr_quality_screen2.py run --codes 196170,058470 # 특정 종목 테스트

Python >= 3.8, 외부 의존성 없음. DART 키 필요(~/.dart_api_key).
"""

import argparse
import json
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dart_data  # 기존 DART 헬퍼 재사용(_get, _load_corpcode, _api_key)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_IN = os.path.join(_ROOT, "data", "kr_screen_result.json")
_OUT = os.path.join(_ROOT, "data", "kr_screen2_result.json")

_MIN_GROSS = Decimal("15")
_MIN_ICR = Decimal("2")
_MIN_OCF_NI = Decimal("0.7")

_corp_map = None


def _corp_code(stock_code):
    global _corp_map
    if _corp_map is None:
        _corp_map = {r["stock_code"]: r["corp_code"]
                     for r in dart_data._load_corpcode() if r["stock_code"]}
    return _corp_map.get(stock_code)


def _amt(v):
    if v in (None, "", "-"):
        return None
    try:
        return Decimal(str(v).replace(",", ""))
    except Exception:
        return None


def _pick(items, sj_set, name_exact=None, name_has=None):
    """sj_div ∈ sj_set 이고 account_nm 이 조건에 맞는 첫 계정의 (당기,전기) 반환."""
    for it in items:
        if it.get("sj_div") not in sj_set:
            continue
        nm = (it.get("account_nm") or "").replace(" ", "")
        if name_exact and nm != name_exact:
            continue
        if name_has and name_has not in nm:
            continue
        return _amt(it.get("thstrm_amount")), _amt(it.get("frmtrm_amount"))
    return None, None


def _pick_any(items, sj_set, names):
    """names 를 순서대로 시도(정확 일치 우선), 없으면 부분 일치."""
    for n in names:
        t, f = _pick(items, sj_set, name_exact=n)
        if t is not None:
            return t, f
    for n in names:
        t, f = _pick(items, sj_set, name_has=n)
        if t is not None:
            return t, f
    return None, None


def _fetch_accounts(corp_code):
    """최근 사업보고서 전체계정 → 필요한 항목 dict. CFS 우선, 실패 시 OFS."""
    for year in ("2024", "2025"):  # 최신 확정 사업보고서 우선
        for fs in ("CFS", "OFS"):
            d = dart_data._get("fnlttSinglAcntAll.json", corp_code=corp_code,
                               bsns_year=year, reprt_code="11011", fs_div=fs)
            items = d.get("list") or []
            if items:
                return items, year, fs
    return None, None, None


def screen_one(stock):
    code, name = stock["code"], stock["name"]
    corp = _corp_code(code)
    if not corp:
        return {**stock, "status2": "no_corp"}
    try:
        items, year, fs = _fetch_accounts(corp)
    except Exception as e:
        return {**stock, "status2": "error", "error": str(e)[:80]}
    if not items:
        return {**stock, "status2": "no_data"}

    rev_t, rev_f = _pick_any(items, {"IS", "CIS"}, ["매출액", "수익(매출액)", "영업수익"])
    gp_t, gp_f = _pick(items, {"IS", "CIS"}, name_exact="매출총이익")
    cogs_t, _ = _pick(items, {"IS", "CIS"}, name_exact="매출원가")
    op_t, _ = _pick_any(items, {"IS", "CIS"}, ["영업이익", "영업이익(손실)"])
    ni_t, _ = _pick_any(items, {"IS", "CIS"}, ["당기순이익", "당기순이익(손실)", "당기순이익(손실)"])
    ocf_t, ocf_f = _pick(items, {"CF"}, name_has="영업활동현금흐름")
    if ocf_t is None:
        ocf_t, ocf_f = _pick(items, {"CF"}, name_has="영업활동")
    capt_t, capt_f = _pick(items, {"CF"}, name_has="유형자산의취득")
    capi_t, capi_f = _pick(items, {"CF"}, name_has="무형자산의취득")
    int_t, _ = _pick(items, {"CF"}, name_has="이자의지급")

    fails, m = [], {}

    # ① 매출총이익률
    if gp_t is None and rev_t and cogs_t is not None:
        gp_t = rev_t - cogs_t
    if rev_t and gp_t is not None and rev_t != 0:
        gm = gp_t / rev_t * 100
        m["gross_margin"] = round(float(gm), 2)
        if gm < _MIN_GROSS:
            fails.append(f"매출총이익률 {m['gross_margin']}<15")
    else:
        m["gross_margin"] = None

    # ② 이자보상배율 (이자지급 없거나 0이면 판정보류=통과)
    if op_t is not None and int_t and int_t != 0:
        icr = op_t / int_t
        m["interest_coverage"] = round(float(icr), 2)
        if icr < _MIN_ICR:
            fails.append(f"이자보상 {m['interest_coverage']}<2")
    else:
        m["interest_coverage"] = None

    # ③ OCF/NI
    if ocf_t is not None and ni_t and ni_t > 0:
        r = ocf_t / ni_t
        m["ocf_ni"] = round(float(r), 2)
        if r < _MIN_OCF_NI:
            fails.append(f"OCF/NI {m['ocf_ni']}<0.7")
    else:
        m["ocf_ni"] = None
        if ni_t is not None and ni_t <= 0:
            fails.append("당기순손실")

    # ④ 누적 FCF (당기+전기 가용연수)
    fcf_years = []
    for ocf, capt, capi in ((ocf_t, capt_t, capi_t), (ocf_f, capt_f, capi_f)):
        if ocf is not None:
            fcf_years.append(ocf - (capt or 0) - (capi or 0))
    if fcf_years:
        fcf_sum = sum(fcf_years)
        m["fcf_sum_eok"] = round(float(fcf_sum) / 1e8, 0)
        m["fcf_years"] = len(fcf_years)
        if fcf_sum <= 0:
            fails.append(f"누적FCF {m['fcf_sum_eok']}억<=0")
    else:
        m["fcf_sum_eok"] = None

    return {**stock, "status2": "pass" if not fails else "fail",
            "dart_year": year, "fs_div": fs, **m, "fails2": fails}


def cmd_run(codes=None):
    if codes:
        pool = [{"code": c.strip(), "name": c.strip(), "market": "?", "score": None}
                for c in codes.split(",")]
    else:
        if not os.path.exists(_IN):
            print("1차 결과가 없습니다. kr_quality_screen.py run 먼저.", file=sys.stderr)
            sys.exit(1)
        pool = json.load(open(_IN, encoding="utf-8"))["pass_list"]

    results, done = [], 0
    for s in pool:
        results.append(screen_one(s))
        done += 1
        if done % 20 == 0:
            sys.stderr.write(f"  ...{done}/{len(pool)} 처리\n")

    passed = [r for r in results if r["status2"] == "pass"]
    passed.sort(key=lambda r: r.get("score") or 0, reverse=True)
    failed = [r for r in results if r["status2"] == "fail"]
    errs = [r for r in results if r["status2"] not in ("pass", "fail")]

    out = {"total": len(results), "passed": len(passed), "failed": len(failed),
           "errors": len(errs), "pass_list": passed, "fail_list": failed, "error_list": errs}
    if not codes:
        json.dump(out, open(_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print(f"\n{'='*64}")
    print(f"去劣 2차(정밀) 결과: {len(results)} → 통과 {len(passed)} / 탈락 {len(failed)} / 에러 {len(errs)}")
    print(f"{'='*64}")
    print("최종 일류 통과(딥리서치 대상):")
    for r in passed:
        print(f"  [{r['market']:6}] {r['name']}({r['code']})  "
              f"GM {r.get('gross_margin')}%·ICR {r.get('interest_coverage')}·"
              f"OCF/NI {r.get('ocf_ni')}·FCF {r.get('fcf_sum_eok')}억")
    if failed and codes:
        print("\n탈락:")
        for r in failed:
            print(f"  {r['name']}({r['code']}): {', '.join(r['fails2'])}")
    if not codes:
        print(f"\n저장: {_OUT}")


def main():
    ap = argparse.ArgumentParser(description="코스피/코스닥 去劣 2차 정밀 스크리너(DART)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--codes", help="특정 종목코드(쉼표구분)만 테스트")
    args = ap.parse_args()
    if args.cmd == "run":
        cmd_run(args.codes)


if __name__ == "__main__":
    main()
