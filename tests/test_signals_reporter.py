"""Weekly report rendering: the trade table, and the rebalance-day notice.

These run the real Jinja template rather than a stub, because both behaviours
are properties of what the template renders: a section that is present, and a
warning whose date is the one the signals were actually computed for.
"""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from quant_trade.config import AppConfig
from quant_trade.signals.reporter import TRADE_ROWS_SHOWN, generate_weekly_report, save_report


def _config(tmp: str) -> AppConfig:
    config = AppConfig()
    config.report.output_dir = tmp
    return config


def _result(
    signal_date: date,
    trade_log: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A reporter input shaped the way ``as_reporter_input`` builds it.

    ``nav_series`` is empty on purpose: an empty series skips the matplotlib
    chart, which keeps these tests about the HTML and off fonts and pixels.
    """
    payload: dict[str, Any] = {
        "nav_series": pd.Series(dtype=float),
        "benchmark_series": None,
        "metrics": {"total_return": 0.1, "sharpe_ratio": 1.2, "max_drawdown": -0.05, "win_rate": 0.6},
        "portfolio": None,
        "signal_date": signal_date.isoformat(),
    }
    if trade_log is not None:
        payload["trade_log"] = trade_log
    return payload


def _render(tmp: str, result: dict[str, Any], today: date | None = None) -> str:
    return generate_weekly_report(result, [], _config(tmp), today=today)


def _buy(day: str, code: str = "600519.SH") -> dict[str, Any]:
    return {
        "date": day,
        "action": "BUY",
        "ts_code": code,
        "shares": 100,
        "price": 10.0,
        "commission": 5.0,
        "transfer_fee": 0.1,
    }


def _sell(day: str, code: str = "300750.SZ") -> dict[str, Any]:
    return {
        "date": day,
        "action": "SELL",
        "ts_code": code,
        "shares": 100,
        "price": 20.0,
        "commission": 5.0,
        "stamp_duty": 10.0,
        "transfer_fee": 0.2,
    }


class TestRebalanceNotice:
    """The notice keys off the signal date, not off the day the report is run."""

    def test_wednesday_run_shows_the_notice_with_its_own_date(self) -> None:
        wednesday = date(2026, 7, 22)
        with tempfile.TemporaryDirectory() as tmp:
            html = _render(tmp, _result(wednesday), today=wednesday)

        assert "非调仓日运行" in html
        assert "2026-07-22" in html
        # The copy used to claim the date was 最近周五 while filling in the
        # actual signal date — false whenever the run was not on a Friday.
        assert "最近周五" not in html

    def test_friday_signal_date_has_no_notice(self) -> None:
        friday = date(2026, 7, 24)
        with tempfile.TemporaryDirectory() as tmp:
            html = _render(tmp, _result(friday), today=friday)

        assert "非调仓日运行" not in html

    def test_friday_holiday_still_warns(self) -> None:
        """Run on a Friday, but the market was shut — the newest data is Thursday's.

        Asking "is today a Friday" answers yes here and suppresses a notice that
        the reader needs, because the signals are a day older than they look.
        """
        friday = date(2026, 7, 24)
        thursday = date(2026, 7, 23)
        with tempfile.TemporaryDirectory() as tmp:
            html = _render(tmp, _result(thursday), today=friday)

        assert "非调仓日运行" in html
        assert "2026-07-23" in html

    def test_injected_dates_decide_the_outcome(self) -> None:
        """Same template, two injected dates, opposite notices — no clock read."""
        with tempfile.TemporaryDirectory() as tmp:
            quiet = _render(tmp, _result(date(2026, 7, 24)), today=date(2026, 7, 24))
            loud = _render(tmp, _result(date(2026, 7, 24)), today=date(2026, 7, 22))

        assert "非调仓日运行" not in quiet
        # Signal date is a Friday, so no notice even though the run date is not:
        # the notice describes the data, not the calendar.
        assert "非调仓日运行" not in loud

    def test_notice_does_not_change_the_signals(self) -> None:
        """The notice reports a fact; it must not alter what was computed."""
        signal_date = date(2026, 7, 24)
        signals = [
            {"ts_code": "600519.SH", "name": "600519.SH", "target_pct": 0.1, "direction": "BUY", "reason": "得分靠前"}
        ]
        with tempfile.TemporaryDirectory() as tmp:
            as_friday = generate_weekly_report(_result(signal_date), signals, _config(tmp), today=signal_date)
            as_wednesday = generate_weekly_report(_result(signal_date), signals, _config(tmp), today=date(2026, 7, 22))

        for html in (as_friday, as_wednesday):
            assert "600519.SH" in html
            assert "得分靠前" in html


class TestSaveReport:
    def test_filename_comes_from_the_injected_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = save_report("<html></html>", _config(tmp), today=date(2026, 7, 22))

            assert path.name == "weekly_2026_07_22.html"
            assert path.parent == Path(tmp)


class TestTradeTable:
    def test_fills_are_rendered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            html = _render(tmp, _result(date(2026, 7, 24), [_buy("2026-07-24")]), today=date(2026, 7, 24))

        assert "交易明细" in html
        assert "600519.SH" in html
        assert "共 1 笔" in html

    def test_absent_trade_log_renders_no_section(self) -> None:
        """A result dict without the key is normal, not an error."""
        with tempfile.TemporaryDirectory() as tmp:
            html = _render(tmp, _result(date(2026, 7, 24)), today=date(2026, 7, 24))

        assert "交易明细" not in html

    def test_buy_row_reports_zero_stamp_duty(self) -> None:
        """Buy rows carry no ``stamp_duty`` key at all — it is a sell-side tax."""
        with tempfile.TemporaryDirectory() as tmp:
            html = _render(tmp, _result(date(2026, 7, 24), [_buy("2026-07-24")]), today=date(2026, 7, 24))

        # commission 5.00 + transfer 0.10 + stamp duty 0.00
        assert "¥5.10" in html

    def test_sell_row_includes_stamp_duty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            html = _render(tmp, _result(date(2026, 7, 24), [_sell("2026-07-24")]), today=date(2026, 7, 24))

        # commission 5.00 + stamp 10.00 + transfer 0.20
        assert "¥15.20" in html

    def test_long_log_is_truncated_and_says_so(self) -> None:
        log = [_buy(f"2026-07-{(i % 28) + 1:02d}", code=f"{i:06d}.SZ") for i in range(TRADE_ROWS_SHOWN + 1)]
        with tempfile.TemporaryDirectory() as tmp:
            html = _render(tmp, _result(date(2026, 7, 24), log), today=date(2026, 7, 24))

        assert f"共 {TRADE_ROWS_SHOWN + 1} 笔" in html
        assert f"展示最近 {TRADE_ROWS_SHOWN} 笔" in html
        # Newest first: the oldest fill is the one dropped, not the latest.
        assert log[0]["ts_code"] not in html
        assert log[-1]["ts_code"] in html

    def test_short_log_is_not_announced_as_truncated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            html = _render(tmp, _result(date(2026, 7, 24), [_buy("2026-07-24")]), today=date(2026, 7, 24))

        assert "展示最近" not in html


class TestMetricsSurface:
    def test_turnover_is_never_rendered(self) -> None:
        """The engine pins turnover at 0.0 and never fills it.

        Rendering it would state a number that is always false, which is worse
        than stating nothing.
        """
        result = _result(date(2026, 7, 24))
        result["metrics"]["turnover"] = 0.0
        with tempfile.TemporaryDirectory() as tmp:
            html = _render(tmp, result, today=date(2026, 7, 24))

        assert "换手" not in html


class TestFactorICPanel:
    """The panel's rows and its sign colouring."""

    ROWS = [{"name": "momentum_20d", "ic_weekly": -0.015, "ic_mean": 0.041, "ic_ir": 0.8}]

    @staticmethod
    def _ic_section(html: str) -> str:
        """Just the IC table, so class assertions cannot match the metric cards."""
        start = html.index("因子表现跟踪")
        return html[start : html.index("</table>", start)]

    def test_each_metric_is_coloured_by_its_own_sign(self) -> None:
        """A negative week against a positive cumulative mean.

        The two cells sit in one row and must be coloured independently —
        sharing a class would hide exactly the decay this panel exists to show.
        """
        with tempfile.TemporaryDirectory() as tmp:
            html = generate_weekly_report(
                _result(date(2026, 7, 24)), [], _config(tmp), factor_ic_data=self.ROWS, today=date(2026, 7, 24)
            )

        section = self._ic_section(html)
        assert "momentum_20d" in section
        assert "-0.0150" in section and "+0.0410" in section
        assert 'class="negative"' in section, "the negative week is not marked red"
        assert 'class="positive"' in section, "the positive cumulative mean is not marked green"

    def test_absent_ic_renders_no_section(self) -> None:
        """No producer means no section — not an empty table with a heading."""
        with tempfile.TemporaryDirectory() as tmp:
            html = generate_weekly_report(_result(date(2026, 7, 24)), [], _config(tmp), today=date(2026, 7, 24))

        assert "因子表现跟踪" not in html

    def test_empty_list_renders_no_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            html = generate_weekly_report(
                _result(date(2026, 7, 24)), [], _config(tmp), factor_ic_data=[], today=date(2026, 7, 24)
            )

        assert "因子表现追踪" not in html
        assert "因子表现跟踪" not in html
