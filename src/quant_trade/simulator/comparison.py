"""Multi-line comparison — manual vs strategy vs benchmark."""

from __future__ import annotations

import base64
import io
from datetime import date
from pathlib import Path
from typing import Any, cast

import matplotlib
import matplotlib.pyplot as plt
import pandas as pd
from loguru import logger

from quant_trade.backtest.engine import compute_metrics, run_backtest
from quant_trade.backtest.portfolio import Portfolio
from quant_trade.data.calendar import TradeCalendar
from quant_trade.data.store import DataStore
from quant_trade.simulator.session import SessionStore
from quant_trade.simulator.types import ComparisonResult, WeeklyDiff
from quant_trade.strategies.registry import strategy_registry

matplotlib.use("Agg")


class ComparisonEngine:
    """Builds comparison reports for a simulator session."""

    def __init__(self, store: DataStore, session_store: SessionStore) -> None:
        self._store = store
        self._sessions = session_store

    def compare(self, session_id: str, export_html: bool = False) -> ComparisonResult:
        """Generate full comparison report."""
        row = self._sessions.load(session_id)
        if row is None:
            raise ValueError(f"Session {session_id} not found")

        start_date = _to_date(row["start_date"])
        end_date = _to_date(row["cursor_date"])
        ref_name = row.get("reference_strategy")

        # 1. Build manual NAV from decisions
        decisions = self._sessions.load_decisions(session_id)
        manual_nav = self._build_manual_nav(row, decisions, start_date, end_date)

        # 2. Strategy NAV via run_backtest
        strategy_nav: list[dict[str, Any]] | None = None
        strategy_holds_by_week: dict[int, list[str]] = {}
        if ref_name:
            strategy_nav, strategy_holds_by_week = self._run_strategy_shadow(
                ref_name, start_date, end_date, row["initial_capital"]
            )

        # 3. Benchmark NAV
        benchmark_nav = self._compute_benchmark(start_date, end_date)

        # 4. Compute metrics
        metrics = self._compute_metrics_triple(manual_nav, strategy_nav, benchmark_nav)

        # 5. Build weekly diffs
        weekly_diffs = self._build_weekly_diffs(decisions, strategy_holds_by_week)

        # 6. Annotate extremes
        weekly_diffs = self._annotate_extremes(weekly_diffs, manual_nav)

        result = ComparisonResult(
            session_id=session_id,
            weeks_completed=len(decisions),
            nav_manual=manual_nav,
            nav_strategy=strategy_nav,
            nav_benchmark=benchmark_nav,
            metrics=metrics,
            weekly_diffs=weekly_diffs,
        )

        if export_html:
            result.html_path = self._export_html(result, row)

        return result

    def _build_manual_nav(
        self, row: dict[str, Any], decisions: list[Any], start: date, end: date
    ) -> list[dict[str, Any]]:
        """Rebuild manual NAV from decision history + current portfolio."""
        initial_capital = float(row["initial_capital"])
        portfolio = Portfolio(cash=initial_capital, initial_capital=initial_capital)

        cal_df = self._store.get_calendar(start, end)
        calendar = TradeCalendar(cal_df["trade_date"].tolist())
        all_dates = calendar.trade_dates_between(start, end)

        nav_entries: list[dict[str, Any]] = []
        nav_entries.append({"trade_date": str(start), "nav": initial_capital})

        for decision in decisions:
            exec_date = _to_date(decision.exec_date)
            for order in decision.executed_orders:
                if order.direction == "BUY":
                    portfolio.buy(
                        code=order.ts_code,
                        price=order.price,
                        amount=order.shares * order.price,
                        trade_date=exec_date,
                    )
                elif order.direction == "SELL":
                    portfolio.sell(
                        code=order.ts_code,
                        price=order.price,
                        shares=order.shares,
                        trade_date=exec_date,
                    )

            # Mark NAV at the decision's cursor date
            nav_entry = self._mark_nav(portfolio, _to_date(decision.cursor_date))
            if nav_entry:
                nav_entries.append(nav_entry)

        # Mark current NAV
        current = portfolio.total_value
        last_date = str(all_dates[-1]) if all_dates else str(end)
        if not nav_entries or nav_entries[-1]["trade_date"] != last_date:
            nav_entries.append({"trade_date": last_date, "nav": current})

        return nav_entries

    def _mark_nav(self, portfolio: Portfolio, trade_date: date) -> dict[str, Any] | None:
        """Get a NAV point by marking holdings to close prices."""
        codes = list(portfolio.holdings.keys())
        if codes:
            prices_df = self._store.get_daily(codes, trade_date, trade_date, fields=["ts_code", "close"])
            if not prices_df.empty:
                price_map: dict[str, float] = {}
                for _, row in prices_df.iterrows():
                    price_map[str(row["ts_code"])] = float(row["close"])
                portfolio.update_prices(price_map)
        return {"trade_date": str(trade_date), "nav": portfolio.total_value}

    def _run_strategy_shadow(
        self, ref_name: str, start: date, end: date, initial_capital: float
    ) -> tuple[list[dict[str, Any]] | None, dict[int, list[str]]]:
        """Run strategy backtest and return NAV + per-week holdings."""
        strategy = strategy_registry.get(str(ref_name))
        if strategy is None:
            return None, {}

        try:
            result = run_backtest(
                strategy=strategy,
                start=start,
                end=end,
                initial_capital=initial_capital,
                store=self._store,
            )
        except Exception as e:
            logger.warning(f"Strategy backtest failed: {e}")
            return None, {}

        nav_series = result.get("nav_series")
        if nav_series is None or nav_series.empty:
            return None, {}

        nav: list[dict[str, Any]] = []
        if isinstance(nav_series, pd.Series):
            for idx, val in nav_series.items():
                nav.append({"trade_date": str(_to_date(idx)), "nav": round(float(val), 2)})
        return nav, {}

    def _compute_benchmark(self, start: date, end: date) -> list[dict[str, Any]]:
        """Get CSI 300 normalized NAV."""
        df = self._store.get_daily(["000300.SH"], start, end, fields=["trade_date", "close"])
        if df.empty:
            return []
        df = df.sort_values("trade_date")
        if df.empty:
            return []
        base = float(df.iloc[0]["close"])
        if base <= 0:
            return []
        result: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            result.append(
                {
                    "trade_date": str(_to_date(row["trade_date"])),
                    "nav": round(float(row["close"]) / base, 4),
                }
            )
        return result

    def _compute_metrics_triple(
        self,
        manual_nav: list[dict[str, Any]],
        strategy_nav: list[dict[str, Any]] | None,
        benchmark_nav: list[dict[str, Any]] | None,
    ) -> dict[str, dict[str, float]]:
        """Compute key metrics for each line."""
        metrics: dict[str, dict[str, float]] = {}

        metrics["manual"] = self._calc_metrics(manual_nav)
        if strategy_nav:
            metrics["strategy"] = self._calc_metrics(strategy_nav)
        if benchmark_nav:
            metrics["benchmark"] = self._calc_metrics(benchmark_nav)
        return metrics

    def _calc_metrics(self, nav_list: list[dict[str, Any]]) -> dict[str, float]:
        """Compute metrics from a NAV dict list."""
        if not nav_list or len(nav_list) < 2:
            return {
                "total_return": 0.0,
                "annual_return": 0.0,
                "annual_volatility": 0.0,
                "sharpe_ratio": 0.0,
                "max_drawdown": 0.0,
                "win_rate": 0.0,
            }
        # Build Series
        dates = [date.fromisoformat(item["trade_date"][:10]) for item in nav_list]
        values = [float(item["nav"]) for item in nav_list]
        s = pd.Series(values, index=pd.DatetimeIndex(dates))
        return compute_metrics(s)

    def _build_weekly_diffs(self, decisions: list[Any], strategy_holds: dict[int, list[str]]) -> list[WeeklyDiff]:
        """Compare user decisions vs strategy week by week."""
        diffs: list[WeeklyDiff] = []
        for d in decisions:
            user_codes = [o.ts_code for o in d.executed_orders if o.direction == "BUY"]
            strategy_codes = strategy_holds.get(d.decision_number, [])

            user_set = set(user_codes)
            strategy_set = set(strategy_codes)

            common = sorted(user_set & strategy_set)
            user_only = sorted(user_set - strategy_set)
            strategy_only = sorted(strategy_set - user_set)

            diffs.append(
                WeeklyDiff(
                    week_number=d.decision_number,
                    cursor_date=_to_date(d.cursor_date),
                    user_holds=sorted(user_codes),
                    strategy_holds=sorted(strategy_codes),
                    user_only=user_only,
                    strategy_only=strategy_only,
                    common=common,
                    overlap_count=len(common),
                    total_user=len(user_codes),
                    total_strategy=len(strategy_codes),
                )
            )
        return diffs

    def _annotate_extremes(self, diffs: list[WeeklyDiff], manual_nav: list[dict[str, Any]]) -> list[WeeklyDiff]:
        """Annotate concentration and drawdown warnings."""
        if len(manual_nav) < 3:
            return diffs

        values = [float(item["nav"]) for item in manual_nav]
        for i, diff in enumerate(diffs):
            # Concentration: ≤2 stocks held
            if diff.total_user <= 2 and diff.total_user > 0:
                diff.concentration_warning = f"持仓过度集中: {diff.total_user}只"

            # Weekly drawdown
            if i > 0 and i <= len(values) - 1:
                prev_val = values[i - 1]
                curr_val = min(values[i], values[-1]) if i >= len(values) else values[i]
                if prev_val > 0:
                    weekly_return = (curr_val - prev_val) / prev_val
                    if weekly_return < -0.10:
                        diff.drawdown_warning = f"周回撤: {weekly_return:.1%}"
        return diffs

    def _export_html(self, result: ComparisonResult, row: dict[str, Any]) -> str:
        """Export comparison to HTML."""
        out_dir = Path("reports")
        out_dir.mkdir(parents=True, exist_ok=True)
        filename = f"sim_{result.session_id[:8]}_{date.today().strftime('%Y_%m_%d')}.html"
        path = out_dir / filename

        chart_b64 = self._render_comparison_chart(result)

        diff_rows = ""
        for d in result.weekly_diffs:
            warnings_html = ""
            if d.concentration_warning:
                warnings_html += f'<span class="warn">⚠️ {d.concentration_warning}</span> '
            if d.drawdown_warning:
                warnings_html += f'<span class="warn">📉 {d.drawdown_warning}</span>'
            diff_rows += f"""
            <tr>
                <td>{d.week_number}</td>
                <td>{d.cursor_date}</td>
                <td>{", ".join(d.user_only) or "-"}</td>
                <td>{", ".join(d.strategy_only) or "-"}</td>
                <td>{", ".join(d.common) or "-"}</td>
                <td>{warnings_html or "-"}</td>
            </tr>"""

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>模拟对比报告</title>
<style>
body{{font-family:-apple-system,sans-serif;max-width:960px;margin:0 auto;padding:20px;color:#1a1a2e}}
h1{{border-bottom:2px solid #3b82f6;padding-bottom:8px}}
.metrics{{display:flex;gap:20px;flex-wrap:wrap;margin:20px 0}}
.card{{background:#f8fafc;border-radius:8px;padding:16px;min-width:200px;flex:1}}
.card h3{{margin:0 0 8px;color:#64748b;font-size:13px}}
.card .val{{font-size:24px;font-weight:700;color:#0f172a}}
.pos{{color:#10b981}}.neg{{color:#ef4444}}
table{{width:100%;border-collapse:collapse;margin:16px 0}}
th,td{{border:1px solid #e2e8f0;padding:8px 12px;text-align:left;font-size:13px}}
th{{background:#f1f5f9}}
.warn{{color:#f59e0b;font-size:12px}}
</style></head>
<body>
<h1>模拟对比报告</h1>
<p>会话: {row.get("name", "")} | 周期: {row.get("start_date", "")} → {row.get("cursor_date", "")}</p>
<img src="data:image/png;base64,{chart_b64}" style="width:100%" alt="NAV Comparison">

<h2>关键指标</h2>
<div class="metrics">
{self._render_metric_cards(result.metrics)}
</div>

<h2>逐周决策差异</h2>
<table>
<tr><th>周</th><th>日期</th><th>你选了(策略未选)</th><th>策略选了(你未选)</th><th>共同</th><th>风险标注</th></tr>
{diff_rows}
</table>
</body></html>"""

        path.write_text(html, encoding="utf-8")
        logger.info(f"Comparison report saved to {path}")
        return str(path)

    def _render_comparison_chart(self, result: ComparisonResult) -> str:
        """Render three-line NAV chart to base64."""
        fig, ax = plt.subplots(figsize=(10, 4))

        if result.nav_manual:
            dates = [date.fromisoformat(d["trade_date"][:10]) for d in result.nav_manual]
            vals = [d["nav"] for d in result.nav_manual]
            if vals and vals[0] != 0:
                vals = [v / vals[0] for v in vals]
            ax.plot(pd.to_datetime(dates), vals, label="手动", color="#3b82f6", linewidth=1.5)

        if result.nav_strategy:
            dates = [date.fromisoformat(d["trade_date"][:10]) for d in result.nav_strategy]
            vals = [d["nav"] for d in result.nav_strategy]
            if vals and vals[0] != 0:
                vals = [v / vals[0] for v in vals]
            ax.plot(pd.to_datetime(dates), vals, label="策略", color="#10b981", linewidth=1, linestyle="--")

        if result.nav_benchmark:
            dates = [date.fromisoformat(d["trade_date"][:10]) for d in result.nav_benchmark]
            vals = [d["nav"] for d in result.nav_benchmark]
            if vals and vals[0] != 0:
                vals = [v / vals[0] for v in vals]
            ax.plot(pd.to_datetime(dates), vals, label="沪深300", color="#9ca3af", linewidth=1, linestyle=":")

        ax.axhline(y=1.0, color="#e5e7eb", linewidth=0.5)
        ax.legend(loc="upper left")
        ax.grid(True, alpha=0.3)
        ax.set_ylabel("归一化净值")
        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100)
        plt.close(fig)
        buf.seek(0)
        return base64.b64encode(buf.read()).decode()

    def _render_metric_cards(self, metrics: dict[str, dict[str, float]]) -> str:
        """Render metric cards as HTML."""
        labels = {
            "total_return": "累计收益",
            "annual_return": "年化收益",
            "sharpe_ratio": "夏普比率",
            "max_drawdown": "最大回撤",
            "win_rate": "周胜率",
        }
        html_parts: list[str] = []
        for line_name, line_metrics in metrics.items():
            name_cn = {"manual": "手动", "strategy": "策略", "benchmark": "基准"}.get(line_name, line_name)
            html_parts.append(f'<div class="card"><h3>{name_cn}</h3>')
            for key, label in labels.items():
                val = line_metrics.get(key, 0)
                cls = "pos" if val > 0 else "neg" if val < 0 else ""
                fmt = (
                    f"{val:.2%}"
                    if key in ("total_return", "annual_return", "max_drawdown", "win_rate")
                    else f"{val:.2f}"
                )
                html_parts.append(f'<div>{label}: <span class="{cls}">{fmt}</span></div>')
            html_parts.append("</div>")
        return "\n".join(html_parts)


def _to_date(d: Any) -> date:
    """Convert various representations to date."""
    if isinstance(d, date):
        return d
    if hasattr(d, "date"):
        return cast(date, d.date())
    if isinstance(d, str):
        return date.fromisoformat(d[:10])
    return date.today()
