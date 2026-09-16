## Purpose

Simulator engine: interactive weekly rebalance loop — session lifecycle (create/resume/status), user order execution under A-share rules, week skipping, and reference strategy shadow execution.

## Requirements

### Requirement: Create a new simulation session

The system SHALL allow users to create a simulation session with a name, start date, optional end date, initial capital, and optional reference strategy.

#### Scenario: Create session with all parameters

- **WHEN** user creates session with `name="2024复盘"`, `start=2024-01-01`, `end=2024-12-31`, `initial_capital=100000`, `reference_strategy="factor_ranking"`
- **THEN** a session is created with status "active", cursor_date set to the first Friday ≥ start, portfolio initialized with cash = initial_capital, and reference strategy stored

#### Scenario: Create session without end date

- **WHEN** user creates session without specifying end date
- **THEN** end_date is set to the latest available trade date in the database

#### Scenario: Create session without reference strategy

- **WHEN** user creates session with `reference_strategy=None`
- **THEN** the session is created and snapshots will not include strategy recommendations

### Requirement: Resume a paused or in-progress session

The system SHALL allow users to resume a session by its ID, restoring the cursor position, portfolio state, and all prior decisions.

#### Scenario: Resume paused session

- **WHEN** user resumes session "abc-123" that was paused at cursor_date 2024-03-15
- **THEN** portfolio holdings, cash, and trade log are restored exactly; cursor returns to the same Friday; a snapshot is generated and ready for the next decision

#### Scenario: Resume non-existent session

- **WHEN** user tries to resume session ID that does not exist
- **THEN** an error is returned with message "session not found"

### Requirement: Execute a weekly step with user orders

The system SHALL accept user buy/sell orders at the cursor Friday, validate them against A-share rules, execute at next Monday's open price, and advance the cursor to the following Friday.

#### Scenario: Buy order executed at Monday open

- **WHEN** cursor is at Friday 2024-03-15, user submits `[{code: "600519", target_pct: 0.15, direction: "BUY"}]`
- **THEN** the order is executed at 2024-03-18 open price, subject to A-share fee model (commission ≥5, transfer fee), with 100-share lot rounding; cursor advances to the next Friday's close 2024-03-22; NAV is marked at each intermediate trading day's close

#### Scenario: Sell order blocked by T+1

- **WHEN** a stock was bought on the same Monday and the user tries to sell it in the SAME step
- **THEN** the sell is rejected with reason "T+1 restriction"

Actually — within a single step, buys execute AFTER sells in the engine. But a stock already in the portfolio that was bought last week can be sold; a stock bought in the PREVIOUS step's Monday (last week) would have `buy_date < exec_date` so T+1 is satisfied. Let me rephrase.

#### Scenario: Sell order blocked by T+1

- **WHEN** a stock was bought on the previous execution Monday and user tries to sell it this week, with `buy_date == exec_date` (same calendar Monday)
- **THEN** the sell is rejected with reason "T+1 restriction"

#### Scenario: Buy order blocked by limit-up

- **WHEN** a stock's execution price hits or exceeds its daily limit-up price
- **THEN** the buy order is skipped with reason "涨停无法买入"

#### Scenario: Order blocked by suspension

- **WHEN** a stock is detected as suspended (zero volume in recent 10 calendar days)
- **THEN** the order is skipped with reason "停牌"

#### Scenario: Single buy exceeds 30% cash cap

- **WHEN** user submits a buy order where the target amount exceeds 30% of available cash
- **THEN** the buy is capped at `cash * 0.3` and executed at that reduced amount

#### Scenario: Multiple buy orders in one step

- **WHEN** user submits 3 buy orders and 1 sell order in a single step
- **THEN** all sells execute first (freeing cash), then all buys execute (using updated cash balance)

### Requirement: Skip a week without trading

The system SHALL allow users to skip a weekly rebalance without making any changes, advancing the cursor to the next Friday while keeping current holdings intact.

#### Scenario: Skip a week

- **WHEN** user calls `skip()` at cursor Friday 2024-03-15 with holdings of 600519 and 300750
- **THEN** cursor advances to the next Friday; holdings remain unchanged; NAV is recorded for all intermediate trading days using daily close prices; the skip decision is recorded in the decision log with `orders=[]`

### Requirement: Get current simulation status

The system SHALL return the current session state including cursor position, portfolio summary, and count of decisions made.

#### Scenario: Status query

- **WHEN** user queries session status after 12 decisions
- **THEN** response includes `{cursor_date, total_weeks_completed, portfolio_value, cash, holding_count, decision_count, status}`

### Requirement: Reference strategy shadow execution

When a reference strategy is configured, the system SHALL run it in shadow mode at each step to generate what the strategy would have recommended, without executing any of its orders.

#### Scenario: Strategy signals recorded but not executed

- **WHEN** session has `reference_strategy="factor_ranking"` and user makes a manual decision
- **THEN** `strategy.generate_signals(cursor_date, universe, store)` is called; the resulting SignalResult is saved to the decision record; strategy signals are displayed in the snapshot but do NOT affect the portfolio

#### Scenario: No reference strategy

- **WHEN** session has `reference_strategy=None`
- **THEN** no strategy signal computation runs; snapshot does not include strategy recommendations

### Requirement: Simulator logs snapshot build progress at INFO level

The system SHALL emit INFO-level log messages at each stage of snapshot construction so users can observe progress during interactive sessions.

#### Scenario: Snapshot build logs are visible

- **WHEN** `SnapshotBuilder.build_snapshot()` is called
- **THEN** the following messages are emitted at INFO level:
  - `"Building snapshot: cursor_date={}, universe_size={}"`
  - `"Building market overview..."`
  - `"Building portfolio snapshot..."`
  - `"Building factor ranking..."`
  - `"Building strategy signals..."`
- **AND** factor-level progress (e.g., `"Factor 1/6: momentum_20d..."`) remains at INFO level

#### Scenario: Factor ranking skip logs a warning

- **WHEN** `build_snapshot()` is called with `skip_heavy=True`
- **THEN** an INFO log `"Skipping factor ranking and strategy signals (deferred to first step)"` is emitted

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
