# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Quick Start

```bash
uv sync --extra dev          # Install all deps including dev tools
quant-trade weekly           # Full pipeline: sync data → compute factors → run strategy → HTML report
```

## Build & Run

```bash
uv sync --extra dev          # Install all deps including dev tools
uv run quant-trade           # Run CLI entry point
uv run python -m quant_trade # Equivalent: run as module

# CLI Commands
quant-trade data sync [--include-financials]    # Sync market data to DuckDB
quant-trade data status                         # Show database status
quant-trade factor update                       # Compute all enabled factors
quant-trade factor list                         # List registered factors
quant-trade factor ic                           # Factor IC summary
quant-trade factor alpha158 [--start DATE]      # Compute & persist 158 Alpha158 factors
quant-trade strategy run                        # Generate trading signals
quant-trade strategy list                       # List registered strategies
quant-trade backtest run [--start DATE] [--end DATE]  # Run backtest
quant-trade model train [--start DATE] [--end DATE] [--output PATH]  # Walk-forward LightGBM
quant-trade model predict [--date DATE]         # Model top picks for a date
quant-trade weekly                              # Full pipeline → HTML report
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
uv run pytest tests/test_cli.py         # Run single test file
uv run pytest -k "test_main_runs"       # Run tests matching pattern
```

pytest is configured in `pyproject.toml` with `pythonpath = ["src"]` — imports use `quant_trade.*` directly, no `src.` prefix.

## Architecture

**Package layout — `src` layout with flat namespace:**
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
akshare/tushare → DuckDB (daily_kline, financials, stock_basic, trade_calendar, index_weights)
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
         Factor.compute()   DataStore        TradeCalendar
              │               │
              ▼               ▼
         Strategy.generate_signals(date, universe, data)
              │
              ▼
         Backtest Engine (weekly loop with A-share rules)
              │
              ▼
         Weekly HTML Report
```

**Key design decisions:**
- DuckDB as sole storage (columnar + SQL + DataFrame, single-file, zero ops)
- Self-built backtest engine (not vectorbt) — weekly loop handles A-share rules naturally
- Factor registry pattern — `@register("name")` decorator, strategies reference factors by name
- Backtest = paper trading — same `generate_signals()` code path for both

## Key Patterns

- Python 3.13+ required, managed via `.python-version` (uv picks this up).
- mypy runs in strict mode on `src/` only, not `tests/`.
- ruff isort configured with `known-first-party = ["quant_trade"]` — third-party imports sort before first-party.
- Coverage omits `__init__.py` files. Run tests to see coverage report inline.
