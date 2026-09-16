## ADDED Requirements

### Requirement: Generate snapshot at cursor date

The system SHALL generate a comprehensive snapshot at each cursor Friday aggregating market overview, portfolio state, factor rankings, and strategy signals — all computed using only data available at or before the cursor date.

#### Scenario: Snapshot includes market overview

- **WHEN** snapshot is generated for cursor Friday 2024-03-15
- **THEN** the snapshot includes the CSI 300 close price, weekly return, and the number of trading days in that week, all derived from `daily_kline` with `trade_date <= 2024-03-15`

#### Scenario: Snapshot includes portfolio state

- **WHEN** session holds 2 stocks with current prices available
- **THEN** the snapshot includes for each holding: ts_code, shares, avg_cost, current_price, unrealized P&L percentage, weight percentage of total portfolio value, and the date of last buy; cash balance and total NAV are also included

#### Scenario: Snapshot includes factor ranking

- **WHEN** factors are enabled and data is available
- **THEN** the snapshot includes top-30 stocks ranked by composite factor score, with per-factor breakdowns for momentum, value, and quality dimensions; each row includes rank, code, composite score, and individual factor scores

#### Scenario: Snapshot includes reference strategy signals

- **WHEN** session has `reference_strategy="factor_ranking"`
- **THEN** the snapshot includes the strategy's recommended buys (top-N stocks with target weights and reasons), recommended sells (holdings not in the strategy's target list), and the full strategy target portfolio

### Requirement: No lookahead in snapshot data

All queries used to build the snapshot SHALL filter by trade_date or ann_date ≤ cursor_date to prevent future information leakage.

#### Scenario: Financial data respects announcement date

- **WHEN** generating snapshot at 2024-03-15, a company reported earnings on 2024-04-01 (with `ann_date = 2024-04-01`)
- **THEN** that financial data MUST NOT appear in the factor scores or snapshot for 2024-03-15

#### Scenario: Kline data cut off at cursor date

- **WHEN** generating snapshot at Friday 2024-03-15
- **THEN** all `get_daily` queries use `end=cursor_date`; no trade data from 2024-03-18 (next Monday) or later is included

### Requirement: Snapshot must be computed in under 2 seconds for Phase 1

For CLI responsiveness, a full snapshot (market overview + 10-holdings portfolio + top-30 ranking + strategy signals) SHALL complete within 2 seconds for a universe of up to 800 stocks.

#### Scenario: Snapshot performance on full universe

- **WHEN** snapshot is requested with 800 stocks in universe, 10 holdings, 8 enabled factors
- **THEN** the total computation time is less than 2 seconds

### Requirement: Missing data is annotated in snapshot

When data is unavailable for certain components (e.g., financials table empty, factor computation failure), the snapshot SHALL annotate the affected component with a clear status indicator rather than silently omitting it.

#### Scenario: Value factors unavailable due to empty financials

- **WHEN** `financials` table has zero rows
- **THEN** the factor ranking table shows momentum and technical factors with scores, and value/quality columns are annotated with "无数据" or a similar indicator; the snapshot metadata includes `data_quality_warnings: ["财务数据不可用，价值/质量因子缺失"]`

### Requirement: Snapshot supports programmatic and display consumption

The snapshot SHALL be a typed data structure (dataclass) with both raw data (for programmatic consumers) and formatted display strings (for CLI rendering), produced by the same method.

#### Scenario: CLI renders snapshot as formatted table

- **WHEN** `Simulator.snapshot()` is called from the CLI
- **THEN** the returned Snapshot object contains both `portfolio: list[dict]` (raw) and `portfolio_table: str` (formatted with rich table)

#### Scenario: API returns snapshot as JSON

- **WHEN** `Simulator.snapshot()` is called from the FastAPI endpoint
- **THEN** the dataclass is serialized to JSON with date strings in ISO format and floating-point numbers rounded to 4 decimal places
