from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config.settings import AppSettings
from app.services.state import BotState


class Persistence:
    def __init__(self, settings: AppSettings) -> None:
        self.db_path = Path(settings.persistence_db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        cursor = self._conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                payload TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS positions (
                symbol TEXT PRIMARY KEY,
                raw TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                symbol TEXT,
                action TEXT,
                status TEXT,
                side TEXT,
                qty REAL,
                filled_qty REAL,
                filled_avg_price REAL,
                notional REAL,
                reason TEXT,
                timestamp TEXT,
                raw TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS journal (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                reason TEXT NOT NULL,
                entry_price REAL,
                exit_price REAL,
                quantity REAL,
                notional REAL,
                realized_pnl REAL,
                drawdown REAL,
                raw TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS universe_cache (
                cache_key TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def _to_json(self, value: Any) -> str:
        return json.dumps(value, default=str)

    def _from_json(self, value: str) -> Any:
        return json.loads(value) if value else {}

    def close(self) -> None:
        self._conn.close()

    def load_state(self) -> dict[str, Any]:
        cursor = self._conn.cursor()
        cursor.execute("SELECT payload FROM bot_state WHERE id = 1")
        row = cursor.fetchone()
        if row is None:
            return {}
        return self._from_json(row["payload"])

    def save_state(self, state: BotState) -> None:
        payload = self._to_json(state.model_dump())
        cursor = self._conn.cursor()
        cursor.execute(
            "INSERT INTO bot_state (id, payload) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
            (payload,),
        )
        self._conn.commit()

    def load_universe_snapshot(self, cache_key: str = "default") -> dict[str, Any]:
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT payload FROM universe_cache WHERE cache_key = ?",
            (cache_key,),
        )
        row = cursor.fetchone()
        if row is None:
            return {}
        return self._from_json(row["payload"])

    def save_universe_snapshot(self, payload: dict[str, Any], cache_key: str = "default") -> None:
        cursor = self._conn.cursor()
        cursor.execute(
            "INSERT INTO universe_cache (cache_key, payload, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(cache_key) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at",
            (
                cache_key,
                self._to_json(payload),
                datetime.now().isoformat(),
            ),
        )
        self._conn.commit()

    def save_positions(self, positions: list[dict[str, Any]]) -> None:
        cursor = self._conn.cursor()
        for position in positions:
            cursor.execute(
                "INSERT INTO positions (symbol, raw, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(symbol) DO UPDATE SET raw=excluded.raw, updated_at=excluded.updated_at",
                (position.get("symbol", ""), self._to_json(position), datetime.now().isoformat()),
            )
        self._conn.commit()

    def save_order(self, symbol: str, order: dict[str, Any], action: str, reason: str) -> None:
        order_id = order.get("id") or order.get("client_order_id") or f"order-{datetime.now().timestamp()}"
        filled_avg_price = None
        try:
            filled_avg_price = float(order.get("filled_avg_price", 0))
        except (TypeError, ValueError):
            filled_avg_price = None
        filled_qty = None
        try:
            filled_qty = float(order.get("filled_qty", 0))
        except (TypeError, ValueError):
            filled_qty = None

        notional = None
        try:
            notional = float(order.get("notional", 0))
        except (TypeError, ValueError):
            notional = None

        cursor = self._conn.cursor()
        cursor.execute(
            "INSERT INTO orders (order_id, symbol, action, status, side, qty, filled_qty, filled_avg_price, notional, reason, timestamp, raw) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(order_id) DO UPDATE SET symbol=excluded.symbol, status=excluded.status, side=excluded.side, qty=excluded.qty, "
            "filled_qty=excluded.filled_qty, filled_avg_price=excluded.filled_avg_price, notional=excluded.notional, reason=excluded.reason, raw=excluded.raw",
            (
                order_id,
                symbol,
                action,
                order.get("status", "unknown"),
                order.get("side", "unknown"),
                float(order.get("qty", 0)) if order.get("qty") is not None else None,
                filled_qty,
                filled_avg_price,
                notional,
                reason,
                datetime.now().isoformat(),
                self._to_json(order),
            ),
        )
        self._conn.commit()

    def save_journal_entry(
        self,
        symbol: str,
        action: str,
        reason: str,
        entry_price: float | None,
        exit_price: float | None,
        quantity: float | None,
        notional: float | None,
        realized_pnl: float | None,
        drawdown: float | None,
        raw: dict[str, Any],
    ) -> None:
        cursor = self._conn.cursor()
        cursor.execute(
            "INSERT INTO journal (timestamp, symbol, action, reason, entry_price, exit_price, quantity, notional, realized_pnl, drawdown, raw) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now().isoformat(),
                symbol,
                action,
                reason,
                entry_price,
                exit_price,
                quantity,
                notional,
                realized_pnl,
                drawdown,
                self._to_json(raw),
            ),
        )
        self._conn.commit()

    def get_journal(self, limit: int = 50) -> list[dict[str, Any]]:
        cursor = self._conn.cursor()
        cursor.execute("SELECT * FROM journal ORDER BY id DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        entries: list[dict[str, Any]] = []
        for row in rows:
            raw = self._from_json(row["raw"])
            entries.append(
                {
                    "id": row["id"],
                    "timestamp": row["timestamp"],
                    "symbol": row["symbol"],
                    "action": row["action"],
                    "reason": row["reason"],
                    "entry_price": row["entry_price"],
                    "exit_price": row["exit_price"],
                    "quantity": row["quantity"],
                    "notional": row["notional"],
                    "realized_pnl": row["realized_pnl"],
                    "drawdown": row["drawdown"],
                    "raw": raw,
                }
            )
        return entries

    def get_metrics(self) -> dict[str, Any]:
        cursor = self._conn.cursor()
        cursor.execute("SELECT COUNT(*) as total_trades, SUM(realized_pnl) as cumulative_realized_pnl, "
                       "AVG(realized_pnl) as average_gain_loss, "
                       "SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins, "
                       "SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losses "
                       "FROM journal")
        row = cursor.fetchone()
        total = int(row["total_trades"] or 0)
        wins = int(row["wins"] or 0)
        losses = int(row["losses"] or 0)
        cumulative = float(row["cumulative_realized_pnl"] or 0.0)
        average = float(row["average_gain_loss"] or 0.0) if total else 0.0
        win_rate = (wins / total * 100.0) if total else 0.0
        return {
            "total_trades": total,
            "win_rate": win_rate,
            "average_gain_loss": average,
            "cumulative_realized_pnl": cumulative,
        }
