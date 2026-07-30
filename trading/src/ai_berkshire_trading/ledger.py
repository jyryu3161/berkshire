from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import sqlite3
from pathlib import Path

from .models import AnalysisSnapshot


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS signals (
 analysis_id TEXT PRIMARY KEY, code TEXT NOT NULL, analyzed_at TEXT NOT NULL,
 payload TEXT NOT NULL, UNIQUE(code, analyzed_at)
);
CREATE TABLE IF NOT EXISTS strategy_positions (
 code TEXT PRIMARY KEY, quantity INTEGER NOT NULL CHECK(quantity >= 0), updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
 run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, status TEXT NOT NULL, reason TEXT
);
CREATE TABLE IF NOT EXISTS order_intents (
 idempotency_key TEXT PRIMARY KEY, run_id TEXT NOT NULL, analysis_id TEXT NOT NULL,
 code TEXT NOT NULL, side TEXT NOT NULL, target_quantity INTEGER NOT NULL,
 requested_quantity INTEGER NOT NULL, limit_price INTEGER NOT NULL, status TEXT NOT NULL,
 created_at TEXT NOT NULL, FOREIGN KEY(run_id) REFERENCES runs(run_id)
);
CREATE TABLE IF NOT EXISTS broker_orders (
 attempt_id INTEGER PRIMARY KEY AUTOINCREMENT, idempotency_key TEXT NOT NULL,
 broker_order_ref TEXT NOT NULL UNIQUE, requested_quantity INTEGER NOT NULL,
 status TEXT NOT NULL,
 FOREIGN KEY(idempotency_key) REFERENCES order_intents(idempotency_key)
);
CREATE TABLE IF NOT EXISTS fills (
 fill_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL, quantity INTEGER NOT NULL,
 price INTEGER NOT NULL, filled_at TEXT NOT NULL,
 FOREIGN KEY(idempotency_key) REFERENCES order_intents(idempotency_key)
);
CREATE TABLE IF NOT EXISTS reconciliations (
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, code TEXT NOT NULL,
 account_quantity INTEGER NOT NULL, strategy_quantity INTEGER NOT NULL,
 checked_at TEXT NOT NULL, ok INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS trailing_peaks (
 code TEXT PRIMARY KEY, peak_price INTEGER NOT NULL CHECK(peak_price > 0),
 activated_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
"""


class Ledger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    @contextmanager
    def transaction(self):
        try:
            yield self.db
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def add_signal(self, signal: AnalysisSnapshot) -> bool:
        signal.validate()
        try:
            with self.transaction() as db:
                db.execute(
                    "INSERT INTO signals VALUES(?,?,?,?)",
                    (signal.analysis_id, signal.code, signal.analyzed_at.isoformat(), signal.canonical_json()),
                )
            return True
        except sqlite3.IntegrityError:
            row = self.db.execute(
                "SELECT code, analyzed_at, payload FROM signals WHERE analysis_id=?",
                (signal.analysis_id,),
            ).fetchone()
            if row and row["payload"] == signal.canonical_json():
                return False
            raise

    def latest_signal_json(self, code: str) -> str | None:
        row = self.db.execute(
            "SELECT payload FROM signals WHERE code=? ORDER BY analyzed_at DESC, analysis_id DESC LIMIT 1", (code,)
        ).fetchone()
        return row["payload"] if row else None

    def strategy_quantity(self, code: str) -> int:
        row = self.db.execute("SELECT quantity FROM strategy_positions WHERE code=?", (code,)).fetchone()
        return int(row["quantity"]) if row else 0

    def strategy_positions(self) -> dict[str, int]:
        return {
            str(row["code"]): int(row["quantity"])
            for row in self.db.execute(
                "SELECT code, quantity FROM strategy_positions WHERE quantity > 0"
            ).fetchall()
        }

    def reconcile(self, run_id: str, account_positions: dict[str, int]) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        ok = True
        with self.transaction() as db:
            for row in db.execute("SELECT code, quantity FROM strategy_positions").fetchall():
                enough = account_positions.get(row["code"], 0) >= row["quantity"]
                ok &= enough
                db.execute(
                    "INSERT INTO reconciliations(run_id,code,account_quantity,strategy_quantity,checked_at,ok) VALUES(?,?,?,?,?,?)",
                    (run_id, row["code"], account_positions.get(row["code"], 0), row["quantity"], now, int(enough)),
                )
        return ok

    def trailing_peak(self, code: str) -> int | None:
        row = self.db.execute(
            "SELECT peak_price FROM trailing_peaks WHERE code=?", (code,)
        ).fetchone()
        return int(row["peak_price"]) if row else None

    def raise_trailing_peak(self, code: str, price: int) -> None:
        """고점 기록 개시 또는 갱신 — 고점은 오르기만 한다."""
        now = datetime.now(timezone.utc).isoformat()
        with self.transaction() as db:
            db.execute(
                """INSERT INTO trailing_peaks VALUES(?,?,?,?)
                   ON CONFLICT(code) DO UPDATE SET
                     peak_price=excluded.peak_price, updated_at=excluded.updated_at
                   WHERE excluded.peak_price > trailing_peaks.peak_price""",
                (code, price, now, now),
            )

    def clear_trailing_peak(self, code: str) -> None:
        with self.transaction() as db:
            db.execute("DELETE FROM trailing_peaks WHERE code=?", (code,))

    @staticmethod
    def idempotency_key(analysis_id: str, code: str, target_quantity: int) -> str:
        return hashlib.sha256(f"{analysis_id}:{code}:{target_quantity}".encode()).hexdigest()

    def create_intent(self, *, run_id: str, analysis_id: str, code: str, side: str,
                      target_quantity: int, requested_quantity: int, limit_price: int) -> tuple[str, bool]:
        key = self.idempotency_key(analysis_id, code, target_quantity)
        with self.transaction() as db:
            cur = db.execute(
                "INSERT OR IGNORE INTO order_intents VALUES(?,?,?,?,?,?,?,?,?,?)",
                (key, run_id, analysis_id, code, side, target_quantity, requested_quantity,
                 limit_price, "PLANNED", datetime.now(timezone.utc).isoformat()),
            )
        return key, cur.rowcount == 1

    def filled_quantity(self, key: str) -> int:
        row = self.db.execute(
            "SELECT COALESCE(SUM(quantity), 0) AS quantity FROM fills WHERE idempotency_key=?",
            (key,),
        ).fetchone()
        return int(row["quantity"])

    def active_order(self, key: str) -> sqlite3.Row | None:
        return self.db.execute(
            """SELECT * FROM broker_orders WHERE idempotency_key=?
               AND status IN ('SUBMITTED','PARTIAL') ORDER BY attempt_id DESC LIMIT 1""",
            (key,),
        ).fetchone()

    def intent_recovery_info(self, key: str) -> dict | None:
        """Details a broker adapter needs to match an intent to a live order."""
        row = self.db.execute(
            "SELECT code, side, requested_quantity, limit_price FROM order_intents WHERE idempotency_key=?",
            (key,),
        ).fetchone()
        if not row:
            return None
        refs = [
            str(r["broker_order_ref"])
            for r in self.db.execute(
                "SELECT broker_order_ref FROM broker_orders WHERE idempotency_key=?",
                (key,),
            ).fetchall()
        ]
        return {
            "code": str(row["code"]), "side": str(row["side"]),
            "requested_quantity": int(row["requested_quantity"]),
            "limit_price": int(row["limit_price"]), "known_refs": refs,
        }

    def intent_status(self, key: str) -> str:
        row = self.db.execute(
            "SELECT status FROM order_intents WHERE idempotency_key=?", (key,)
        ).fetchone()
        if not row:
            raise KeyError(key)
        return str(row["status"])

    def mark_intent(self, key: str, status: str) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE order_intents SET status=? WHERE idempotency_key=?",
                (status, key),
            )

    def record_broker_order(self, key: str, broker_order_ref: str, requested_quantity: int) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO broker_orders(idempotency_key,broker_order_ref,requested_quantity,status) VALUES(?,?,?,'SUBMITTED')",
                (key, broker_order_ref, requested_quantity),
            )

    def mark_broker_order(self, broker_order_ref: str, status: str) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE broker_orders SET status=? WHERE broker_order_ref=?",
                (status, broker_order_ref),
            )

    def record_fill(self, fill_id: str, key: str, code: str, side: str, quantity: int, price: int, filled_at: str) -> bool:
        """Record a fill; `quantity` is cumulative per fill_id.

        Brokers that only report per-order totals may call this repeatedly with
        a growing cumulative quantity; only the increase is applied to the
        strategy position, so replays and late fills never double-count.
        """
        if quantity < 0:
            raise ValueError("fill quantity must not be negative")
        with self.transaction() as db:
            row = db.execute(
                "SELECT quantity FROM fills WHERE fill_id=?", (fill_id,)
            ).fetchone()
            if row:
                previous = int(row["quantity"])
                if quantity < previous:
                    raise ValueError("cumulative fill quantity cannot shrink")
                delta = quantity - previous
                if not delta:
                    return False
                db.execute(
                    "UPDATE fills SET quantity=?, price=?, filled_at=? WHERE fill_id=?",
                    (quantity, price, filled_at, fill_id),
                )
            else:
                delta = quantity
                if not delta:
                    return False
                db.execute(
                    "INSERT INTO fills VALUES(?,?,?,?,?)",
                    (fill_id, key, quantity, price, filled_at),
                )
            signed = delta if side == "BUY" else -delta
            existing = db.execute(
                "SELECT quantity FROM strategy_positions WHERE code=?", (code,)
            ).fetchone()
            if existing:
                updated = int(existing["quantity"]) + signed
                if updated < 0:
                    raise ValueError("fill would sell more than strategy-owned quantity")
                db.execute(
                    "UPDATE strategy_positions SET quantity=?, updated_at=? WHERE code=?",
                    (updated, filled_at, code),
                )
                if updated == 0:
                    # 전량 청산 — 고점 기록을 지워 다음 재진입이 오염되지 않게 한다.
                    db.execute("DELETE FROM trailing_peaks WHERE code=?", (code,))
            else:
                if signed < 0:
                    raise ValueError("sell fill has no strategy-owned position")
                db.execute(
                    "INSERT INTO strategy_positions VALUES(?,?,?)",
                    (code, signed, filled_at),
                )
        return True
