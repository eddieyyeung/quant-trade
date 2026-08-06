"""Weekly HTML report generator."""

import base64
import io
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape
from loguru import logger

from quant_trade.backtest.portfolio import Portfolio
from quant_trade.config import AppConfig

matplotlib.use("Agg")  # Non-interactive backend


# Default template embedded for portability (overridden by filesystem if available)
DEFAULT_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>量化周报 {{ report_date }}</title></head>
<body><h1>{{ report_date }} 量化周报</h1></body></html>
"""


def _get_env() -> Environment:
    """Get Jinja2 environment, preferring filesystem templates, falling back to embedded."""
    template_dir = Path(__file__).parent.parent / "templates"
    if template_dir.exists() and list(template_dir.glob("*.j2")):
        return Environment(
            loader=FileSystemLoader(str(template_dir)),
            autoescape=select_autoescape(["html", "xml"]),
        )
    return Environment(loader=None)


def _render_nav_chart(
    nav: pd.Series | None,
    benchmark: pd.Series | None = None,
) -> str:
    """Render NAV vs benchmark chart to base64 PNG string."""
    if nav is None or nav.empty:
        return ""

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(nav.index, np.asarray(nav.values, dtype=float), label="策略", color="#3b82f6", linewidth=1.5)

    if benchmark is not None and not benchmark.empty:
        # Align benchmark dates
        common = nav.index.intersection(benchmark.index)
        if len(common) > 0:
            bm_aligned = benchmark.loc[common]
            # Normalize benchmark to same starting point as strategy
            bm_normalized = bm_aligned / bm_aligned.iloc[0] if bm_aligned.iloc[0] != 0 else bm_aligned
            ax.plot(common, np.asarray(bm_normalized.values, dtype=float), label="沪深300", color="#9ca3af", linewidth=1, linestyle="--")

    ax.axhline(y=1.0, color="#e5e7eb", linewidth=0.5)
    ax.set_ylabel("累计净值")
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100)
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode()


def _format_metrics(result: dict[str, Any], portfolio: Portfolio | None = None) -> dict[str, Any]:
    """Format backtest metrics for template rendering."""
    m = result.get("metrics", {})
    current_value: float = 100_000.0
    if portfolio is not None:
        current_value = portfolio.total_value

    nav = result.get("nav_series")
    weekly_return = 0.0
    if nav is not None and len(nav) >= 6:
        weekly_return = (nav.iloc[-1] / nav.iloc[-6] - 1) if nav.iloc[-6] != 0 else 0.0

    return {
        "current_value": current_value,
        "total_return": m.get("total_return", 0.0),
        "sharpe_ratio": m.get("sharpe_ratio"),
        "max_drawdown": m.get("max_drawdown"),
        "weekly_return": weekly_return,
        "benchmark_return": m.get("benchmark_return", 0.0),
        "excess_return": m.get("excess_return", 0.0),
        "win_rate": m.get("win_rate", 0.0),
    }


def generate_weekly_report(
    result: dict[str, Any],
    signals: list[dict[str, Any]],
    config: AppConfig,
    portfolio: Portfolio | None = None,
    factor_ic_data: list[dict[str, Any]] | None = None,
) -> str:
    """
    Generate a complete HTML weekly report.

    Args:
        result: Backtest result dict from run_backtest().
        signals: List of signal dicts with ts_code, target_pct, direction, reason.
        config: AppConfig instance.
        portfolio: Optional Portfolio for holdings display.
        factor_ic_data: Optional factor IC tracking data.

    Returns:
        HTML string.
    """
    env = _get_env()
    try:
        template = env.get_template(config.report.template)
    except Exception:
        template = env.from_string(DEFAULT_TEMPLATE)

    today = date.today()

    # Determine if today is a Friday (or near it)
    is_friday = today.weekday() == 4

    # Next rebalance date
    days_until_friday = (4 - today.weekday()) % 7
    next_friday = today + timedelta(days=days_until_friday if days_until_friday > 0 else 7)

    # Render NAV chart
    nav_chart = _render_nav_chart(
        result.get("nav_series"),
        result.get("benchmark_series"),
    )

    # Format holdings
    holdings_data = []
    if portfolio is not None:
        for code, h in portfolio.holdings.items():
            pnl_pct = 0.0
            if h.avg_cost > 0 and h.current_price > 0:
                pnl_pct = (h.current_price - h.avg_cost) / h.avg_cost
            holdings_data.append(
                {
                    "ts_code": code,
                    "name": code,  # TODO: lookup name from stock_basic
                    "avg_cost": h.avg_cost,
                    "current_price": h.current_price,
                    "pnl_pct": pnl_pct,
                }
            )

    context = {
        "report_date": today.strftime("%Y-%m-%d"),
        "is_friday": is_friday,
        "signal_date": result.get("signal_date", today.strftime("%Y-%m-%d")),
        "metrics": _format_metrics(result, portfolio),
        "nav_chart": nav_chart,
        "signals": signals,
        "holdings": holdings_data,
        "factor_ic": factor_ic_data or [],
        "updated_at": today.strftime("%Y-%m-%d %H:%M"),
        "next_rebalance": next_friday.strftime("%Y-%m-%d"),
        "initial_capital": config.backtest.initial_capital,
    }

    return template.render(**context)


def save_report(html: str, config: AppConfig) -> Path:
    """Save HTML report to the output directory. Returns the file path."""
    out_dir = Path(config.report.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    today = date.today()
    filename = f"weekly_{today.strftime('%Y_%m_%d')}.html"
    path = out_dir / filename
    path.write_text(html, encoding="utf-8")
    logger.info(f"Report saved to {path}")
    return path
