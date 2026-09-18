"""Platform runtime — assembles the FastAPI app that serves the research platform.

The simulator's API is mounted unchanged so it keeps working while the research
domains are migrated onto this app one at a time.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from quant_trade.api.backtests import create_backtests_router
from quant_trade.api.data import create_data_router
from quant_trade.api.factors import create_factors_router
from quant_trade.api.log_stream import create_log_stream_router
from quant_trade.api.models import create_models_router
from quant_trade.api.reports import create_reports_router
from quant_trade.api.runs import create_runs_router
from quant_trade.api.strategies import create_strategies_router
from quant_trade.config import DEFAULT_CONFIG_PATH, AppConfig
from quant_trade.jobs.runner import JobRunner
from quant_trade.runs.store import RunStore
from quant_trade.simulator.api import create_app as create_simulator_app

REPO_ROOT = Path(__file__).resolve().parents[3]
"""``src/quant_trade/runtime/app.py`` → repository root."""

WEB_DIST = REPO_ROOT / "web" / "dist"
"""Where ``npm run build`` puts the frontend bundle."""

_BUILD_HINT = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>Quant Trade</title></head>
<body style="font-family:system-ui;max-width:40rem;margin:4rem auto;line-height:1.6">
<h1>量化研究平台</h1>
<p>后端已启动，但前端尚未构建。</p>
<pre style="background:#f5f5f5;padding:1rem;border-radius:6px">cd web &amp;&amp; npm install &amp;&amp; npm run build</pre>
<p>开发模式下可改用 <code>npm run dev</code>（Vite 监听 9333 并代理 <code>/api</code>）。</p>
<p><a href="/api/health">/api/health</a></p>
</body></html>"""


def create_platform_app(config_path: str | None = None, dist: Path | None = None) -> FastAPI:
    """Build the platform app: health, the simulator API, and the frontend.

    ``dist`` exists so tests can point at a fixture directory instead of the
    real build output.
    """
    config = AppConfig.from_yaml(config_path or DEFAULT_CONFIG_PATH)
    dist_dir = dist if dist is not None else WEB_DIST

    # Built here rather than in the lifespan so an unusable database path fails
    # at startup instead of producing a half-working process. The worker only
    # starts in the lifespan, which TestClient triggers for fixture-built apps.
    run_store = RunStore(config.data.db_path)
    runner = JobRunner(config=config, runs=run_store)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        runner.start()
        try:
            yield
        finally:
            runner.stop()
            run_store.close()

    app = FastAPI(title="Quant Trade Research Platform", lifespan=lifespan)

    @app.get("/api/health")
    def health() -> JSONResponse:
        reachable, detail = _probe_database(config)
        return JSONResponse(
            {
                "status": "ok" if reachable else "degraded",
                "db_path": config.data.db_path,
                "db_reachable": reachable,
                "detail": detail,
            }
        )

    # include_router, not mount: these routes already carry the /api prefix, so
    # mounting under /api would double it. They must also come before the SPA
    # catch-all, which is registered last and matches every remaining path.
    app.include_router(create_simulator_app(config_path).router)
    app.include_router(create_data_router(config))
    app.include_router(create_factors_router(config))
    app.include_router(create_backtests_router(config))
    app.include_router(create_models_router(config))
    app.include_router(create_reports_router(config))
    app.include_router(create_strategies_router(config))
    app.include_router(create_runs_router(run_store, runner))
    app.include_router(create_log_stream_router(run_store))

    _mount_frontend(app, dist_dir)
    return app


def _probe_database(config: AppConfig) -> tuple[bool, str]:
    """Check the database opens and is queryable.

    The connection must use DuckDB's default configuration. Opening the same
    file read-only while another connection in this process holds it read-write
    raises ``ConnectionException: Can't open a connection to same database file
    with a different configuration``. Concurrency inside the process is instead
    handled by DuckDB's MVCC, which lets readers proceed while a writer holds
    its lock.
    """
    import duckdb

    try:
        conn = duckdb.connect(config.data.db_path)
    except Exception as e:
        return False, str(e)
    try:
        conn.execute("SELECT 1 FROM trade_calendar LIMIT 1").fetchone()
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()
    return True, ""


def _mount_frontend(app: FastAPI, dist_dir: Path) -> None:
    """Serve the built frontend, or explain how to build it."""
    if not (dist_dir / "index.html").is_file():

        @app.get("/", response_class=HTMLResponse)
        def build_hint() -> str:
            return _BUILD_HINT

        return

    index = dist_dir / "index.html"

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> Any:
        # Unknown /api paths must 404 as JSON, never fall through to the SPA.
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")

        candidate = (dist_dir / full_path).resolve()
        # Keep the resolved path inside dist — a `..` segment must not escape.
        if full_path and candidate.is_file() and candidate.is_relative_to(dist_dir.resolve()):
            return FileResponse(candidate)
        return FileResponse(index)
