# duckdb-memory-control Specification

## Purpose
TBD - created by archiving change fix-simulator-memory-leak. Update Purpose after archive.
## Requirements
### Requirement: DuckDB connection initializes with memory and thread limits

When a DuckDB connection is initialized via `init_db()`, the system SHALL execute the following pragma statements:

- `SET memory_limit = '512MB'`
- `SET threads = 2`

#### Scenario: connection initialization sets pragmas

- **WHEN** `init_db(db_path)` is called to create or open a database connection
- **THEN** the connection's `memory_limit` SHALL be set to 512MB
- **AND** the connection's `threads` SHALL be set to 2

#### Scenario: existing connections are not affected

- **WHEN** a DuckDB connection already exists and is reused via `DataStore.conn` property
- **THEN** the pragma settings SHALL have been applied at initial connection time and remain in effect

### Requirement: DataStore.close triggers memory shrink

When `DataStore.close()` is called, the system SHALL execute `PRAGMA shrink_memory` before closing the connection, attempting to release DuckDB's internal buffer pool back to the operating system.

#### Scenario: close releases memory

- **WHEN** `DataStore.close()` is called on an active connection
- **THEN** `PRAGMA shrink_memory` SHALL be executed
- **AND** the connection SHALL then be closed

