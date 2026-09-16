## ADDED Requirements

### Requirement: Session metadata stored in DuckDB

The system SHALL store session metadata (ID, name, date range, capital, cursor, strategy, status, timestamps) in a `simulator_session` table within the existing DuckDB database.

#### Scenario: Session created and queryable

- **WHEN** a new session is created
- **THEN** a row is inserted into `simulator_session` with a UUID id, provided name/date/capital values, status "active", and timestamps for created_at/updated_at

#### Scenario: Session state updated on each step

- **WHEN** user completes a step (cursor advances to the next Friday)
- **THEN** the session row in DuckDB is updated: `cursor_date` set to the new position, `portfolio_json` set to the serialized Portfolio state, `updated_at` set to current UTC timestamp

#### Scenario: Multiple sessions can be listed

- **WHEN** 3 sessions exist in the database (1 active, 1 paused, 1 completed)
- **THEN** querying all sessions returns 3 rows; filtering by `status = "active"` returns 1 row

### Requirement: Decision and snapshot history stored as JSON files

The system SHALL store each week's decision (user orders, notes, snapshot_before, reference signals) as an append-only JSON file under `data/simulator/<session_id>/decisions.json`.

#### Scenario: First decision appended

- **WHEN** user completes their first decision with `[{code: "600519", target_pct: 0.15, direction: "BUY"}]` and note "看好白酒"
- **THEN** `data/simulator/<session_id>/decisions.json` is created and contains a JSON array with one entry: `{decision_number: 1, cursor_date, exec_date, orders: [...], notes: "...", snapshot_before: {...}, strategy_signals: {...}, timestamp}`

#### Scenario: Subsequent decisions appended

- **WHEN** user completes their 5th decision and `decisions.json` already contains 4 entries
- **THEN** the 5th entry is appended to the array in `decisions.json`; the file remains a valid JSON array

### Requirement: Full session state recoverable

A paused session SHALL be fully recoverable: the Portfolio (cash, holdings with avg_cost and buy_dates, trade_log), cursor position, and all decision history MUST be restored to the exact same state as when paused.

#### Scenario: Resume restores exact portfolio

- **WHEN** a session is paused with `cash=32450.52`, holdings `{"600519": {shares: 600, avg_cost: 1650.32}}`, cursor_date `2024-03-15`
- **THEN** upon resume, the Portfolio object has cash `32450.52`, 600 shares of 600519 at avg_cost `1650.32`, and cursor is at `2024-03-15`; the next snapshot request returns data for that date

### Requirement: Session can be deleted

The system SHALL allow users to delete a session and all its associated data.

#### Scenario: Delete active session

- **WHEN** user deletes session "abc-123"
- **THEN** the DuckDB row is removed; `data/simulator/abc-123/` directory and all files within are deleted; subsequent resume attempts return "session not found"

### Requirement: Session schema versioning

The system SHALL include a `schema_version` field in the serialized portfolio JSON to support future format changes.

#### Scenario: Session with older schema version loaded

- **WHEN** loading a session saved with `schema_version: 1` and the current code expects version 2
- **THEN** a migration function is attempted; if migration is impossible, a clear error is raised; if migration succeeds, the session loads normally
