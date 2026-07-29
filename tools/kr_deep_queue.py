#!/usr/bin/env python3
"""딥리서치 롤링 큐 — 상위 300 유니버스를 매주 상위 N개씩 순환 처리.

AI Berkshire 한국 파이프라인 ④단계 스케줄러. kr_deep_universe.json(상위 300종목,
去劣 통과분 우선 → 시총 상위순)을 큐로 삼아 매주 미처리 상위 N개를 배출한다.
12주(주당 25종목)에 걸쳐 300종목을 중복 없이 순차 분석하고, 모두 처리되면 한
사이클 완료 → reset 으로 다음 사이클(재스크리닝 후) 시작.

유니버스가 없으면 2차 去劣 통과분(kr_screen2_result.json)으로 폴백한다.

상태파일: data/kr_deep_queue.json  {"done":[...codes...], "cycle":n}

사용법:
    python3 tools/kr_deep_queue.py next --n 5      # 이번 배치(코드,이름,시장) 출력(JSON)
    python3 tools/kr_deep_queue.py mark 005930,000660,...   # 처리 완료 표시
    python3 tools/kr_deep_queue.py status
    python3 tools/kr_deep_queue.py reset           # 사이클 초기화(재스크리닝 후)

Python >= 3.8.
"""

import argparse
import datetime
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UNIVERSE = os.path.join(_ROOT, "data", "kr_deep_universe.json")
_SCREEN2 = os.path.join(_ROOT, "data", "kr_screen2_result.json")
_STATE = os.path.join(_ROOT, "data", "kr_deep_queue.json")
_LEDGER = os.path.join(_ROOT, "local", "trading", "ledger.sqlite3")
# 보유 종목 재분석 보장 주기(일). 신호 유효기간 90일 전에 넉넉히 갱신되도록
# 8주로 잡는다 — 이보다 오래된 보유 종목은 done 여부와 무관하게 재분석한다.
_HELD_REFRESH_DAYS = 56


def _pass_list():
    """큐 소스(우선순위 정렬 완료). 유니버스 우선, 없으면 2차 去劣 통과분 폴백."""
    if os.path.exists(_UNIVERSE):
        return list(json.load(open(_UNIVERSE, encoding="utf-8"))["universe"])
    if not os.path.exists(_SCREEN2):
        sys.stderr.write("❌ 유니버스·2차 결과 모두 없음. "
                         "kr_universe.py build 먼저.\n"); sys.exit(1)
    p = [r for r in json.load(open(_SCREEN2, encoding="utf-8"))["pass_list"]]
    p.sort(key=lambda r: r.get("score") or 0, reverse=True)
    return p


def _state():
    if os.path.exists(_STATE):
        return json.load(open(_STATE, encoding="utf-8"))
    return {"done": [], "cycle": 1}


def _save(st):
    os.makedirs(os.path.dirname(_STATE), exist_ok=True)
    json.dump(st, open(_STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def _held_first():
    """실전 보유 종목은 매 배치 최우선 재평가 대상.

    청산 트리거가 verdict 변경뿐이므로, 보유 종목이 재스크리닝에서
    탈락하거나 큐 후순위로 밀리면 90일 동결로 방치된다. 따라서
    (a) 유니버스 포함 여부와 무관하게 강제 포함하고, (b) 이번 사이클에
    이미 분석했더라도 _HELD_REFRESH_DAYS 초과면 다시 분석한다.
    """
    if not os.path.exists(_LEDGER):
        return []
    import sqlite3
    db = sqlite3.connect(f"file:{_LEDGER}?mode=ro", uri=True)
    try:
        entries = []
        for (code,) in db.execute(
            "SELECT code FROM strategy_positions WHERE quantity > 0 ORDER BY code"
        ).fetchall():
            row = db.execute(
                "SELECT payload, MAX(analyzed_at) FROM signals WHERE code=?", (code,)
            ).fetchone()
            name, market, age = code, "KOSPI", None
            if row and row[0]:
                payload = json.loads(row[0])
                name = payload.get("name", code)
                market = payload.get("market", "KOSPI")
                try:
                    analyzed = datetime.date.fromisoformat(str(row[1])[:10])
                    age = (datetime.date.today() - analyzed).days
                except (ValueError, TypeError):
                    age = None
            # 신선한 신호가 있으면 이번엔 쉰다 — 56일이 지나는 순간부터
            # done 여부와 무관하게 매 배치 재주입되므로 보장은 유지된다.
            if age is not None and age < _HELD_REFRESH_DAYS:
                continue
            entries.append({"code": code, "name": name, "market": market,
                            "score": None, "held": True})
        return entries
    finally:
        db.close()


def cmd_next(n):
    st = _state()
    done = set(st["done"])
    held = _held_first()
    held_codes = {h["code"] for h in held}
    rest = [{"code": r["code"], "name": r["name"], "market": r["market"],
             "score": r.get("score")}
            for r in _pass_list()
            if r["code"] not in done and r["code"] not in held_codes]
    print(json.dumps((held + rest)[:n], ensure_ascii=False, indent=2))


def cmd_mark(codes):
    st = _state()
    st["done"] = sorted(set(st["done"]) | set(c.strip() for c in codes.split(",")))
    _save(st)
    total = len(_pass_list())
    print(f"✅ 완료 표시: {len(st['done'])}/{total} (cycle {st['cycle']})")


def cmd_status():
    st = _state()
    total = len(_pass_list())
    done = len(st["done"])
    remaining = total - done
    backlog = max(0, st.get("target", done) - done)
    print(f"사이클 {st['cycle']} · 처리 {done}/{total} · 남음 {remaining} · 밀림(backlog) {backlog}")
    if remaining == 0:
        print("→ 한 사이클 완료. 재스크리닝 후 reset 하세요.")


def cmd_plan(base, cap):
    """이번 실행의 처리 개수 N을 밀림 보정하여 산출.

    누적 목표(target)에 **하루 1회만** base(기본 5)를 더한다(같은 날 중복 호출은
    증가 없음 — 헤드리스 세션이 plan을 또 불러도 target이 부풀지 않게 멱등화).
    아직 못 채운 만큼(target-done)을 cap(기본 10) 이내에서 처리한다. 어떤 날
    실패해 done이 안 늘면 backlog이 쌓여 다음 날 N이 커져(상한 cap) 따라잡는다.
    target은 상태파일에 누적 저장. reset(새 사이클) 시 0으로 초기화.
    """
    st = _state()
    done = len(st["done"])
    today = datetime.date.today().isoformat()
    if st.get("target_date") != today:          # 날짜당 1회만 증가
        st["target"] = st.get("target", done) + base
        st["target_date"] = today
        _save(st)
    n = min(cap, max(0, st.get("target", done) - done))
    print(n)


def _started(st):
    """사이클 시작일(YYYY-MM-DD). 없으면 유니버스 생성일 → 오늘 순으로 폴백."""
    s = st.get("started")
    if s:
        return s
    if os.path.exists(_UNIVERSE):
        try:
            return json.load(open(_UNIVERSE, encoding="utf-8")).get("built") \
                or datetime.date.today().isoformat()
        except Exception:
            pass
    return datetime.date.today().isoformat()


def cmd_label():
    """Notion 라우팅용 사이클 버킷 라벨. 예: '사이클 2 (2026-07~)'."""
    st = _state()
    print(f"사이클 {st.get('cycle', 1)} ({_started(st)[:7]}~)")


def cmd_reset():
    st = _state()
    _save({"done": [], "cycle": st.get("cycle", 1) + 1,
           "started": datetime.date.today().isoformat(),
           "target": 0, "target_date": None})
    print(f"✅ 큐 초기화 → 사이클 {st.get('cycle',1)+1}")


def main():
    ap = argparse.ArgumentParser(description="딥리서치 롤링 큐")
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("next"); n.add_argument("--n", type=int, default=5)
    m = sub.add_parser("mark"); m.add_argument("codes")
    pl = sub.add_parser("plan"); pl.add_argument("--base", type=int, default=5)
    pl.add_argument("--cap", type=int, default=10)
    sub.add_parser("status"); sub.add_parser("reset"); sub.add_parser("label")
    a = ap.parse_args()
    {"next": lambda: cmd_next(a.n), "mark": lambda: cmd_mark(a.codes),
     "plan": lambda: cmd_plan(a.base, a.cap),
     "status": cmd_status, "reset": cmd_reset, "label": cmd_label}[a.cmd]()


if __name__ == "__main__":
    main()
