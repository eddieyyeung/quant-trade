## MODIFIED Requirements

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
