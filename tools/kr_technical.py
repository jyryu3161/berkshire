#!/usr/bin/env python3
"""한국주식 기술적 분석 — 월봉·주봉 장기 차트 분석(밸류와 '타이밍'의 분리).

동기: 4대가 프레임은 '싼 우량주'를 잘 찾지만, 사이클/박스권 종목은
'싸다(밸류)'와 '쌀 때(타이밍)'가 다르다. 오리온처럼 9년 박스권 상단에서
저PER '매수'가 나오면 신규 진입 위험보상은 오히려 비대칭이다. 이 도구는
월봉·주봉으로 (1)추세 vs 박스 분류, (2)현재 위치(상단/중단/하단),
(3)이평 이격·ATH 낙폭·RSI, (4)신규진입 적합도를 산출해 리포트에 넣는다.

설계 원칙: 데이터·지표 계산은 외부 의존성 0(stdlib + curl, 네이버 무키).
차트 PNG(`chart`)만 matplotlib/mplfinance 사용(설치돼 있으면).

사용법:
    python3 tools/kr_technical.py analyze 271560                 # 월봉 분석(JSON)
    python3 tools/kr_technical.py analyze 271560 --tf week       # 주봉
    python3 tools/kr_technical.py analyze 271560 --md            # 리포트 삽입용 마크다운
    python3 tools/kr_technical.py chart 271560 --tf month --out /tmp/x.png
"""

import argparse
import ast
import datetime
import json
import subprocess
import sys

_TF = {"month": "month", "week": "week", "day": "day"}
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def _curl(url):
    r = subprocess.run(["/usr/bin/curl", "-s", "--noproxy", "*",
                        "-H", f"User-Agent: {_UA}",
                        "-H", "Referer: https://finance.naver.com/", url],
                       capture_output=True, timeout=20)
    return r.stdout.decode("utf-8", errors="replace")


def fetch_ohlc(code, timeframe="month", start="20100101", end=None):
    """네이버 차트 API에서 OHLC 취득 → [{d,o,h,l,c,v}, ...] (오래된→최신)."""
    end = end or datetime.datetime.now().strftime("%Y%m%d")
    url = (f"https://api.finance.naver.com/siseJson.naver?symbol={code}"
           f"&requestType=1&startTime={start}&endTime={end}&timeframe={_TF.get(timeframe,'month')}")
    raw = _curl(url).strip()
    rows = ast.literal_eval(raw)
    out = []
    for r in rows[1:]:
        try:
            o, h, l, c = float(r[1]), float(r[2]), float(r[3]), float(r[4])
            # 결함 봉 제외: 거래정지 등으로 O/H/L/C 중 0 이하가 있으면 스킵(ATL=0 왜곡 방지)
            if min(o, h, l, c) <= 0:
                continue
            out.append({"d": r[0], "o": o, "h": h, "l": l, "c": c, "v": float(r[5])})
        except Exception:
            continue
    return out


# ── 지표 ────────────────────────────────────────────────
def _sma(xs, n):
    return sum(xs[-n:]) / n if len(xs) >= n else None


def _rsi(closes, n=14):
    if len(closes) <= n:
        return None
    gains = losses = 0.0
    for i in range(-n, 0):
        ch = closes[i] - closes[i - 1]
        gains += max(ch, 0); losses += max(-ch, 0)
    ag, al = gains / n, losses / n
    if al == 0:
        return 100.0
    rs = ag / al
    return 100 - 100 / (1 + rs)


def _pct_rank(xs, v):
    return sum(1 for x in xs if x <= v) / len(xs)


def analyze(code, timeframe="month"):
    D = fetch_ohlc(code, timeframe)
    if len(D) < 12:
        return {"error": "데이터 부족", "code": code, "n": len(D)}
    closes = [x["c"] for x in D]
    highs = [x["h"] for x in D]
    lows = [x["l"] for x in D]
    cur = closes[-1]
    ath = max(highs); ath_d = next(x["d"] for x in D if x["h"] == ath)
    atl = min(lows); atl_d = next(x["d"] for x in D if x["l"] == atl)

    # 이평(해당 타임프레임 기준 봉 수)
    ma_defs = {"month": [(12, "1y"), (36, "3y"), (60, "5y")],
               "week": [(13, "13w"), (52, "1y"), (104, "2y")],
               "day": [(20, "20d"), (60, "60d"), (120, "120d")]}[timeframe]
    mas = {}
    for n, lbl in ma_defs:
        v = _sma(closes, n)
        mas[lbl] = {"value": v, "gap": (cur / v - 1) if v else None}

    span = ath - atl
    pos_in_range = (cur - atl) / span if span else None      # 0=바닥,1=천장
    pct = _pct_rank(closes, cur)                              # 종가 백분위
    dd = cur / ath - 1                                        # ATH 대비 낙폭

    # 추세 vs 박스 분류: 순변화 vs 진폭
    net = closes[-1] / closes[0] - 1
    years = max(1, len(D) / {"month": 12, "week": 52, "day": 250}[timeframe])
    cagr = (closes[-1] / closes[0]) ** (1 / years) - 1
    amp = span / atl                                         # 박스 진폭 배수
    # 최근 추세: 마지막 20% 구간의 이평 기울기 부호
    tail = closes[-max(6, len(closes) // 10):]
    slope_up = tail[-1] > _sma(tail, len(tail))
    if abs(cagr) < 0.06 and amp > 0.5:
        regime = "박스권(사이클)"
    elif cagr >= 0.10 and dd > -0.35:
        regime = "장기 우상향 추세"
    elif cagr <= -0.05:
        regime = "장기 하락 추세"
    else:
        regime = "완만/혼조"

    # 현재 위치 라벨 — 드문 꼬리(ATL/ATH)에 왜곡되지 않도록 '종가 백분위'(체류 위치) 기준.
    # 이격(3년 이평 대비)이 크면 상단 판정을 강화한다.
    long_gap = mas[ma_defs[1][1]]["gap"] or 0      # 3y(또는 해당 중기) 이평 이격
    extended = long_gap >= 0.15
    if pct >= 0.80 or (pct >= 0.7 and extended):
        loc = "상단(고평가 구간)"
    elif pct >= 0.45:
        loc = "중단"
    else:
        loc = "하단(저평가 구간)"

    # 신규 진입 적합도(밸류가 매력적이라는 전제하의 '타이밍')
    if regime.startswith("박스"):
        if loc.startswith("상단"):
            entry = "부적합 — 박스 상단. 신규매수는 위험보상 비대칭(밸류 매수는 하단서 분할)"
        elif loc.startswith("하단"):
            entry = "적합 — 박스 하단, 밸류+타이밍 정렬"
        else:
            entry = "중립 — 박스 중단, 분할 접근"
    elif regime.endswith("추세") and "우상향" in regime:
        entry = "이평 지지 확인 후 눌림목 분할(추세 유효)" if (mas[ma_defs[1][1]]["gap"] or 0) > -0.1 else "추세 훼손 주의"
    else:
        entry = "관망 — 방향성 불명확, 이평 회복·거래량 확인"

    rng_lo = round(atl / 1000) * 1000
    rng_hi = round(ath / 1000) * 1000
    return {
        "code": code, "timeframe": timeframe, "n_bars": len(D),
        "period": f"{D[0]['d']}~{D[-1]['d']}",
        "current": cur,
        "ath": ath, "ath_date": ath_d, "atl": atl, "atl_date": atl_d,
        "drawdown_from_ath": dd,
        "range_low": rng_lo, "range_high": rng_hi,
        "position_in_range": pos_in_range, "close_percentile": pct,
        "location": loc,
        "cagr": cagr, "amplitude": amp, "regime": regime, "trend_up": slope_up,
        "ma": mas, "rsi14": _rsi(closes),
        "entry_timing": entry,
    }


def _p(x, nd=0):
    return "-" if x is None else (f"{x*100:.1f}%" if abs(x) < 5 else f"{x:,.{nd}f}")


def to_md(a):
    if a.get("error"):
        return f"기술적 분석 실패: {a['error']}"
    tf = {"month": "월봉", "week": "주봉", "day": "일봉"}[a["timeframe"]]
    L = [f"## 기술적 분석 ({tf} 장기, {a['period']})", ""]
    L.append("| 항목 | 값 | 해석 |")
    L.append("|---|---|---|")
    L.append(f"| 국면 분류 | {a['regime']} | 추세주면 눌림목, 박스주면 하단이 매수 자리 |")
    L.append(f"| 현재가 | {a['current']:,.0f}원 | 종가 백분위 상위 {(1-a['close_percentile'])*100:.0f}% |")
    L.append(f"| 장기 레인지 | {a['range_low']:,.0f}~{a['range_high']:,.0f}원 | 현재 위치: {a['location']} |")
    L.append(f"| ATH 대비 | {a['drawdown_from_ath']*100:+.1f}% | 고점 {a['ath']:,.0f}({a['ath_date'][:6]}) |")
    for lbl, m in a["ma"].items():
        L.append(f"| 이평 {lbl} 이격 | {m['gap']*100:+.1f}% | {m['value']:,.0f}원 대비 |" if m["value"] else f"| 이평 {lbl} | 데이터부족 | |")
    L.append(f"| CAGR(상장이래) | {a['cagr']*100:+.1f}% | 진폭 {a['amplitude']*100:.0f}% |")
    if a["rsi14"] is not None:
        L.append(f"| RSI(14) | {a['rsi14']:.0f} | {'과열' if a['rsi14']>=70 else '침체' if a['rsi14']<=30 else '중립'} |")
    L.append("")
    L.append(f"신규 진입 타이밍 — {a['entry_timing']}")
    L.append("")
    L.append("※ 주의: 위는 '타이밍/가격 안전마진' 판단이며 사업가치(4대가 매수의견)와 별개다. "
             "'싸다(밸류) ≠ 쌀 때(타이밍)' — 박스권 상단의 저PER은 밸류트랩일 수 있다.")
    return "\n".join(L)


def make_chart(code, timeframe="month", out=None, name=None):
    import matplotlib
    matplotlib.use("Agg")
    import pandas as pd, mplfinance as mpf
    D = fetch_ohlc(code, timeframe)
    idx = [datetime.datetime.strptime(x["d"], "%Y%m%d") for x in D]
    import pandas
    df = pd.DataFrame({"Open": [x["o"] for x in D], "High": [x["h"] for x in D],
                       "Low": [x["l"] for x in D], "Close": [x["c"] for x in D],
                       "Volume": [x["v"] for x in D]}, index=pandas.DatetimeIndex(idx))
    a = analyze(code, timeframe)
    ns = {"month": [12, 36], "week": [13, 52], "day": [20, 60]}[timeframe]
    ap = [mpf.make_addplot(df["Close"].rolling(ns[0]).mean(), color="#2563eb", width=1.1),
          mpf.make_addplot(df["Close"].rolling(ns[1]).mean(), color="#f59e0b", width=1.1)]
    mc = mpf.make_marketcolors(up="#e11d48", down="#2563eb", edge="inherit", wick="inherit", volume="#9ca3af")
    st = mpf.make_mpf_style(base_mpf_style="yahoo", marketcolors=mc, gridstyle=":", facecolor="white")
    out = out or f"/tmp/{code}_{timeframe}.png"
    # matplotlib 기본 폰트에 한글 글리프가 없어 제목은 ASCII만(캡션·표는 한글 유지)
    title_name = name if (name and name.isascii()) else code
    ttl = f"{title_name}" + (f" ({code})" if str(title_name) != str(code) else "") + f" {timeframe}  |  KRW"
    fig, axes = mpf.plot(df, type="candle", style=st, addplot=ap, volume=True, returnfig=True,
                         figsize=(13, 7.5), xrotation=0, title=f"\n{ttl}")
    ax = axes[0]
    for y, c, t in [(a["range_high"], "#dc2626", f"range hi {a['range_high']:,.0f}"),
                    (a["range_low"], "#16a34a", f"range lo {a['range_low']:,.0f}"),
                    (a["current"], "#111827", f"now {a['current']:,.0f}")]:
        ax.axhline(y, color=c, ls="--", lw=1, alpha=.7)
        ax.text(0.004, y, t, color=c, fontsize=9, va="bottom", transform=ax.get_yaxis_transform())
    ax.axhspan(a["range_low"], a["range_high"], color="#94a3b8", alpha=.05)
    fig.savefig(out, dpi=130, bbox_inches="tight")
    return out


def main():
    ap = argparse.ArgumentParser(description="한국주식 기술적 분석(월봉·주봉)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    an = sub.add_parser("analyze"); an.add_argument("code"); an.add_argument("--tf", default="month", choices=list(_TF)); an.add_argument("--md", action="store_true")
    ch = sub.add_parser("chart"); ch.add_argument("code"); ch.add_argument("--tf", default="month", choices=list(_TF)); ch.add_argument("--out"); ch.add_argument("--name")
    a = ap.parse_args()
    if a.cmd == "analyze":
        res = analyze(a.code, a.tf)
        print(to_md(res) if a.md else json.dumps(res, ensure_ascii=False, indent=2))
    elif a.cmd == "chart":
        print("saved:", make_chart(a.code, a.tf, a.out, a.name))


if __name__ == "__main__":
    main()
