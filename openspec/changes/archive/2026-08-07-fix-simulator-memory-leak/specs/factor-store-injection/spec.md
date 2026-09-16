## ADDED Requirements

### Requirement: FactorRegistry.get accepts optional store parameter

`FactorRegistry.get(name, store=None)` SHALL accept an optional `store` parameter of type `DataStore | None`. When a `store` is provided, the returned Factor instance MUST use that DataStore instead of creating a new one.

#### Scenario: get with store injected

- **WHEN** caller invokes `factor_registry.get("momentum_20d", store=shared_store)`
- **THEN** the returned Factor instance's internal `store` attribute SHALL reference `shared_store`
- **AND** no new DuckDB connection SHALL be created by that Factor instance

#### Scenario: get without store preserves backward compatibility

- **WHEN** caller invokes `factor_registry.get("momentum_20d")` without a store argument
- **THEN** the returned Factor instance SHALL create its own DataStore internally, matching existing behavior

### Requirement: Factor compute uses injected store

When a Factor instance is created with an injected DataStore, all `compute()` method calls SHALL use that store for data queries, producing mathematically identical results to using a standalone DataStore.

#### Scenario: compute result equivalence

- **WHEN** `Factor.compute(date, universe)` is called on an instance with injected store `S1`
- **THEN** the returned pd.Series SHALL be equal to the result from a standalone instance using a separate `DataStore()` connected to the same database file

### Requirement: SnapshotBuilder passes shared store to factor registry

`SnapshotBuilder._build_factor_ranking()` SHALL pass `self._store` to `factor_registry.get()` when building factor rankings.

#### Scenario: single DuckDB connection during snapshot build

- **WHEN** `SnapshotBuilder.build_snapshot()` is invoked
- **THEN** all factor computations within that invocation SHALL share the SnapshotBuilder's DataStore connection
- **AND** the total number of DuckDB connections created during one snapshot build SHALL not exceed 1 (excluding any pre-existing connections)
