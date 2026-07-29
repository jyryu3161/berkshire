#!/usr/bin/env python3
"""Agent 리포트 1건 → Notion 발행 + 큐 완료표시. 주간 파이프라인 ⑤ 보조.

입력: 종목코드 + Agent가 반환한 마크다운(맨 끝 ```json 스코어 블록 포함) 파일.
동작: JSON 스코어 파싱 → 2차 스크린 재무(GM/OCF-NI/FCF) 병합 → report.json →
      notion_publish.add → kr_deep_queue.mark. reports/{종목명}/ 에도 저장.

사용법:
    python3 tools/kr_publish_one.py 042700 /path/agent_output.md
"""
import json, os, re, subprocess, sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    code, md_path = sys.argv[1], sys.argv[2]
    md = open(md_path, encoding="utf-8").read()

    # 맨 끝 ```json 블록 파싱
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", md, re.S)
    if not blocks:
        sys.stderr.write("❌ JSON 스코어 블록을 찾지 못함\n"); sys.exit(1)
    sc = json.loads(blocks[-1])
    body = md[:md.rfind("```json")].rstrip()  # json 블록 제외한 리포트 본문

    # 점수 척도 정규화 → 5점 만점 통일 (100점→/20, 10점→/2)
    def to5(v):
        if v is None:
            return None
        v = float(v)
        if v > 20:   # 100점 척도
            return round(v / 20, 2)
        if v > 5:    # 10점 척도
            return round(v / 2, 2)
        return round(v, 2)
    for k in ("score", "s_biz", "s_fin", "s_ind", "s_risk"):
        sc[k] = to5(sc.get(k))

    # 2차 스크린 재무 병합
    s2 = json.load(open(os.path.join(_ROOT, "data", "kr_screen2_result.json"), encoding="utf-8"))
    rec = next((r for r in s2["pass_list"] if r["code"] == code), {})

    report = {
        "name": rec.get("name", sc.get("name", code)), "code": code,
        "market": rec.get("market", "KOSPI"),
        "score": sc.get("score"), "verdict": sc.get("verdict", "보류"),
        "one_liner": sc.get("one_liner", ""),
        "s_biz": sc.get("s_biz"), "s_fin": sc.get("s_fin"),
        "s_ind": sc.get("s_ind"), "s_risk": sc.get("s_risk"),
        "gross_margin": rec.get("gross_margin"), "ocf_ni": rec.get("ocf_ni"),
        "fcf_eok": rec.get("fcf_sum_eok"), "date": sys.argv[3] if len(sys.argv) > 3 else None,
        "body_md": body,
    }

    # 실전 매매 sidecar — 불완전하면 None(경고만), 연구 발행은 계속된다.
    try:
        import kr_trade_signal
        cycle_path = os.path.join(_ROOT, "data", "kr_active_month.txt")
        cycle = (open(cycle_path, encoding="utf-8").read().strip()
                 if os.path.exists(cycle_path) else "kr-weekly")
        signal = kr_trade_signal.build_trade_signal(report, body, f"kr-{cycle}")
        if signal:
            report["trade_signal"] = signal
    except Exception as exc:
        sys.stderr.write(f"⚠️ trade_signal 생성 실패(발행은 계속): {exc}\n")
    name = report["name"]
    # 로컬 저장
    d = os.path.join(_ROOT, "reports", name)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, f"{name}-weekly.md"), "w", encoding="utf-8").write(body)
    rp = os.path.join(_ROOT, "data", f"_report_{code}.json")
    json.dump(report, open(rp, "w", encoding="utf-8"), ensure_ascii=False)

    # Notion 발행
    r = subprocess.run(["python3", os.path.join(_ROOT, "tools", "notion_publish.py"), "add", rp],
                       capture_output=True, text=True)
    sys.stdout.write(r.stdout); sys.stderr.write(r.stderr)
    if r.returncode != 0:
        sys.exit(1)
    # 큐 완료표시
    subprocess.run(["python3", os.path.join(_ROOT, "tools", "kr_deep_queue.py"), "mark", code])


if __name__ == "__main__":
    main()
