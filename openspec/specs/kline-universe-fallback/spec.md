## Purpose

TBD — see change design.md for architecture context.

## Requirements

### Requirement: Universe falls back to kline-derived stocks

When no index constituent records match the as-of date, `get_universe()` SHALL derive the universe from stocks with kline data in the trailing 60 calendar days, excluding ST stocks.

#### Scenario: Historical session without index weights

- **WHEN** `get_universe(["000300.SH"], as_of=2023-06-02)` and `index_weights` has no rows with `in_date <= 2023-06-02`
- **THEN** universe is derived from `daily_kline` rows in [2023-04-03, 2023-06-02]
- **AND** ST stocks are excluded
- **AND** result is non-empty (772 stocks on real data)

#### Scenario: Index weights win when present

- **WHEN** index weights exist for the as-of date
- **THEN** the index-derived universe is returned unchanged (no fallback)

#### Scenario: Both sources empty

- **WHEN** no index weights match and no kline rows exist in the window
- **THEN** empty list is returned

### Requirement: Index daily kline sync

The system SHALL support syncing index daily kline (e.g., 000300.SH, 000905.SH) into `daily_kline` via akshare, covering full history in one call.

#### Scenario: Sync restores market overview

- **WHEN** `sync_index_daily(store, adapter, ["000300.SH", "000905.SH"])` completes
- **THEN** `daily_kline` contains index rows with ts_code ending `.SH` and amount/pct_change/turn_rate filled as 0.0
- **AND** `SnapshotBuilder._build_market_overview()` returns non-null (benchmark_close populated)

#### Scenario: CLI data sync includes indices

- **WHEN** `quant-trade data sync` runs
- **THEN** index daily kline is synced for default indices after index weights sync
