"""Command-line interface for quant-trade."""

import contextlib
import sys
import webbrowser
from datetime import date, timedelta

from loguru import logger

from quant_trade.backtest.engine import run_backtest
from quant_trade.config import DEFAULT_CONFIG_PATH, AppConfig
from quant_trade.data.index_weights import get_default_universe, sync_index_weights
from quant_trade.data.sources.akshare_adapter import AkshareAdapter
from quant_trade.data.store import DataStore
from quant_trade.data.sync import sync_all, sync_daily_kline, sync_financials
from quant_trade.factors.registry import registry as factor_registry
from quant_trade.signals.reporter import generate_weekly_report, save_report
from quant_trade.strategies.registry import strategy_registry


def main() -> None:
    """Entry point: dispatch to subcommands."""
    args = sys.argv[1:] if len(sys.argv) > 1 else ["help"]

    if not args:
        _usage()
        return

    cmd = args[0]
    config = AppConfig.from_yaml(DEFAULT_CONFIG_PATH)

    if cmd == "data" and len(args) >= 2:
        _cmd_data(args[1], config)
    elif cmd == "factor" and len(args) >= 2:
        _cmd_factor(args[1], config)
    elif cmd == "strategy" and len(args) >= 2:
        _cmd_strategy(args[1], config)
    elif cmd == "backtest" and len(args) >= 2:
        _cmd_backtest(args[1], config, args[2:])
    elif cmd == "weekly":
        _cmd_weekly(config)
    elif cmd == "help" or cmd == "--help" or cmd == "-h":
        _usage()
    else:
        print(f"Unknown command: {cmd}")
        _usage()


def _cmd_data(sub: str, config: AppConfig) -> None:
    """Handle 'data' subcommands."""
    store = DataStore(config.data.db_path)

    if sub == "sync":
        include_fin = "--include-financials" in sys.argv
        adapter = AkshareAdapter()
        logger.info("Step 1/4: Syncing stock basic & trade calendar...")
        results = sync_all(store, [], primary=config.data.primary_source, include_financials=False)
        logger.info("Step 2/4: Syncing index weights...")
        sync_index_weights(store, get_default_universe(), date.today(), adapter)
        logger.info("Step 3/4: Getting universe codes...")
        codes = store.get_universe(get_default_universe(), date.today())
        if not codes:
            logger.warning("No universe codes found. Using sample of all stocks.")
            df = adapter.fetch_stock_basic()
            codes = df["ts_code"].tolist()[:200] if not df.empty else []
        logger.info(f"Step 4/4: Syncing daily kline for {len(codes)} stocks...")
        from quant_trade.data.sync import get_backup_sources

        rows = sync_daily_kline(store, adapter, codes, date(2015, 1, 1), date.today())

        # If primary missed any stocks, fill gaps with backup
        synced_count = _count_synced_stocks(store)
        missing = [c for c in codes if c not in synced_count]
        if missing:
            for backup in get_backup_sources(store, config.data.backup_sources):
                logger.info(f"Filling {len(missing)} missing stocks via {backup.source_name}")
                try:
                    extra = sync_daily_kline(store, backup, missing, date(2015, 1, 1), date.today())
                except Exception as e:
                    logger.warning(f"Backup {backup.source_name} failed: {e}")
                    continue
                rows += extra
                missing = [c for c in missing if c not in _count_synced_stocks(store)]
                if not missing:
                    break

        results["daily_kline"] = rows
        if include_fin:
            results["financials"] = sync_financials(store, adapter, codes)
        print("Sync results:", results)

    elif sub == "status":
        latest = store.get_latest_trade_date()
        print(f"DB path: {config.data.db_path}")
        print(f"Latest trade date: {latest}")
        try:
            row_count = store.conn.execute("SELECT COUNT(*) FROM daily_kline").fetchone()
            if row_count is not None:
                print(f"Daily kline rows: {row_count[0]}")
            else:
                print("Daily kline: no data")
        except Exception:
            print("Daily kline: no data")

    else:
        print(f"Unknown data command: {sub}")
        print("Available: data sync, data status")


def _count_synced_stocks(store: DataStore) -> set[str]:
    """Return set of stock codes that have at least one row in daily_kline."""
    try:
        df = store.conn.execute("SELECT DISTINCT ts_code FROM daily_kline").df()
        return set(df["ts_code"].tolist())
    except Exception:
        return set()


def _cmd_factor(sub: str, config: AppConfig) -> None:
    """Handle 'factor' subcommands."""
    store = DataStore(config.data.db_path)
    latest = store.get_latest_trade_date()
    if latest is None:
        logger.error("No data in database. Run 'quant-trade data sync' first.")
        return

    universe = store.get_universe(get_default_universe(), latest)

    if sub == "update":
        logger.info(f"Computing factors for {latest} on {len(universe)} stocks...")
        results: dict[str, int] = {}
        for fname in config.factor.enabled:
            factor = factor_registry.get(fname)
            if factor is None:
                logger.warning(f"Factor '{fname}' not found")
                continue
            try:
                vals = factor.compute(latest, universe)
                results[fname] = len(vals)
                logger.info(f"  {fname}: {len(vals)} values")
            except Exception as e:
                logger.error(f"  {fname}: failed - {e}")
                results[fname] = 0
        print("Factor update results:", results)

    elif sub == "list":
        print("Registered factors:")
        for name in factor_registry.list_all():
            f = factor_registry.get(name)
            if f:
                print(f"  {name} [{f.category.value}]")

    elif sub == "ic":
        from quant_trade.factors.analysis import compute_ic_series

        dates = [latest]  # single date for quick check

        for fname in config.factor.enabled:
            factor = factor_registry.get(fname)
            if factor is None:
                continue
            ic_result = compute_ic_series(
                store,
                fname,
                lambda d, u, factor=factor: factor.compute(d, u),  # type: ignore[misc]
                universe,
                dates,
            )
            print(
                f"  {fname}: IC mean={ic_result.get('ic_mean', 'N/A'):.4f}"
                if isinstance(ic_result.get("ic_mean"), float)
                else f"  {fname}: IC mean=N/A"
            )
    else:
        print(f"Unknown factor command: {sub}")
        print("Available: factor update, factor list, factor ic")


def _cmd_strategy(sub: str, config: AppConfig) -> None:
    """Handle 'strategy' subcommands."""
    store = DataStore(config.data.db_path)
    latest = store.get_latest_trade_date()
    if latest is None:
        logger.error("No data. Run 'quant-trade data sync' first.")
        return

    universe = store.get_universe(get_default_universe(), latest)

    if sub == "run":
        strategy = strategy_registry.get(config.strategy.name)
        if strategy is None:
            logger.error(f"Strategy '{config.strategy.name}' not found")
            return

        # Inject config parameters
        if hasattr(strategy, "top_n"):
            strategy.top_n = config.strategy.top_n
        if hasattr(strategy, "factor_weights"):
            strategy.factor_weights = config.strategy.factor_weights
        if hasattr(strategy, "max_industry_weight"):
            strategy.max_industry_weight = config.strategy.max_industry_weight

        signals = strategy.generate_signals(latest, universe, store)
        print(f"\nSignal date: {latest}")
        print(f"Universe: {len(universe)} stocks")
        print(f"\nOrders ({len(signals.orders)}):")
        for o in signals.orders:
            tag = "🟢 BUY " if o.direction == "BUY" else "🔴 SELL"
            print(f"  {tag} {o.ts_code}  {o.target_pct:.1%}  ({o.reason})")
        if not signals.orders:
            print("  No signals generated.")

    elif sub == "list":
        print("Registered strategies:")
        for name in strategy_registry.list_all():
            print(f"  {name}")

    else:
        print(f"Unknown strategy command: {sub}")
        print("Available: strategy run, strategy list")


def _cmd_backtest(sub: str, config: AppConfig, extra: list[str]) -> None:
    """Handle 'backtest' subcommands."""
    from datetime import date as dt

    store = DataStore(config.data.db_path)

    if sub == "run":
        # Parse optional --start and --end from extra args
        start = config.backtest.start_date
        end = dt.today()
        for i, arg in enumerate(extra):
            if arg == "--start" and i + 1 < len(extra):
                start = dt.fromisoformat(extra[i + 1])
            elif arg == "--end" and i + 1 < len(extra):
                end = dt.fromisoformat(extra[i + 1])

        strategy = strategy_registry.get(config.strategy.name)
        if strategy is None:
            logger.error(f"Strategy '{config.strategy.name}' not found")
            return

        # Inject config
        if hasattr(strategy, "top_n"):
            strategy.top_n = config.strategy.top_n
        if hasattr(strategy, "factor_weights"):
            strategy.factor_weights = config.strategy.factor_weights

        logger.info(f"Running backtest {start} → {end}...")
        result = run_backtest(
            strategy=strategy,
            start=start,
            end=end,
            initial_capital=config.backtest.initial_capital,
            commission_rate=config.backtest.commission_rate,
            min_commission=config.backtest.min_commission,
            stamp_duty_rate=config.backtest.stamp_duty_rate,
            transfer_fee_rate=config.backtest.transfer_fee_rate,
            benchmark_code=config.backtest.benchmark,
            store=store,
        )

        metrics = result["metrics"]
        print("\n=== Backtest Results ===")
        print(f"Period: {start} → {end}")
        print(f"Total Return:    {metrics.get('total_return', 0):.2%}")
        print(f"Annual Return:   {metrics.get('annual_return', 0):.2%}")
        print(f"Annual Vol:      {metrics.get('annual_volatility', 0):.2%}")
        print(f"Sharpe Ratio:    {metrics.get('sharpe_ratio', 0):.2f}")
        print(f"Max Drawdown:    {metrics.get('max_drawdown', 0):.2%}")
        print(f"Calmar Ratio:    {metrics.get('calmar_ratio', 0):.2f}")
        print(f"Win Rate:        {metrics.get('win_rate', 0):.2%}")
        print(f"Excess Return:   {metrics.get('excess_return', 0):.2%}")

    else:
        print(f"Unknown backtest command: {sub}")
        print("Available: backtest run [--start YYYY-MM-DD] [--end YYYY-MM-DD]")


def _cmd_weekly(config: AppConfig) -> None:
    """Run the full weekly pipeline: sync → factors → strategy → report."""
    logger.info("=== Weekly Pipeline ===")

    # 1. Data sync
    store = DataStore(config.data.db_path)
    adapter = AkshareAdapter()
    logger.info("Step 1/4: Syncing stock basic & trade calendar...")
    sync_all(store, [], primary=config.data.primary_source, include_financials=False)

    # Index weights must be synced before the universe can be computed —
    # otherwise the universe is empty on a fresh database.
    logger.info("Syncing index weights...")
    sync_index_weights(store, get_default_universe(), date.today(), adapter)

    logger.info("Getting universe codes...")
    codes = store.get_universe(get_default_universe(), date.today())
    if not codes:
        logger.warning("No universe codes found. Using sample of all stocks.")
        df = adapter.fetch_stock_basic()
        codes = df["ts_code"].tolist()[:100] if not df.empty else []

    logger.info(f"Syncing daily kline for {len(codes)} stocks...")
    try:
        row = store.conn.execute("SELECT MAX(trade_date) FROM daily_kline").fetchone()
        latest_kline = row[0] if row else None
    except Exception:
        latest_kline = None
    # Fresh installs bootstrap full history; otherwise incremental (last 5 days)
    kline_start = (latest_kline - timedelta(days=5)) if latest_kline else date(2015, 1, 1)
    sync_all(store, codes, start=kline_start, primary=config.data.primary_source)

    # 2. Factors
    latest = store.get_latest_trade_date()
    if latest is None:
        logger.error("No data available. Aborting.")
        return
    universe = store.get_universe(get_default_universe(), latest)

    # 3. Strategy
    logger.info("Step 2/4: Running strategy...")
    strategy = strategy_registry.get(config.strategy.name)
    if strategy is None:
        logger.error(f"Strategy '{config.strategy.name}' not found")
        return
    if hasattr(strategy, "top_n"):
        strategy.top_n = config.strategy.top_n
    signals = strategy.generate_signals(latest, universe, store)

    # 4. Run a quick backtest for the NAV chart context
    logger.info("Step 3/4: Computing NAV history...")
    result = run_backtest(
        strategy=strategy,
        start=config.backtest.start_date,
        end=latest,
        initial_capital=config.backtest.initial_capital,
        commission_rate=config.backtest.commission_rate,
        min_commission=config.backtest.min_commission,
        stamp_duty_rate=config.backtest.stamp_duty_rate,
        transfer_fee_rate=config.backtest.transfer_fee_rate,
        benchmark_code=config.backtest.benchmark,
        store=store,
    )

    # Convert signals to dict for template
    signal_dicts = [
        {
            "ts_code": o.ts_code,
            "name": o.ts_code,  # TODO: lookup name
            "target_pct": o.target_pct,
            "direction": o.direction,
            "reason": o.reason,
        }
        for o in signals.orders
    ]

    # 5. Generate report
    logger.info("Step 4/4: Generating report...")
    portfolio = result.get("portfolio")
    html = generate_weekly_report(result, signal_dicts, config, portfolio=portfolio)
    path = save_report(html, config)

    logger.info(f"Report saved: {path}")
    print(f"\nWeekly report generated: {path}")

    # Try to open in browser
    with contextlib.suppress(Exception):
        webbrowser.open(str(path.resolve()))


def _usage() -> None:
    """Print usage information."""
    print("quant-trade — A-Share Quantitative Research Platform")
    print()
    print("Usage: quant-trade <command> [options]")
    print()
    print("Commands:")
    print("  data sync [--include-financials]   Sync market data to DuckDB")
    print("  data status                        Show database status")
    print("  factor update                      Compute all enabled factors")
    print("  factor list                        List registered factors")
    print("  factor ic                          Show factor IC summary")
    print("  strategy run                       Generate trading signals")
    print("  strategy list                      List registered strategies")
    print("  backtest run [--start YYYY-MM-DD] [--end YYYY-MM-DD]")
    print("                                     Run backtest")
    print("  weekly                             Full pipeline → HTML report")
    print("  help                               Show this help")
    print()
    print(f"Config: {DEFAULT_CONFIG_PATH}")
