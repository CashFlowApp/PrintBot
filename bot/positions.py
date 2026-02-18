"""SQLite persistence: positions and events. Survives restarts."""
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from loguru import logger

DB_PATH = Path(__file__).resolve().parent.parent / "polymarket_bot.db"

SCHEMA_POSITIONS = """
CREATE TABLE IF NOT EXISTS positions (
    market_id TEXT PRIMARY KEY,
    yes_token TEXT,
    no_token TEXT,
    yes_filled REAL DEFAULT 0,
    no_filled REAL DEFAULT 0,
    cost_basis_yes REAL DEFAULT 0,
    cost_basis_no REAL DEFAULT 0,
    entry_time TEXT,
    status TEXT DEFAULT 'OPEN'
);
"""

SCHEMA_EVENTS = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    type TEXT,
    payload TEXT
);
"""


def _utc_ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class PositionStore:
    """Thread-safe SQLite access for positions and events."""

    def __init__(self, path: Optional[Path] = None):
        self._path = path or DB_PATH
        self._lock = threading.Lock()
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, check_same_thread=False)

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._conn()
            try:
                schema = SCHEMA_POSITIONS + SCHEMA_EVENTS
                logger.debug("Executing schema: {}", schema)
                conn.executescript(schema)
                conn.commit()
            finally:
                conn.close()

    def log_event(self, type_: str, payload: Any) -> None:
        import json
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT INTO events (ts, type, payload) VALUES (?, ?, ?)",
                    (_utc_ts(), type_, json.dumps(payload) if not isinstance(payload, str) else payload),
                )
                conn.commit()
            finally:
                conn.close()

    def open_position(
        self,
        market_id: str,
        yes_token: str,
        no_token: str,
        yes_filled: float,
        no_filled: float,
        cost_basis_yes: float,
        cost_basis_no: float,
    ) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    """INSERT INTO positions (market_id, yes_token, no_token, yes_filled, no_filled,
                       cost_basis_yes, cost_basis_no, entry_time, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        market_id,
                        yes_token,
                        no_token,
                        yes_filled,
                        no_filled,
                        cost_basis_yes,
                        cost_basis_no,
                        _utc_ts(),
                        "OPEN",
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def update_position_status(self, market_id: str, status: str) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute("UPDATE positions SET status = ? WHERE market_id = ? AND status = 'OPEN'", (status, market_id))
                conn.commit()
            finally:
                conn.close()

    def update_fills(self, market_id: str, yes_filled: float, no_filled: float, cost_basis_yes: float, cost_basis_no: float) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    """UPDATE positions SET yes_filled = ?, no_filled = ?, cost_basis_yes = ?, cost_basis_no = ?
                       WHERE market_id = ? AND status = 'OPEN'""",
                    (yes_filled, no_filled, cost_basis_yes, cost_basis_no, market_id),
                )
                conn.commit()
            finally:
                conn.close()

    def get_open_positions(self) -> list[dict]:
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    """SELECT market_id, yes_token, no_token, yes_filled, no_filled,
                       cost_basis_yes, cost_basis_no, entry_time
                       FROM positions WHERE status = 'OPEN'"""
                )
                rows = cur.fetchall()
                return [
                    {
                        "market_id": r[0],
                        "yes_token": r[1],
                        "no_token": r[2],
                        "yes_filled": r[3],
                        "no_filled": r[4],
                        "cost_basis_yes": r[5],
                        "cost_basis_no": r[6],
                        "entry_time": r[7],
                    }
                    for r in rows
                ]
            finally:
                conn.close()

    def count_open_markets(self) -> int:
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute("SELECT COUNT(DISTINCT market_id) FROM positions WHERE status = 'OPEN'")
                return cur.fetchone()[0] or 0
            finally:
                conn.close()
