## MODIFIED Requirements

### Requirement: Weekly schedule computation terminates on holiday-gap calendars

`TradeCalendar.weeks_between(start, end)` SHALL always terminate and produce no duplicate signal days, including calendars containing holiday gaps longer than a weekend (e.g., Golden Week, Spring Festival).

#### Scenario: Golden Week gap terminates

- **WHEN** calendar contains trade dates ending Thu 2023-09-28 and resuming Mon 2023-10-09, with `weeks_between(2023-09-25, 2023-10-13)`
- **THEN** result is `[(2023-09-28, 2023-10-09), (2023-10-13, 2023-10-16)]`
- **AND** computation completes in under 100ms

#### Scenario: Start date inside a holiday gap

- **WHEN** start date falls inside the gap (e.g., 2023-10-01, Sunday of Golden Week)
- **THEN** the gap week is skipped and scheduling begins at the next week with trade data

#### Scenario: No duplicate signal days

- **WHEN** any calendar contains multiple holiday gaps
- **THEN** each signal day appears at most once in the result

#### Scenario: Last trade date has no exec day

- **WHEN** the final trade date has no following trade date
- **THEN** that week is not emitted (same as prior behavior)
