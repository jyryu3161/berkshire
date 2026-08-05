#!/usr/bin/env python3
"""매수 전 급락 원인 판단 게이트 — 14:30 매매 전(13:50) 실행.

신규 진입 후보(미보유 + 진입가 근접)를 골라, 헤드리스 Claude가 최근
뉴스·공시를 검색해 "하락 원인이 기업 근본 가치(이익력·경쟁지위·재무
건전성·지배구조)를 훼손하는가"를 판단한다. 결과는
local/trading/entry_gate.json 에 기록되고, 매매 엔진은 당일 allow 목록에
있는 종목만 신규 진입한다(파일이 없으면 신규 진입 전면 차단 fail-closed).

판단 실패·타임아웃·JSON 파싱 실패는 전부 allow=False 처리한다.
"""
import datetime
import json
import re
import sqlite3
import subprocess
import sys

_ROOT = "/home/ubuntu/ai-berkshire"
_LEDGER = f"{_ROOT}/local/trading/ledger.sqlite3"
_STRATEGY = f"{_ROOT}/local/trading/strategy.json"
_OUT = f"{_ROOT}/local/trading/entry_gate.json"
_CLAUDE = "/home/ubuntu/.local/bin/claude"
_KST = datetime.timezone(datetime.timedelta(hours=9))
_NEAR = 1.03  # 진입선 3% 이내 접근 시 후보로 간주(당일 변동 여유)


def _candidates():
    sys.path.insert(0, f"{_ROOT}/tools")
    import kr_deep_queue as q
    strategy = json.load(open(_STRATEGY, encoding="utf-8"))
    min_score = float(strategy.get("watch_entry_min_score", 3.5))
    db = sqlite3.connect(f"file:{_LEDGER}?mode=ro", uri=True)
    held = {r[0] for r in db.execute(
        "SELECT code FROM strategy_positions WHERE quantity > 0")}
    out = []
    for code, payload in db.execute(
        "SELECT code, payload FROM signals WHERE analyzed_at = "
        "(SELECT MAX(analyzed_at) FROM signals s2 WHERE s2.code = signals.code)"
    ).fetchall():
        if code in held:
            continue
        s = json.loads(payload)
        t = s.get("targets_krw")
        if not t:
            continue
        bands_ok = t["bear"] * 2 >= t["base"]
        if s.get("verdict") == "BUY":
            threshold = t["base"] if bands_ok else t["bear"]
        elif (s.get("verdict") == "WATCH" and bands_ok
              and (s.get("score") or 0) >= min_score):
            threshold = t["bear"]
        else:
            continue
        price = q._close_price(code)
        if price and price <= threshold * _NEAR:
            out.append({"code": code, "name": s.get("name", code),
                        "price": price, "threshold": threshold,
                        "verdict": s.get("verdict")})
    return out


def _judge(c):
    prompt = (
        f"{c['name']}({c['code']}) 종목이 자동매매 신규 매수 후보가 됐다. "
        f"현재가 {c['price']:,}원이 가치평가 기준 매수선({c['threshold']:,}원) 부근/이하다. "
        "웹 검색과 최근 공시로 최근 2~3주 이 종목의 주가 하락(또는 현재 가격대)의 원인을 파악하라. "
        "판단 기준: 원인이 기업의 근본 가치(이익창출력, 경쟁지위, 재무건전성, 지배구조)를 "
        "훼손하는 것(실적 구조 악화, 회계·법률 리스크, 대규모 증자·전용, 산업 구조 붕괴 등)이면 "
        "매수 부적합(allow=false). 시장 전반 조정, 수급, 일회성·단기 노이즈면 매수 가능(allow=true). "
        "불확실하면 allow=false. 응답의 마지막 줄에 JSON 한 줄만 출력하라: "
        '{"allow": true|false, "reason": "근거 1-2문장"}'
    )
    try:
        r = subprocess.run(
            [_CLAUDE, "-p", prompt, "--model", "claude-opus-5",
             "--dangerously-skip-permissions"],
            capture_output=True, text=True, timeout=600, cwd=_ROOT)
        matches = re.findall(r'\{[^{}]*"allow"[^{}]*\}', r.stdout)
        if matches:
            d = json.loads(matches[-1])
            return bool(d.get("allow")), str(d.get("reason", ""))[:500]
        return False, f"판단 출력 파싱 실패(exit {r.returncode})"
    except subprocess.TimeoutExpired:
        return False, "판단 타임아웃"
    except Exception as exc:
        return False, f"판단 실패: {exc}"


def main():
    today = datetime.datetime.now(_KST)
    candidates = _candidates()
    print(f"[{today:%F %T}] 후보 {len(candidates)}건:",
          [c["code"] for c in candidates], flush=True)
    decisions = {}
    for c in candidates:
        allow, reason = _judge(c)
        decisions[c["code"]] = {
            "allow": allow, "reason": reason, "name": c["name"],
            "price": c["price"], "threshold": c["threshold"],
            "verdict": c["verdict"],
        }
        print(f"  {c['name']}({c['code']}): {'허용' if allow else '차단'} — {reason}",
              flush=True)
    json.dump({"date": today.strftime("%Y-%m-%d"),
               "generated_at": today.isoformat(timespec="seconds"),
               "decisions": decisions},
              open(_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"게이트 기록: {_OUT} ({len(decisions)}건)", flush=True)


if __name__ == "__main__":
    main()
