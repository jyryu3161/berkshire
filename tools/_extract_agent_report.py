#!/usr/bin/env python3
"""Agent task .output(JSONL) 에서 최종 리포트 텍스트만 추출 → 클린 .md 저장.
컨텍스트 오염 없이(전체를 출력하지 않고) 발행 파이프라인에 넘기기 위한 보조.
사용: python3 _extract_agent_report.py <task.output> <out.md>
"""
import html, json, re, sys

src, out = sys.argv[1], sys.argv[2]
cands = []

def walk(o):
    if isinstance(o, str):
        if "```json" in o and ("한줄결론" in o or "스코어카드" in o or "one_liner" in o):
            cands.append(o)
    elif isinstance(o, dict):
        for v in o.values():
            walk(v)
    elif isinstance(o, list):
        for v in o:
            walk(v)

for line in open(src, encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    try:
        walk(json.loads(line))
    except Exception:
        continue

if not cands:
    sys.stderr.write("❌ 리포트 텍스트를 찾지 못함\n"); sys.exit(1)
text = max(cands, key=len)
text = html.unescape(text)  # &gt; &lt; &amp; 복원
open(out, "w", encoding="utf-8").write(text)
print(f"✅ 추출 {len(text)}자 → {out}")
