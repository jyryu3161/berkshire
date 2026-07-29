#!/usr/bin/env python3
"""리포트 → trade_signal sidecar 생성 (주간 파이프라인 ⑤ 발행 직전 호출).

trading/ 실행 경계가 요구하는 구조화 신호를 리포트에서 기계적으로 뽑는다.
- verdict: 매수|보류|관망|제외 → BUY|HOLD|WATCH|EXCLUDE (그 외 값이면 신호 생략)
- targets: 캐노니컬 '목표주가 교차검증' 3밴드 표(보수/중립/공격)를 파싱
- sector: 네이버 integration API의 업종명 (BUY 필수)
불완전하면 None을 반환하고 경고만 남긴다 — 연구 발행은 신호 없이도 계속된다.
"""
import datetime
import hashlib
import json
import re
import sys

_KST = datetime.timezone(datetime.timedelta(hours=9))
_VERDICT = {"매수": "BUY", "보류": "HOLD", "관망": "WATCH", "제외": "EXCLUDE"}
_BAND_ROW = r"^\|\s*{band}(?:\s*\([^)]*\))?\s*\|\s*\**\s*([\d,]+)\s*원"
_SECTION = "## 목표주가 교차검증"


def parse_target_bands(body_md: str) -> dict | None:
    """보수/중립/공격 밴드 표에서 bear/base/bull 정수 원가격을 뽑는다.

    전략 표 등 다른 표에도 같은 밴드명이 등장하므로, 캐노니컬
    '목표주가 교차검증' 섹션이 있으면 그 안에서만 찾고, 가격 셀이
    숫자+원 형식인 행만 인정한다.
    """
    scope = body_md
    if _SECTION in body_md:
        tail = body_md.split(_SECTION, 1)[1]
        next_heading = re.search(r"^## ", tail, re.M)
        scope = tail[: next_heading.start()] if next_heading else tail
    values = {}
    for band, key in (("보수", "bear"), ("중립", "base"), ("공격", "bull")):
        rows = re.findall(_BAND_ROW.format(band=band), scope, re.M)
        if not rows:
            return None
        values[key] = int(rows[-1].replace(",", ""))
    if not 0 < values["bear"] < values["base"] < values["bull"]:
        return None
    return values


def fetch_sector(code: str) -> str | None:
    """업종 그룹 키. 포트폴리오 업종 한도는 문자열 동일성만 쓰므로
    네이버 industryCode('업종278' 형태)로 충분하다. 구 스키마의
    industryName(dict)이 오면 그대로 쓴다."""
    try:
        import krx_data
        d = krx_data._curl_json(
            f"https://m.stock.naver.com/api/stock/{code}/integration",
            f"https://m.stock.naver.com/domestic/stock/{code}/total",
        )
        ind = d.get("industryCompareInfo")
        if isinstance(ind, dict) and ind.get("industryName"):
            return str(ind["industryName"]).strip() or None
        industry_code = str(d.get("industryCode") or "").strip()
        if industry_code:
            return f"업종{industry_code}"
    except Exception as exc:
        sys.stderr.write(f"⚠️ 업종 조회 실패({code}): {exc}\n")
    return None


def build_trade_signal(report: dict, body_md: str, cycle_id: str) -> dict | None:
    verdict = _VERDICT.get(str(report.get("verdict", "")).strip())
    if not verdict:
        sys.stderr.write(
            f"⚠️ verdict '{report.get('verdict')}' 미표준 → trade_signal 생략\n"
        )
        return None
    code = str(report.get("code", "")).strip()
    if not re.fullmatch(r"\d{6}", code):
        sys.stderr.write("⚠️ 6자리 종목코드 아님 → trade_signal 생략\n")
        return None
    targets = parse_target_bands(body_md)
    sector = fetch_sector(code)
    if verdict == "BUY" and not targets:
        sys.stderr.write("⚠️ 매수 판정이나 목표주가 밴드 파싱 실패 → trade_signal 생략\n")
        return None
    if verdict == "BUY" and not sector:
        sys.stderr.write("⚠️ 매수 판정이나 업종 확인 실패 → trade_signal 생략\n")
        return None
    now = datetime.datetime.now(_KST).replace(microsecond=0)
    return {
        "schema_version": "1",
        "analysis_id": f"kr-weekly-{code}-{now:%Y%m%d}",
        "cycle_id": cycle_id,
        "code": code,
        "name": str(report.get("name", code)),
        "market": str(report.get("market", "KOSPI")),
        "sector": sector,
        "analyzed_at": now.isoformat(),
        "verdict": verdict,
        "targets_krw": targets if verdict == "BUY" else None,
        "source_hash": hashlib.sha256(body_md.encode("utf-8")).hexdigest(),
        "audit_status": "PASS",
    }


if __name__ == "__main__":
    report_path, md_path = sys.argv[1], sys.argv[2]
    report = json.load(open(report_path, encoding="utf-8"))
    body = open(md_path, encoding="utf-8").read()
    signal = build_trade_signal(report, body, "manual")
    print(json.dumps(signal, ensure_ascii=False, indent=2))
