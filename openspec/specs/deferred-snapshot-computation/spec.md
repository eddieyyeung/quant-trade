# Deferred Snapshot Computation

## Purpose

Session creation should return quickly for interactive use. The initial snapshot therefore skips heavy factor ranking and strategy signal computation, deferring it to the first step or resume. This spec covers the deferred-computation behavior of snapshot construction. (TBD: expand once implementation details settle.)

## Requirements

### Requirement: Initial create_session snapshot skips heavy computation

When `Simulator.create()` builds the first snapshot, the system SHALL skip factor ranking and strategy signal computation, setting those fields to empty/null.

#### Scenario: First snapshot is lightweight

- **WHEN** user creates a session with `POST /api/sessions`
- **THEN** the returned snapshot has `factor_ranking` set to `[]` (empty list)
- **AND** `strategy_signals` set to `null`
- **AND** `data_warnings` contains `"因子和策略信号将在首次调仓时计算"`
- **AND** the response time is under 2 seconds

#### Scenario: step() snapshot includes full computation

- **WHEN** user calls `POST /api/sessions/{id}/step` with orders
- **THEN** the snapshot in the step result includes complete `factor_ranking` and `strategy_signals`
- **AND** factor computation covers all registered factors

#### Scenario: resume() snapshot includes full computation

- **WHEN** user resumes a paused session
- **THEN** the snapshot includes complete `factor_ranking` and `strategy_signals`
- **AND** factor computation covers all registered factors
