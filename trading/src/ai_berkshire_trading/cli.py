from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .broker import AuthenticationExpired
from .config import LiveConfig, StrategyConfig
from .execution import ExecutionEngine, SafetyHalt
from .kis import KISBroker
from .ledger import Ledger
from .models import AnalysisSnapshot, Verdict
from .notion import NotionExecutionLogger, NotionSignalSink
from .runtime_log import log_event, runtime_logger


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _local(name: str) -> str:
    return str(_repo_root() / "local" / "trading" / name)


def _configure(args) -> None:
    path = Path(args.strategy_config)
    raw = json.loads(path.read_text(encoding="utf-8"))
    updates = {
        "capital_cap_krw": args.capital_cap_krw,
        "max_equity_weight": args.max_equity_weight,
        "max_single_name_weight": args.max_single_name_weight,
        "max_sector_weight": args.max_sector_weight,
        "rebalance_deadband": args.rebalance_deadband,
        "daily_turnover_limit": args.daily_turnover_limit,
        "trailing_stop_pct": args.trailing_stop_pct,
        "watch_entry_min_score": args.watch_entry_min_score,
        "watch_entry_weight": args.watch_entry_weight,
        "max_positions": args.max_positions,
        "max_signal_age_days": args.max_signal_age_days,
        "min_order_krw": args.min_order_krw,
    }
    for key, value in updates.items():
        if value is not None:
            raw[key] = value
    if args.capital_cap_krw is not None:
        raw["capital_mode"] = "FIXED_CAP"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.chmod(temporary, 0o600)
    StrategyConfig.load(temporary)
    temporary.replace(path)
    os.chmod(path, 0o600)
    log_event(
        runtime_logger(args.runtime_log), "STRATEGY_CONFIG_CHANGED",
        capital_mode=raw["capital_mode"],
        capital_cap_krw=raw.get("capital_cap_krw"),
        max_equity_weight=raw["max_equity_weight"],
        max_single_name_weight=raw["max_single_name_weight"],
        max_sector_weight=raw["max_sector_weight"],
        daily_turnover_limit=raw["daily_turnover_limit"],
    )
    print(json.dumps(raw, ensure_ascii=False, indent=2))


def _read_notion_token(path: str) -> str:
    token = Path(path).expanduser().read_text(encoding="utf-8").strip()
    if not token:
        raise RuntimeError(f"Notion token file {path} is empty")
    return token


def _ingest_outbox(ledger: Ledger, sink: NotionSignalSink, signals_dir: Path,
                   runtime) -> None:
    """outbox의 신호를 원장에 수집하고 파일을 아카이브한다.

    수집(신규·중복 모두)된 파일은 processed/로, 형식 불량은 rejected/로
    이동한다 — 매 실행이 무한히 쌓인 디렉토리를 재스캔하거나 같은 불량
    파일 경고를 반복하지 않도록.
    """
    for path in sorted(signals_dir.glob("*.json")):
        try:
            snapshot = AnalysisSnapshot.from_dict(
                json.loads(path.read_text(encoding="utf-8"))
            )
            inserted = ledger.add_signal(snapshot)
        except Exception as exc:
            log_event(runtime, "SIGNAL_REJECTED", path=path.name, error=str(exc))
            rejected = signals_dir / "rejected"
            rejected.mkdir(parents=True, exist_ok=True)
            path.replace(rejected / path.name)
            continue
        if inserted:
            try:
                sink.append(snapshot)
            except Exception as exc:
                log_event(runtime, "NOTION_SIGNAL_APPEND_FAILED",
                          analysis_id=snapshot.analysis_id, error=str(exc))
        processed = signals_dir / "processed"
        processed.mkdir(parents=True, exist_ok=True)
        path.replace(processed / path.name)


def _load_signals(ledger: Ledger) -> list[AnalysisSnapshot]:
    codes = {
        str(row["code"])
        for row in ledger.db.execute("SELECT DISTINCT code FROM signals").fetchall()
    } | set(ledger.strategy_positions())
    signals = []
    for code in sorted(codes):
        payload = ledger.latest_signal_json(code)
        if not payload:
            continue
        snapshot = AnalysisSnapshot.from_dict(json.loads(payload))
        if snapshot.verdict is Verdict.BUY or ledger.strategy_quantity(code) > 0:
            signals.append(snapshot)
    return signals


def _run(args) -> int:
    runtime = runtime_logger(args.runtime_log)
    live = LiveConfig.load(args.config)
    strategy = StrategyConfig.load(args.strategy_config)
    if (live.capital_mode, live.capital_cap_krw) != (
        strategy.capital_mode, strategy.capital_cap_krw
    ):
        raise RuntimeError(
            "capital settings differ between live config and strategy config; "
            "refusing split-brain capital basis"
        )
    ledger = Ledger(args.db)
    notion_token = _read_notion_token(live.notion_token_path)
    sink = NotionSignalSink(notion_token, live.trading_signals_database_id)
    signals_dir = Path(args.signals_dir)
    if signals_dir.is_dir():
        _ingest_outbox(ledger, sink, signals_dir, runtime)
    signals = _load_signals(ledger)
    broker = KISBroker(live, runtime, ledger.intent_recovery_info)
    logger = NotionExecutionLogger(
        notion_token, live.execution_log_database_id, runtime
    )
    engine = ExecutionEngine(broker, ledger, logger, strategy,
                             entry_gate_path=args.entry_gate)
    run_id = engine.run(signals)
    status = ledger.db.execute(
        "SELECT status FROM runs WHERE run_id=?", (run_id,)
    ).fetchone()["status"]
    print(f"run {run_id}: {status} ({len(signals)} signals)")
    return 0 if status in ("COMPLETED", "AWAITING_SELL_COMPLETION", "PARTIAL") else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate-signal")
    validate.add_argument("path")
    ingest = sub.add_parser("ingest")
    ingest.add_argument("path")
    ingest.add_argument("--db", default=_local("ledger.sqlite3"))
    show = sub.add_parser("show-strategy")
    show.add_argument("--strategy-config", default=_local("strategy.json"))
    configure = sub.add_parser("configure")
    configure.add_argument("--strategy-config", default=_local("strategy.json"))
    configure.add_argument("--runtime-log", default=_local("runtime.jsonl"))
    configure.add_argument("--capital-cap-krw", type=int)
    configure.add_argument("--max-equity-weight", type=float)
    configure.add_argument("--max-single-name-weight", type=float)
    configure.add_argument("--max-sector-weight", type=float)
    configure.add_argument("--rebalance-deadband", type=float)
    configure.add_argument("--daily-turnover-limit", type=float)
    configure.add_argument("--trailing-stop-pct", type=float)
    configure.add_argument("--watch-entry-min-score", type=float)
    configure.add_argument("--watch-entry-weight", type=float)
    configure.add_argument("--max-positions", type=int)
    configure.add_argument("--max-signal-age-days", type=int)
    configure.add_argument("--min-order-krw", type=int)
    run = sub.add_parser("run")
    run.add_argument(
        "--config",
        default=str(Path.home() / ".config" / "ai-berkshire" / "trading.json"),
    )
    run.add_argument("--strategy-config", default=_local("strategy.json"))
    run.add_argument("--db", default=_local("ledger.sqlite3"))
    run.add_argument("--runtime-log", default=_local("runtime.jsonl"))
    run.add_argument("--signals-dir", default=_local("signals"))
    run.add_argument("--entry-gate", default=_local("entry_gate.json"))
    args = parser.parse_args()
    if args.command == "show-strategy":
        StrategyConfig.load(args.strategy_config)
        print(Path(args.strategy_config).read_text(encoding="utf-8"), end="")
        return
    if args.command == "configure":
        _configure(args)
        return
    if args.command == "run":
        try:
            raise SystemExit(_run(args))
        except SafetyHalt as exc:
            print(f"safety halt: {exc}", file=sys.stderr)
            raise SystemExit(3)
        except AuthenticationExpired as exc:
            print(f"authentication expired: {exc}", file=sys.stderr)
            raise SystemExit(4)
        except (RuntimeError, PermissionError, OSError, ValueError) as exc:
            print(f"run refused: {exc}", file=sys.stderr)
            raise SystemExit(2)
    signal = AnalysisSnapshot.from_dict(json.loads(Path(args.path).read_text(encoding="utf-8")))
    if args.command == "validate-signal":
        print(f"valid: {signal.analysis_id} {signal.code}")
    else:
        inserted = Ledger(args.db).add_signal(signal)
        print("inserted" if inserted else "duplicate")
