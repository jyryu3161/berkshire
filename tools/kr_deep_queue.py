#!/usr/bin/env python3
"""딥리서치 롤링 큐 — 2차 통과분을 매주 상위 N개씩 순환 처리.

AI Berkshire 한국 파이프라인 ④단계 스케줄러. kr_screen2_result.json(통과분,
점수순)을 큐로 삼아 매주 미처리 상위 N개를 배출한다. 모두 처리되면 한 사이클
완료 → reset 으로 다음 사이클(월 1회 재스크리닝 후) 시작.

상태파일: data/kr_deep_queue.json  {"done":[...codes...], "cycle":n}

사용법:
    python3 tools/kr_deep_queue.py next --n 13     # 이번 주 배치(코드,이름,시장) 출력(JSON)
    python3 tools/kr_deep_queue.py mark 005930,000660,...   # 처리 완료 표시
    python3 tools/kr_deep_queue.py status
    python3 tools/kr_deep_queue.py reset           # 사이클 초기화(재스크리닝 후)

Python >= 3.8.
"""

import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCREEN2 = os.path.join(_ROOT, "data", "kr_screen2_result.json")
_STATE = os.path.join(_ROOT, "data", "kr_deep_queue.json")


def _pass_list():
    if not os.path.exists(_SCREEN2):
        sys.stderr.write("❌ 2차 결과 없음. kr_quality_screen2.py run 먼저.\n"); sys.exit(1)
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


def cmd_next(n):
    st = _state()
    done = set(st["done"])
    batch = [{"code": r["code"], "name": r["name"], "market": r["market"],
              "score": r.get("score")}
             for r in _pass_list() if r["code"] not in done][:n]
    print(json.dumps(batch, ensure_ascii=False, indent=2))


def cmd_mark(codes):
    st = _state()
    st["done"] = sorted(set(st["done"]) | set(c.strip() for c in codes.split(",")))
    _save(st)
    total = len(_pass_list())
    print(f"✅ 완료 표시: {len(st['done'])}/{total} (cycle {st['cycle']})")


def cmd_status():
    st = _state()
    total = len(_pass_list())
    remaining = total - len(st["done"])
    print(f"사이클 {st['cycle']} · 처리 {len(st['done'])}/{total} · 남음 {remaining}")
    if remaining == 0:
        print("→ 한 사이클 완료. 재스크리닝 후 reset 하세요.")


def cmd_reset():
    st = _state()
    _save({"done": [], "cycle": st.get("cycle", 1) + 1})
    print(f"✅ 큐 초기화 → 사이클 {st.get('cycle',1)+1}")


def main():
    ap = argparse.ArgumentParser(description="딥리서치 롤링 큐")
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("next"); n.add_argument("--n", type=int, default=13)
    m = sub.add_parser("mark"); m.add_argument("codes")
    sub.add_parser("status"); sub.add_parser("reset")
    a = ap.parse_args()
    {"next": lambda: cmd_next(a.n), "mark": lambda: cmd_mark(a.codes),
     "status": cmd_status, "reset": cmd_reset}[a.cmd]()


if __name__ == "__main__":
    main()
