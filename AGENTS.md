# Repository Guidelines

Contributor guide for **quant-trade**, a Python 3.13 quantitative trading research and backtesting framework for A-share markets.

## Project Structure & Module Organization

Source uses the `src` layout, with the package in `src/quant_trade/`:

- `services/` — **the single entry point for every research operation**; a params object plus a `RunContext` in, a structured result out
- `runtime/` — FastAPI app assembly; `__main__.py` starts the platform
- `data/` — market data ingestion (akshare/tushare adapters, DuckDB storage, trade calendar)
- `factors/` — factor computation (momentum/value/quality), preprocessing, IC analysis
- `strategies/` — strategy engine and signal generation
- `backtest/` — A-share backtest engine (T+1, price limits, fees, suspensions)
- `signals/` — weekly HTML report generation

There is no command-line interface. Research operations live in `services/` and are
invoked by the web UI or by scripts; the only CLI-shaped entry point is
`python -m quant_trade`, which starts the server and takes no subcommands.

Supporting locations: `config/default.yaml` holds runtime configuration, `tests/` holds the pytest suite, and `data/` + `reports/` are generated outputs (gitignored).

## Build, Test, and Development Commands

Everything runs through `uv`:

```bash
uv sync --extra dev                        # install dependencies, including dev tools
uv run python -m quant_trade               # start the platform on http://127.0.0.1:9555
uv run python -m quant_trade --reload      # dev: auto-restart on code change
(cd web && npm install && npm run build)   # build the frontend into web/dist
uv run pytest                              # run tests with coverage
uv run ruff check && uv run ruff format    # lint and auto-format
uv run mypy src                            # strict type-check
```

## Coding Style & Naming Conventions

- Format with ruff: double quotes, 4-space indentation, 120-character line limit.
- Lint rules: `E, W, F, I, B, C4, UP, SIM`; isort recognizes `quant_trade` as first-party.
- mypy runs in `strict` mode — annotate every function signature and return value.
- Use `snake_case` for modules and functions; name tests `test_*.py`. Register factors and strategies with their `@register("name")` decorator.

## Testing Guidelines

- pytest with pytest-cov; coverage is configured in `pyproject.toml` (`--cov=quant_trade`).
- Write one test file per module or area; name test functions `test_*` and add a short docstring.
- Run a single file with `uv run pytest tests/test_services_data.py` or filter with `-k`.

## Commit & Pull Request Guidelines

Git history is minimal (a single "Initial commit"), so adopt Conventional Commits going forward: `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`. Keep each commit focused, and explain the "why" in the body when it isn't obvious.

Pull requests should describe the change and how it was tested, link the related issue, and include screenshots for report or CLI output changes.

## Configuration & Environment

All configuration lives in `config/default.yaml`; override it with the `QUANT_CONFIG` environment variable. Data-source tokens (e.g., `TUSHARE_TOKEN`) belong in `.env` per `.env.example` — never commit real credentials.
