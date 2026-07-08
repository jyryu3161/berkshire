#!/usr/bin/env python3
"""딥리서치 유니버스 빌더 — 去劣 통과분 + 시총 상위순으로 상위 N(기본 300) 구성.

AI Berkshire 한국 파이프라인 ③.5단계. 기존엔 2차 去劣 통과분(약 53종목)만 순환
분석했으나, 커버리지를 넓히기 위해 '상위 300종목'을 하나의 유니버스로 묶어 12주에
걸쳐 중복 없이 순차 분석한다.

우선순위(= 큐 배출 순서):
  1) 2차 去劣 통과분(우량주) — score 내림차순
  2) 나머지는 召回池(시총 상위순)에서 채움 — 이미 든 코드 제외, N까지

즉 '우량주 먼저, 나머지는 시총 상위순'. 비-통과 종목은 비교 가능한 품질점수가
없으므로 시총 순위를 '상위' 기준으로 삼는다(召回池는 시총 내림차순).

상태파일: data/kr_deep_universe.json
  {"n": 300, "built": "YYYY-MM-DD", "universe": [{code,name,market,score,source}...]}
  source: "pass2"(去劣 통과) | "fill"(시총 상위 채움)

사용법:
    python3 tools/kr_universe.py build --n 300
    python3 tools/kr_universe.py show

Python >= 3.8, 외부 의존성 없음.
"""

import argparse
import datetime
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RECALL = os.path.join(_ROOT, "data", "kr_recall_pool.json")
_SCREEN2 = os.path.join(_ROOT, "data", "kr_screen2_result.json")
_OUT = os.path.join(_ROOT, "data", "kr_deep_universe.json")


def _load(path, what):
    if not os.path.exists(path):
        sys.stderr.write(f"❌ {what} 없음: {path}\n")
        sys.exit(1)
    return json.load(open(path, encoding="utf-8"))


def cmd_build(n):
    recall = _load(_RECALL, "召回池")["pool"]           # 시총 내림차순
    passed = _load(_SCREEN2, "2차 去劣 결과")["pass_list"]

    # 1) 去劣 통과분 — score 내림차순
    passed = sorted(passed, key=lambda r: r.get("score") or 0, reverse=True)
    universe, seen = [], set()
    for r in passed:
        if r["code"] in seen:
            continue
        seen.add(r["code"])
        universe.append({"code": r["code"], "name": r["name"],
                         "market": r["market"], "score": r.get("score"),
                         "source": "pass2"})
        if len(universe) >= n:
            break

    # 2) 시총 상위순으로 N까지 채움
    for r in recall:
        if len(universe) >= n:
            break
        if r["code"] in seen:
            continue
        seen.add(r["code"])
        universe.append({"code": r["code"], "name": r["name"],
                         "market": r["market"], "score": None,
                         "source": "fill"})

    out = {"n": len(universe),
           "built": datetime.date.today().isoformat(),
           "pass2_count": sum(1 for u in universe if u["source"] == "pass2"),
           "fill_count": sum(1 for u in universe if u["source"] == "fill"),
           "universe": universe}
    os.makedirs(os.path.dirname(_OUT), exist_ok=True)
    json.dump(out, open(_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"✅ 유니버스 {out['n']}종목 생성 "
          f"(去劣통과 {out['pass2_count']} + 시총채움 {out['fill_count']}) → {_OUT}")


def cmd_show():
    d = _load(_OUT, "유니버스")
    print(f"유니버스 {d['n']}종목 (去劣 {d['pass2_count']} + 채움 {d['fill_count']}) "
          f"· 생성 {d['built']}")
    for i, u in enumerate(d["universe"][:10], 1):
        print(f"  {i:>3} {u['code']} {u['name']} [{u['market']}] "
              f"{u['source']} score={u['score']}")
    if d["n"] > 10:
        print(f"  ... (+{d['n']-10})")


def main():
    ap = argparse.ArgumentParser(description="딥리서치 유니버스 빌더(상위 N)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("--n", type=int, default=300)
    sub.add_parser("show")
    a = ap.parse_args()
    {"build": lambda: cmd_build(a.n), "show": cmd_show}[a.cmd]()


if __name__ == "__main__":
    main()
