"""Trade calendar utilities."""

from datetime import date, datetime, timedelta

import pandas as pd


def _to_date(d: date | pd.Timestamp | datetime) -> date:
    """Convert Timestamp/datetime to date, pass through date objects."""
    if isinstance(d, pd.Timestamp):
        return d.date()
    if isinstance(d, datetime):
        return d.date()
    return d


class TradeCalendar:
    """A-share trade calendar helpers."""

    def __init__(self, trade_dates: list[date]):
        clean = [_to_date(d) for d in trade_dates]
        self._dates: set[date] = set(clean)
        self._sorted: list[date] = sorted(clean)

    @classmethod
    def from_dataframe(cls, df: pd.DataFrame) -> "TradeCalendar":
        """Build TradeCalendar from a DataFrame with 'trade_date' column where is_open=1."""
        dates = df["trade_date"].tolist()
        return cls(dates)

    def is_trade_date(self, d: date) -> bool:
        """Check if a date is a trading day."""
        return _to_date(d) in self._dates

    def next_trade_date(self, d: date) -> date | None:
        """Get the next trading day (>= d). Returns None if none found."""
        target = _to_date(d)
        for td in self._sorted:
            if td >= target:
                return td
        return None

    def last_trade_date(self, d: date | None = None) -> date | None:
        """Get the most recent trading day <= d. Default: today."""
        target = date.today() if d is None else _to_date(d)
        result: date | None = None
        for td in self._sorted:
            if td <= target:
                result = td
            else:
                break
        return result

    def trade_dates_between(self, start: date, end: date) -> list[date]:
        """Get all trading days in [start, end]."""
        s, e = _to_date(start), _to_date(end)
        return [td for td in self._sorted if s <= td <= e]

    def last_trade_date_of_week(self, d: date) -> date | None:
        """Get the last trading day of the week containing date d."""
        d = _to_date(d)
        days_ahead = 4 - d.weekday()
        if days_ahead < 0:
            days_ahead = 0
        friday = d + timedelta(days=days_ahead)
        return self.last_trade_date(friday)

    def weeks_between(self, start: date, end: date) -> list[tuple[date, date]]:
        """
        Get a list of (signal_day, execution_day) pairs for weekly rebalancing.

        Each iteration either appends one week and jumps to the next Monday,
        or skips a holiday gap. ``current`` strictly increases every iteration,
        so the loop always terminates — including calendar weeks where the last
        trade date falls before ``current`` (e.g., Golden Week: signal Sep 28,
        next trade date Oct 9).
        """
        weeks: list[tuple[date, date]] = []
        current = _to_date(start)
        stop = _to_date(end)
        while current <= stop:
            friday = self.last_trade_date_of_week(current)
            if friday is None:
                break
            if friday > stop:
                break
            if friday < current:
                # current sits in a gap after this week's last trade date
                # (weekend or holiday): jump to next Monday and retry
                current = current + timedelta(days=7 - current.weekday())
                continue
            next_day = self.next_trade_date(friday + timedelta(days=1))
            if next_day is None:
                break
            weeks.append((friday, next_day))
            # Jump to next Monday (>= 3 days forward, always past friday)
            current = friday + timedelta(days=7 - friday.weekday())
        return weeks
