# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Quick Start

```bash
uv sync --extra dev          # Install all deps including dev tools
uv run python -m quant_trade # Start the research platform on http://127.0.0.1:9555
```

The platform binds loopback only — there is no authentication, so exposing it
on the network has to be an explicit `--host 0.0.0.0`.

## Build & Run

```bash
uv sync --extra dev          # Install all deps including dev tools
uv run python -m quant_trade # Start the platform (API + built frontend)

# Options — the entry point takes no subcommands
uv run python -m quant_trade --port 9000
uv run python -m quant_trade --reload          # dev: auto-restart on code change
uv run python -m quant_trade --host 0.0.0.0    # opt in to network exposure

# Frontend
cd web && npm install && npm run build   # production bundle -> web/dist
cd web && npm run dev                    # dev server on 9333, proxies /api to 9555
```

Research operations are **not** command-line subcommands. They are functions in
`quant_trade.services`, invoked by the web UI (or directly from a script):

```python
from quant_trade.services import RunContext, data_status, sync_market_data
from quant_trade.services.data import DataStatusParams, DataSyncParams

ctx = RunContext(run_id="manual", config=cfg, store=store)
sync_market_data(DataSyncParams(include_financials=True), ctx)
```

## Lint, Format, Type-Check

```bash
uv run ruff check            # Lint (rules: E, W, F, I, B, C4, UP, SIM)
uv run ruff format           # Format (double-quote, 120-char line)
uv run mypy src              # Strict type-check (Python 3.13)
```

## Tests

```bash
uv run pytest                           # Run all tests (verbose, with coverage)
uv run pytest tests/test_services_data.py  # Run single test file
uv run pytest -k "test_service_chain"     # Run tests matching pattern
```

pytest is configured in `pyproject.toml` with `pythonpath = ["src"]` — imports use `quant_trade.*` directly, no `src.` prefix.

## Architecture

**Package layout — `src` layout with flat namespace:**
- `quant_trade.services` — **the entry point for every research operation** (data/factors/strategies/models/backtest/report/queries). Callers construct a params object plus a `RunContext`, call one function, and get a structured result back. Callers never implement domain logic.
- `quant_trade.runtime` — FastAPI app assembly; `__main__.py` starts it
- `quant_trade.data` — market data ingestion (akshare/tushare adapters, DuckDB storage, trade calendar)
- `quant_trade.factors` — factor computation framework (momentum/value/quality), preprocessing, IC analysis
- `quant_trade.factors.alpha158` — vectorized Alpha158 library (158 qlib factors, polars operators, DuckDB persistence)
- `quant_trade.models` — ML pipeline (feature matrix, T+2 labels, LightGBM walk-forward, RankIC evaluation)
- `quant_trade.strategies` — strategy engine (base class, factor ranking, model ranking, signal generation)
- `quant_trade.backtest` — A-share backtest engine (T+1, price limits, fees, suspension handling)
- `quant_trade.signals` — weekly HTML report generation (Jinja2 + matplotlib)
- `quant_trade.utils` — shared helpers

**Data flow:**
```
         HTTP API  /  scripts / notebooks   ← both are thin adapters
                        │
                        ▼
              quant_trade.services           ← the only place domain logic is invoked
                fn(params, ctx) -> Result
                        │
   ┌────────────┬───────┼────────┬────────────┐
   ▼            ▼       ▼        ▼            ▼
 data sync   factors  strategy  backtest   report
   │            │       │        │            │
   └────────────┴───────┴────────┴────────────┘
                        │
                        ▼
   DuckDB (daily_kline, financials, stock_basic, trade_calendar,
           index_weights, factor_values, simulator_session)
```

**Key design decisions:**
- DuckDB as sole storage (columnar + SQL + DataFrame, single-file, zero ops)
- Service layer is the single domain entry point — a params object plus a `RunContext` in, a structured result out. No caller re-implements parameter parsing or domain logic.
- `RunContext` carries `run_id` / `config` / `store` plus `progress()` / `log()` / `cancelled()`; services report progress and poll for cancellation at loop boundaries. `NULL_CONTEXT` is the no-op default for tests and scripts.
- Service params are pydantic models, so a run is reproducible from `model_dump_json()` alone
- Self-built backtest engine (not vectorbt) — weekly loop handles A-share rules naturally
- Factor registry pattern — `@register("name")` decorator, strategies reference factors by name
- Backtest = paper trading — same `generate_signals()` code path for both

**DuckDB concurrency:** multiple connections in one process may read while another
writes (MVCC). They must all use the *same* configuration — opening a file
read-only while another connection holds it read-write in the same process
raises `ConnectionException`.

## Key Patterns

- Python 3.13+ required, managed via `.python-version` (uv picks this up).
- mypy runs in strict mode on `src/` only, not `tests/`.
- ruff isort configured with `known-first-party = ["quant_trade"]` — third-party imports sort before first-party.
- Coverage omits `__init__.py` files. Run tests to see coverage report inline.
