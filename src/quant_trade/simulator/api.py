"""FastAPI backend for simulator — JSON API only, no frontend."""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from quant_trade.config import DEFAULT_CONFIG_PATH, AppConfig
from quant_trade.data.store import DataStore
from quant_trade.simulator.comparison import ComparisonEngine
from quant_trade.simulator.engine import Simulator
from quant_trade.simulator.session import SessionStore
from quant_trade.simulator.types import OrderRequest


def create_app(config_path: str | None = None) -> FastAPI:
    app = FastAPI(title="Quant Trade Simulator API")

    # CORS — allow frontend from any origin during development
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    config = AppConfig.from_yaml(config_path or DEFAULT_CONFIG_PATH)
    store = DataStore(config.data.db_path)
    data_dir = str(Path(config.data.db_path).parent)
    sim = Simulator(store=store, db_path=config.data.db_path, data_dir=data_dir)

    # ---- Session CRUD ----

    @app.get("/api/sessions")
    def list_sessions(status: str | None = None) -> Any:
        rows = sim._sessions.list_sessions(status=status)
        for r in rows:
            for k in ("start_date", "end_date", "cursor_date", "created_at", "updated_at"):
                if r.get(k):
                    r[k] = str(r[k])
            # Don't send the full portfolio_json to the frontend
            r.pop("portfolio_json", None)
        return rows

    @app.post("/api/sessions")
    def create_session(
        name: str = Query("未命名会话"),
        start_date: str = Query("2023-06-01"),
        end_date: str | None = Query(None),
        capital: float = Query(100000),
        ref: str | None = Query(None),
    ) -> Any:
        logger.info(
            "Creating session: name={}, start={}, end={}, capital={}, ref={}",
            name,
            start_date,
            end_date or "latest",
            capital,
            ref or "none",
        )
        sd = date.fromisoformat(start_date)
        ed = date.fromisoformat(end_date) if end_date else None
        t0 = time.perf_counter()
        try:
            result = sim.create(name=name, start_date=sd, end_date=ed, initial_capital=capital, reference_strategy=ref)
        except Exception:
            logger.exception(
                "Session creation failed: name={}, start={}, end={}, capital={}, ref={}",
                name,
                start_date,
                end_date or "latest",
                capital,
                ref or "none",
            )
            raise
        elapsed_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            "Session created: id={}, total_weeks={}, elapsed={:.0f}ms",
            result["session_id"],
            result["total_weeks"],
            elapsed_ms,
        )
        return {
            "session_id": result["session_id"],
            "cursor_date": result["cursor_date"],
            "total_weeks": result["total_weeks"],
            "snapshot": _serialize_snapshot(result["snapshot"]),
        }

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str) -> Any:
        try:
            result = sim.resume(session_id)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return {
            "session_id": result["session_id"],
            "cursor_date": result["cursor_date"],
            "week_number": result["week_number"],
            "total_weeks": result["total_weeks"],
            "portfolio_value": result["portfolio_value"],
            "previous_decisions": result["previous_decisions"],
            "snapshot": _serialize_snapshot(result["snapshot"]),
        }

    @app.get("/api/sessions/{session_id}/status")
    def get_session_status(session_id: str) -> Any:
        try:
            return sim.status(session_id)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    # ---- Actions ----

    @app.post("/api/sessions/{session_id}/step")
    def step(session_id: str, body: dict[str, Any]) -> Any:
        orders = [
            OrderRequest(ts_code=o["ts_code"], target_pct=float(o["target_pct"]), direction=o["direction"])
            for o in body.get("orders", [])
        ]
        notes = body.get("notes", "")
        try:
            result = sim.step(session_id, orders, notes=notes)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {
            "cursor_advanced": result.cursor_advanced,
            "next_cursor_date": str(result.next_cursor_date),
            "portfolio_total_value": result.portfolio_total_value,
            "portfolio_cash": result.portfolio_cash,
            "holding_count": result.holding_count,
            "decision_number": result.decision.decision_number,
            "executed_orders": [
                {
                    "ts_code": o.ts_code,
                    "direction": o.direction,
                    "shares": o.shares,
                    "price": o.price,
                    "reason": o.reason,
                }
                for o in result.decision.executed_orders
            ],
            "warnings": result.warnings,
        }

    @app.post("/api/sessions/{session_id}/skip")
    def skip(session_id: str) -> Any:
        try:
            result = sim.skip(session_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {
            "cursor_advanced": result.cursor_advanced,
            "next_cursor_date": str(result.next_cursor_date),
            "portfolio_total_value": result.portfolio_total_value,
        }

    @app.get("/api/sessions/{session_id}/compare")
    def compare(session_id: str) -> Any:
        engine = ComparisonEngine(store, SessionStore(store.conn, data_dir))
        try:
            result = engine.compare(session_id, export_html=True)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        return {
            "weeks_completed": result.weeks_completed,
            "nav_manual": result.nav_manual,
            "nav_strategy": result.nav_strategy,
            "nav_benchmark": result.nav_benchmark,
            "metrics": result.metrics,
            "strategy_error": result.strategy_error,
            "weekly_diffs": [
                {
                    "week_number": d.week_number,
                    "cursor_date": str(d.cursor_date),
                    # None and an empty deviation say different things — "no
                    # recommendation this week" versus "followed it exactly".
                    "deviation": None
                    if d.deviation is None
                    else {
                        "followed": d.deviation.followed,
                        "dropped": d.deviation.dropped,
                        "added": d.deviation.added,
                    },
                    "concentration_warning": d.concentration_warning,
                    "drawdown_warning": d.drawdown_warning,
                }
                for d in result.weekly_diffs
            ],
            "html_path": result.html_path,
        }

    @app.delete("/api/sessions/{session_id}")
    def delete_session(session_id: str) -> Any:
        sim._sessions.delete(session_id)
        return {"deleted": session_id}

    return app


def _serialize_snapshot(snap: Any) -> dict[str, Any]:
    return {
        "signal_date": str(snap.signal_date),
        "exec_date": str(snap.exec_date),
        "week_number": snap.week_number,
        "total_weeks": snap.total_weeks,
        "total_value": snap.total_value,
        "cash": snap.cash,
        "market": {
            "benchmark_close": snap.market.benchmark_close,
            "benchmark_weekly_return": snap.market.benchmark_weekly_return,
        }
        if snap.market
        else None,
        "portfolio": [
            {
                "ts_code": p.ts_code,
                "shares": p.shares,
                "avg_cost": p.avg_cost,
                "current_price": p.current_price,
                "market_value": p.market_value,
                "pnl_pct": p.pnl_pct,
                "weight_pct": p.weight_pct,
            }
            for p in snap.portfolio
        ],
        "factor_ranking": [
            {"rank": f.rank, "ts_code": f.ts_code, "composite_score": f.composite_score}
            for f in snap.factor_ranking[:15]
        ],
        "strategy_signals": [
            {
                "ts_code": s.ts_code,
                "target_pct": s.target_pct,
                "direction": s.direction,
                "reason": s.reason,
            }
            for s in (snap.strategy_signals or [])
        ]
        if snap.strategy_signals is not None
        else None,
        "data_warnings": snap.data_warnings,
    }
