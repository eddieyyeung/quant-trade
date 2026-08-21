"""Test TradeCalendar week scheduling, including holiday-gap edge cases."""

from datetime import date, timedelta

from quant_trade.data.calendar import TradeCalendar


class TestWeeksBetween:
    def test_dense_calendar_weekly_pairs(self) -> None:
        """Plain Mon-Fri calendar: one (Friday, next Monday) pair per week."""
        start = date(2024, 1, 1)  # Monday
        dates = [start + timedelta(days=d) for d in range(16) if (start + timedelta(days=d)).weekday() < 5]
        cal = TradeCalendar(dates)

        weeks = cal.weeks_between(date(2024, 1, 1), date(2024, 1, 14))
        assert weeks == [
            (date(2024, 1, 5), date(2024, 1, 8)),
            (date(2024, 1, 12), date(2024, 1, 15)),
        ]

    def test_golden_week_gap_terminates(self) -> None:
        """Holiday week where the last trade date falls before the gap end.

        Reproduces the 2023 Golden Week pattern: last trade date Thu Sep 28,
        market closed until Mon Oct 9. The old algorithm looped forever
        appending (Sep 28, Oct 9).
        """
        dates = [
            date(2023, 9, 25),
            date(2023, 9, 26),
            date(2023, 9, 27),
            date(2023, 9, 28),
            # 9/29 - 10/8 holiday
            date(2023, 10, 9),
            date(2023, 10, 10),
            date(2023, 10, 11),
            date(2023, 10, 12),
            date(2023, 10, 13),
            date(2023, 10, 16),
        ]
        cal = TradeCalendar(dates)

        weeks = cal.weeks_between(date(2023, 9, 25), date(2023, 10, 13))
        assert weeks == [
            (date(2023, 9, 28), date(2023, 10, 9)),
            (date(2023, 10, 13), date(2023, 10, 16)),
        ]

    def test_start_in_gap_jumps_to_next_week(self) -> None:
        """Start date inside a holiday gap skips to the next week."""
        dates = [
            date(2023, 9, 28),  # Thursday before holiday
            date(2023, 10, 9),
            date(2023, 10, 10),
            date(2023, 10, 11),
            date(2023, 10, 12),
            date(2023, 10, 13),
            date(2023, 10, 16),
        ]
        cal = TradeCalendar(dates)

        # Oct 1 (Sunday) is inside the gap
        weeks = cal.weeks_between(date(2023, 10, 1), date(2023, 10, 13))
        assert weeks == [(date(2023, 10, 13), date(2023, 10, 16))]

    def test_no_duplicate_weeks(self) -> None:
        """No duplicate signal days across a holiday calendar."""
        dates = [
            date(2023, 9, 25),
            date(2023, 9, 26),
            date(2023, 9, 27),
            date(2023, 9, 28),
            date(2023, 10, 9),
            date(2023, 10, 10),
            date(2023, 10, 11),
            date(2023, 10, 12),
            date(2023, 10, 13),
            date(2023, 10, 16),
        ]
        cal = TradeCalendar(dates)

        weeks = cal.weeks_between(date(2023, 9, 25), date(2023, 10, 16))
        signals = [w[0] for w in weeks]
        assert len(signals) == len(set(signals))

    def test_end_has_no_exec_day(self) -> None:
        """Last trade date has no next trade date — week is not emitted."""
        dates = [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        cal = TradeCalendar(dates)

        weeks = cal.weeks_between(date(2024, 1, 1), date(2024, 1, 4))
        assert weeks == []  # Friday is the last date, no exec day after
