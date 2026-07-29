#!/usr/bin/env python3
"""목표주가(Target Price) 산출기 — DCF · 상대가치(PER/EV-EBITDA/PBR) · RIM 3방식 교차검증.

설계 원칙(기존 툴체인과 동일): 외부 의존성 0, Python stdlib(Decimal)만 사용.
전문 금융 패키지 대신 자체 구현을 쓰는 이유 — (1) 프록시/무키 환경에서 재현성,
(2) 부동소수점 오차 없는 Decimal 정밀도, (3) 가정을 100% 투명하게 노출(GIGO 방지).

방법론 요약(4대가 프레임의 '버핏 재무 애널리스트'가 호출):
  1) DCF(현금흐름할인) — 절대가치. 예측가능한 성숙기업에 적합.
  2) 상대가치(Multiple) — 실무 표준. PER=EPS×타깃PER, EV/EBITDA(설비집약),
     PBR(금융·자산주). 시장 멀티플을 즉각 반영.
  3) RIM(잔여이익) — 주주가치 중심 절대가치. 장부가(BPS)+초과이익(ROE−자기자본비용).
     버핏식 가치투자 선호, 데이터 왜곡이 적음.
  교차검증: 방법별 적정가를 산출 → 가중평균(중립) + 보수적 하단 + 낙관 상단.

사용법:
    # 개별 방법
    python3 tools/valuation.py per --eps 9200 --target-per 11 --current 95400
    python3 tools/valuation.py pbr --bps 52700 --target-pbr 1.8 --current 95400
    python3 tools/valuation.py ev-ebitda --ebitda 11000 --mult 8 --net-debt 8000 --shares 0.708 --current 95400
        (ebitda·net-debt 단위 '억', shares 단위 '억주')
    python3 tools/valuation.py rim --bps 52700 --roe 0.171 --coe 0.09 --growth 0.03 --years 5 --current 95400
    python3 tools/valuation.py dcf --fcf-ps 8000 --wacc 0.09 --g1 0.08 --years 5 --g-term 0.02 --current 95400
        (또는 총 FCF 사용: --fcf 6000 --net-debt 8000 --shares 0.708, 단위 '억'/'억주')

    # 종합 교차검증(권장) — JSON 입력, 마크다운 표 출력
    python3 tools/valuation.py target --config report_valuation.json --md

target JSON 스키마(적용 가능한 방법만 채우면 됨):
{
  "name": "코웨이", "current": 95400,
  "per":  {"eps": 9200, "target_per": 11},
  "pbr":  {"bps": 52700, "target_pbr": 1.8},
  "ev_ebitda": {"ebitda": 11000, "mult": 8, "net_debt": 8000, "shares": 0.708},
  "rim":  {"bps": 52700, "roe": 0.171, "coe": 0.09, "growth": 0.03, "years": 5},
  "dcf":  {"fcf_ps": 8000, "wacc": 0.09, "g1": 0.08, "years": 5, "g_term": 0.02},
  "weights": {"per": 0.35, "rim": 0.35, "dcf": 0.20, "pbr": 0.10, "ev_ebitda": 0.0}
}
단위: 금액 원, ebitda/net_debt/fcf 는 '억원', shares 는 '억주'. weights 생략 시 균등.
"""

import argparse
import json
import sys
from decimal import Decimal, getcontext, ROUND_HALF_EVEN

getcontext().prec = 28
getcontext().rounding = ROUND_HALF_EVEN

D = Decimal
_EOK = D("1e8")   # 1억


def _d(x):
    if x is None:
        return None
    return x if isinstance(x, Decimal) else D(str(x))


def _won(x):
    """원 단위 반올림 정수 문자열(콤마)."""
    if x is None:
        return "-"
    return f"{int(_d(x).quantize(D('1'))):,}원"


def _pct(x):
    if x is None:
        return "-"
    return f"{_d(x) * 100:.1f}%"


def _upside(fair, current):
    if fair is None or current in (None, 0):
        return None
    return _d(fair) / _d(current) - 1


# ── 1) 상대가치 ─────────────────────────────────────────
def per_value(eps, target_per):
    """적정주가 = 주당순이익(EPS) × 타깃 PER."""
    return _d(eps) * _d(target_per)


def pbr_value(bps, target_pbr):
    """적정주가 = 주당순자산(BPS) × 타깃 PBR."""
    return _d(bps) * _d(target_pbr)


def ev_ebitda_value(ebitda_eok, mult, net_debt_eok, shares_eok):
    """EV = EBITDA × 배수 → 자기자본 = EV − 순차입금 → ÷ 주식수.
    ebitda/net_debt 단위 억원, shares 단위 억주."""
    ev = _d(ebitda_eok) * _d(mult)                 # 억원
    equity = ev - _d(net_debt_eok)                 # 억원
    shares = _d(shares_eok)                         # 억주
    if shares == 0:
        return None
    # 억원/억주 = 원/주  (1e8/1e8 상쇄)
    return equity / shares


# ── 2) DCF(현금흐름할인) ────────────────────────────────
def dcf_per_share(fcf_ps, wacc, g1, years, g_term):
    """주당 FCF 기반 2단계 DCF. 예측기간 g1 성장 후 영구성장 g_term.
    반환: 주당 내재가치(원)."""
    r, g1, gt = _d(wacc), _d(g1), _d(g_term)
    if r <= gt:
        raise ValueError("WACC는 영구성장률(g_term)보다 커야 함")
    f = _d(fcf_ps)
    pv = D(0)
    last = f
    for t in range(1, int(years) + 1):
        last = f * (D(1) + g1) ** t
        pv += last / (D(1) + r) ** t
    # 말기가치: 예측 마지막 FCF가 gt로 영구성장
    tv = last * (D(1) + gt) / (r - gt)
    pv += tv / (D(1) + r) ** int(years)
    return pv


def dcf_total(fcf_eok, wacc, g1, years, g_term, net_debt_eok, shares_eok):
    """총 FCF(FCFF) 기반 DCF → 기업가치 − 순차입금 → 주당. 단위 억원/억주."""
    per_share_ev = dcf_per_share(fcf_eok, wacc, g1, years, g_term)  # 억원 규모
    equity = per_share_ev - _d(net_debt_eok)                       # 억원
    shares = _d(shares_eok)
    if shares == 0:
        return None
    return equity / shares


# ── 3) RIM(잔여이익모델) ────────────────────────────────
def rim_value(bps, roe, coe, growth=0, years=5, fade_to=None):
    """잔여이익모델. 적정주가 = 기초 BPS + Σ 잔여이익 현재가치 + 말기가치.
      잔여이익_t = (ROE_t − 자기자본비용) × 기초자본_t
      자본은 유보(=ROE×(1−배당성향))만큼 성장하나, 간명화를 위해 growth로 자본성장 지정.
    fade_to: 지정 시 ROE가 years 동안 fade_to까지 선형 수렴(보수적).
    반환: 적정주가(원)."""
    b0, r, ce = _d(bps), _d(roe), _d(coe)
    g = _d(growth)
    n = int(years)
    val = b0
    book = b0
    roe_t = r
    step = (r - _d(fade_to)) / n if fade_to is not None else D(0)
    last_ri = D(0)
    for t in range(1, n + 1):
        if fade_to is not None:
            roe_t = r - step * t
        ri = (roe_t - ce) * book                    # 잔여이익(원/주)
        val += ri / (D(1) + ce) ** t
        last_ri = ri
        book = book * (D(1) + g)                     # 다음기 기초자본
    # 말기가치: 마지막 잔여이익이 g로 영구성장
    if ce > g:
        tv = last_ri * (D(1) + g) / (ce - g)
        val += tv / (D(1) + ce) ** n
    return val


def rim_closed(bps, roe, coe, growth=0):
    """Ohlson 단일단계 폐형해: P = B × [1 + (ROE − r)/(r − g)]."""
    b, r, ce, g = _d(bps), _d(roe), _d(coe), _d(growth)
    if ce <= g:
        raise ValueError("자기자본비용은 성장률보다 커야 함")
    return b * (D(1) + (r - ce) / (ce - g))


# ── 종합 교차검증 ───────────────────────────────────────
def target_cross_check(cfg):
    current = _d(cfg.get("current"))
    methods = {}  # key -> (label, fair)
    c = cfg
    if "per" in c:
        methods["per"] = ("PER (EPS×타깃PER)", per_value(c["per"]["eps"], c["per"]["target_per"]))
    if "pbr" in c:
        methods["pbr"] = ("PBR (BPS×타깃PBR)", pbr_value(c["pbr"]["bps"], c["pbr"]["target_pbr"]))
    if "ev_ebitda" in c:
        e = c["ev_ebitda"]
        methods["ev_ebitda"] = ("EV/EBITDA", ev_ebitda_value(e["ebitda"], e["mult"], e.get("net_debt", 0), e["shares"]))
    if "rim" in c:
        r = c["rim"]
        methods["rim"] = ("RIM (잔여이익)", rim_value(r["bps"], r["roe"], r["coe"], r.get("growth", 0), r.get("years", 5), r.get("fade_to")))
    if "dcf" in c:
        d = c["dcf"]
        if "fcf_ps" in d:
            fair = dcf_per_share(d["fcf_ps"], d["wacc"], d["g1"], d.get("years", 5), d["g_term"])
        else:
            fair = dcf_total(d["fcf"], d["wacc"], d["g1"], d.get("years", 5), d["g_term"], d.get("net_debt", 0), d["shares"])
        methods["dcf"] = ("DCF (현금흐름할인)", fair)

    # 가중치(지정 없으면 균등, 지정된 방법만)
    w = c.get("weights", {})
    keys = [k for k in methods if methods[k][1] is not None]
    if w:
        weights = {k: _d(w.get(k, 0)) for k in keys}
        tot = sum(weights.values())
        if tot == 0:
            weights = {k: D(1) for k in keys}; tot = _d(len(keys))
    else:
        weights = {k: D(1) for k in keys}; tot = _d(len(keys))

    blended = sum(methods[k][1] * weights[k] for k in keys) / tot if keys else None
    fair_vals = [methods[k][1] for k in keys]
    low = min(fair_vals) if fair_vals else None      # 보수적 하단
    high = max(fair_vals) if fair_vals else None      # 낙관 상단

    return {
        "name": c.get("name", ""),
        "current": current,
        "methods": {k: {"label": methods[k][0], "fair": methods[k][1],
                        "weight": weights.get(k), "upside": _upside(methods[k][1], current)}
                    for k in keys},
        "blended": blended, "low": low, "high": high,
        "blended_upside": _upside(blended, current),
        "low_upside": _upside(low, current), "high_upside": _upside(high, current),
    }


def _md_report(res):
    """캐노니컬 '목표주가 교차검증' 섹션(모든 KR 리포트 공통 양식).

    출력 순서 고정: ① 헤더(항상 동일) → ② 방법별 적정주가 + **가중평균(중립)** 행
    → ③ 보수·중립·공격 3밴드 표 → ④ 해석 1줄. 이 함수 출력을 리포트에 그대로
    삽입하면 종목이 달라도 동일 양식이 보장된다."""
    L = []
    L.append("## 목표주가 교차검증 (PER·PBR·EV/EBITDA·RIM·SOTP)")
    L.append("")
    L.append("| 방법 | 적정주가 | 가중치 | 현재가 대비 |")
    L.append("|---|---|---|---|")
    for k, m in res["methods"].items():
        L.append(f"| {m['label']} | {_won(m['fair'])} | {_pct(m['weight']/sum(x['weight'] for x in res['methods'].values()))} | {_pct(m['upside'])} |")
    L.append(f"| **가중평균(중립)** | **{_won(res['blended'])}** | 100% | **{_pct(res['blended_upside'])}** |")
    L.append("")
    L.append("| 밴드 | 목표주가 | 현재가 대비 | 성격 |")
    L.append("|---|---|---|---|")
    L.append(f"| 보수 | {_won(res['low'])} | {_pct(res['low_upside'])} | 방법 중 최소치 · 매수 기준선 |")
    L.append(f"| 중립 | {_won(res['blended'])} | {_pct(res['blended_upside'])} | 방법별 가중평균 |")
    L.append(f"| 공격 | {_won(res['high'])} | {_pct(res['high_upside'])} | 방법 중 최대치 |")
    L.append("")
    L.append(f"현재가 {_won(res['current'])} 기준 · 보수 {_won(res['low'])}(하단)을 매수 기준선, "
             f"중립 {_won(res['blended'])}(가중)을 적정가치로 본다.")
    # 리포트 본문 규약: % 리터럴 대신 '퍼센트'(가중치 표기는 배수감 유지 위해 그대로 두지 않고 통일)
    return "\n".join(L).replace("%", "퍼센트")


def _to_plain(o):
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, dict):
        return {k: _to_plain(v) for k, v in o.items()}
    return o


def main():
    ap = argparse.ArgumentParser(description="목표주가 산출기 (DCF·상대가치·RIM 교차검증)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("per"); p.add_argument("--eps", required=True); p.add_argument("--target-per", required=True); p.add_argument("--current")
    p = sub.add_parser("pbr"); p.add_argument("--bps", required=True); p.add_argument("--target-pbr", required=True); p.add_argument("--current")
    p = sub.add_parser("ev-ebitda"); p.add_argument("--ebitda", required=True); p.add_argument("--mult", required=True)
    p.add_argument("--net-debt", default="0"); p.add_argument("--shares", required=True); p.add_argument("--current")
    p = sub.add_parser("rim"); p.add_argument("--bps", required=True); p.add_argument("--roe", required=True); p.add_argument("--coe", required=True)
    p.add_argument("--growth", default="0"); p.add_argument("--years", default="5"); p.add_argument("--fade-to"); p.add_argument("--current")
    p = sub.add_parser("dcf"); p.add_argument("--fcf-ps"); p.add_argument("--fcf"); p.add_argument("--wacc", required=True)
    p.add_argument("--g1", required=True); p.add_argument("--years", default="5"); p.add_argument("--g-term", required=True)
    p.add_argument("--net-debt", default="0"); p.add_argument("--shares"); p.add_argument("--current")
    p = sub.add_parser("target"); p.add_argument("--config", required=True); p.add_argument("--md", action="store_true")

    a = ap.parse_args()

    def show(label, fair, current):
        up = _upside(fair, _d(current)) if current else None
        out = {"method": label, "fair_value_won": float(_d(fair).quantize(D('1'))),
               "current": float(_d(current)) if current else None,
               "upside": float(up) if up is not None else None}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        print(f"→ {label}: 적정주가 {_won(fair)}" + (f" (현재가 대비 {_pct(up)})" if up is not None else ""))

    if a.cmd == "per":
        show("PER", per_value(a.eps, a.target_per), a.current)
    elif a.cmd == "pbr":
        show("PBR", pbr_value(a.bps, a.target_pbr), a.current)
    elif a.cmd == "ev-ebitda":
        show("EV/EBITDA", ev_ebitda_value(a.ebitda, a.mult, a.net_debt, a.shares), a.current)
    elif a.cmd == "rim":
        show("RIM", rim_value(a.bps, a.roe, a.coe, a.growth, a.years, a.fade_to), a.current)
    elif a.cmd == "dcf":
        if a.fcf_ps:
            fair = dcf_per_share(a.fcf_ps, a.wacc, a.g1, a.years, a.g_term)
        elif a.fcf and a.shares:
            fair = dcf_total(a.fcf, a.wacc, a.g1, a.years, a.g_term, a.net_debt, a.shares)
        else:
            sys.stderr.write("❌ dcf: --fcf-ps 또는 (--fcf --shares) 필요\n"); sys.exit(2)
        show("DCF", fair, a.current)
    elif a.cmd == "target":
        cfg = json.load(open(a.config, encoding="utf-8")) if a.config != "-" else json.load(sys.stdin)
        res = target_cross_check(cfg)
        if a.md:
            print(_md_report(res))
        else:
            print(json.dumps(_to_plain(res), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
