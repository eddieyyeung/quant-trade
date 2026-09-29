## MODIFIED Requirements

### Requirement: Index daily kline sync

The system SHALL support syncing index daily kline (e.g., 000300.SH, 000905.SH) into `daily_kline` via akshare, covering full history in one call. Index sync SHALL be invoked as part of the data sync service, not as a separate command.

#### Scenario: Sync restores market overview

- **WHEN** `sync_index_daily(store, adapter, ["000300.SH", "000905.SH"])` completes
- **THEN** `daily_kline` contains index rows with ts_code ending `.SH` and amount/pct_change/turn_rate filled as 0.0
- **AND** `SnapshotBuilder._build_market_overview()` returns non-null (benchmark_close populated)

#### Scenario: CLI data sync includes indices

<!-- Name retained verbatim: the archive tool treats a renamed scenario as a
     dropped one. The CLI is gone; the name is stale. Renaming it needs its own
     change (the delta format has no scenario-level rename). -->

- **WHEN** the data sync service runs
- **THEN** index daily kline is synced for default indices after index weights sync
