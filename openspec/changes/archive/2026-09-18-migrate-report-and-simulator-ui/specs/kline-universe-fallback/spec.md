## MODIFIED Requirements

### Requirement: Index daily kline sync

The system SHALL support syncing index daily kline (e.g., 000300.SH, 000905.SH) into `daily_kline` via akshare, covering full history in one call. Index sync SHALL be invoked as part of the data sync service, not as a separate command.

The market-overview assertion SHALL be exercised against rows the index sync itself wrote. Hand-inserted rows can carry any shape, so seeding `daily_kline` directly proves nothing about the sync: the coverage SHALL drive `sync_index_daily` against a stub adapter and assert on the rows it produced.

#### Scenario: Sync restores market overview

- **WHEN** `sync_index_daily(store, adapter, ["000300.SH", "000905.SH"])` completes
- **THEN** `daily_kline` contains index rows with ts_code ending `.SH` and amount/pct_change/turn_rate filled as 0.0
- **AND** `SnapshotBuilder._build_market_overview()` returns non-null (benchmark_close populated)

#### Scenario: Market overview covered through the sync path

- **WHEN** a fresh database is populated by driving `sync_index_daily` with a stub adapter, and `SnapshotBuilder._build_market_overview()` is then called for a date the synced rows cover
- **THEN** the returned overview is non-null, with `benchmark_code` `000300.SH` and a non-zero `benchmark_close`
- **AND** the assertion holds on rows the sync wrote, not on rows the test inserted itself

<!-- Name retained verbatim: the archive tool treats a renamed scenario as a
     dropped one. The CLI is gone; the name is stale. Renaming it needs its own
     change (the delta format has no scenario-level rename). -->

#### Scenario: CLI data sync includes indices

- **WHEN** the data sync service runs
- **THEN** index daily kline is synced for default indices after index weights sync
