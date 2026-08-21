"""Session persistence — DuckDB metadata + JSON decision log."""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import duckdb
from loguru import logger

from quant_trade.simulator.types import Decision


class SessionStore:
    """CRUD for simulator sessions. Metadata in DuckDB, decisions in JSON files."""

    def __init__(self, conn: duckdb.DuckDBPyConnection, data_dir: str = "data") -> None:
        self._conn = conn
        self._data_dir = Path(data_dir)
        self._sim_dir = self._data_dir / "simulator"

    def _session_dir(self, session_id: str) -> Path:
        return self._sim_dir / session_id

    def _decisions_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "decisions.json"

    # ---- CRUD ----

    def create(
        self,
        name: str,
        start_date: date,
        end_date: date | None,
        initial_capital: float,
        cursor_date: date,
        portfolio_json: str,
        reference_strategy: str | None = None,
    ) -> str:
        """Insert a new session row and return the generated ID."""
        session_id = str(uuid.uuid4())
        session_dir = self._session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("Session directory created: {}", session_dir)

        decisions_path = self._decisions_path(session_id)
        decisions_path.write_text("[]", encoding="utf-8")
        logger.debug("Decisions file initialized: {}", decisions_path)

        now_utc = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        self._conn.execute(
            """
            INSERT INTO simulator_session
                (id, name, start_date, end_date, initial_capital, cursor_date,
                 portfolio_json, reference_strategy, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            [
                session_id,
                name,
                start_date,
                end_date,
                initial_capital,
                cursor_date,
                portfolio_json,
                reference_strategy,
                now_utc,
                now_utc,
            ],
        )
        logger.debug("DB row inserted for session {}", session_id)
        logger.info(f"Session {session_id} created: {name}")
        return session_id

    def save(self, session_id: str, cursor_date: date, portfolio_json: str, status: str = "active") -> None:
        """Update cursor_date, portfolio_json, and status."""
        now_utc = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        self._conn.execute(
            """
            UPDATE simulator_session
            SET cursor_date = ?, portfolio_json = ?, status = ?, updated_at = ?
            WHERE id = ?
            """,
            [cursor_date, portfolio_json, status, now_utc, session_id],
        )

    def load(self, session_id: str) -> dict[str, Any] | None:
        """Load session metadata row as a dict. Returns None if not found."""
        try:
            df = self._conn.execute("SELECT * FROM simulator_session WHERE id = ?", [session_id]).df()
        except Exception as e:
            logger.warning(f"load_session query failed: {e}")
            return None
        if df.empty:
            return None
        return {str(k): v for k, v in df.iloc[0].to_dict().items()}

    def list_sessions(self, status: str | None = None) -> list[dict[str, Any]]:
        """List all sessions, optionally filtered by status."""
        try:
            if status:
                df = self._conn.execute(
                    "SELECT id, name, start_date, end_date, initial_capital, cursor_date, "
                    "reference_strategy, status, created_at, updated_at "
                    "FROM simulator_session WHERE status = ? ORDER BY updated_at DESC",
                    [status],
                ).df()
            else:
                df = self._conn.execute(
                    "SELECT id, name, start_date, end_date, initial_capital, cursor_date, "
                    "reference_strategy, status, created_at, updated_at "
                    "FROM simulator_session ORDER BY updated_at DESC"
                ).df()
            return cast(list[dict[str, Any]], df.to_dict(orient="records")) if not df.empty else []
        except Exception as e:
            logger.warning(f"list_sessions query failed: {e}")
            return []

    def delete(self, session_id: str) -> bool:
        """Delete session row and its data directory. Returns True if a row was deleted."""
        self._conn.execute("DELETE FROM simulator_session WHERE id = ?", [session_id])
        session_dir = self._session_dir(session_id)
        if session_dir.exists():
            shutil.rmtree(session_dir, ignore_errors=True)
            logger.info(f"Deleted session data: {session_dir}")
        return True

    # ---- Decision log ----

    def append_decision(self, session_id: str, decision: Decision) -> None:
        """Append a decision to the session's decisions.json file."""
        path = self._decisions_path(session_id)
        try:
            existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        except (json.JSONDecodeError, FileNotFoundError):
            existing = []
        existing.append(_decision_to_dict(decision))
        path.write_text(json.dumps(existing, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    def load_decisions(self, session_id: str) -> list[Decision]:
        """Load all decisions for a session from JSON."""
        path = self._decisions_path(session_id)
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return [_dict_to_decision(item) for item in raw]
        except (json.JSONDecodeError, FileNotFoundError):
            return []


# ---- Serialization helpers ----


def _decision_to_dict(d: Decision) -> dict[str, Any]:
    return {
        "decision_number": d.decision_number,
        "cursor_date": str(d.cursor_date),
        "exec_date": str(d.exec_date),
        "user_orders": [
            {"ts_code": o.ts_code, "target_pct": o.target_pct, "direction": o.direction} for o in d.user_orders
        ],
        "executed_orders": [
            {
                "ts_code": o.ts_code,
                "direction": o.direction,
                "target_pct": o.target_pct,
                "shares": o.shares,
                "price": o.price,
                "cost_or_proceeds": o.cost_or_proceeds,
                "reason": o.reason,
            }
            for o in d.executed_orders
        ],
        "notes": d.notes,
        "strategy_orders": [
            {"ts_code": s.ts_code, "target_pct": s.target_pct, "direction": s.direction, "reason": s.reason}
            for s in d.strategy_orders
        ]
        if d.strategy_orders
        else None,
        "timestamp": d.timestamp,
    }


def _dict_to_decision(d: dict[str, Any]) -> Decision:
    from quant_trade.simulator.types import ExecutedOrder, OrderRequest, StrategySignalItem

    user_orders = [
        OrderRequest(ts_code=o["ts_code"], target_pct=o["target_pct"], direction=o["direction"])
        for o in d.get("user_orders", [])
    ]
    executed_orders = [
        ExecutedOrder(
            ts_code=o["ts_code"],
            direction=o["direction"],
            target_pct=o["target_pct"],
            shares=o["shares"],
            price=o["price"],
            cost_or_proceeds=o["cost_or_proceeds"],
            reason=o.get("reason", ""),
        )
        for o in d.get("executed_orders", [])
    ]
    strategy_orders = None
    if d.get("strategy_orders"):
        strategy_orders = [
            StrategySignalItem(
                ts_code=s["ts_code"],
                target_pct=s["target_pct"],
                direction=s["direction"],
                reason=s["reason"],
            )
            for s in d["strategy_orders"]
        ]
    return Decision(
        decision_number=d["decision_number"],
        cursor_date=date.fromisoformat(d["cursor_date"]),
        exec_date=date.fromisoformat(d["exec_date"]),
        user_orders=user_orders,
        executed_orders=executed_orders,
        notes=d.get("notes", ""),
        snapshot_before=None,  # Not deserialized from JSON to keep payload small
        strategy_orders=strategy_orders,
        timestamp=d.get("timestamp", ""),
    )
