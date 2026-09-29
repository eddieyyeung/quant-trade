"""DuckDB schema definitions for the quant platform."""

from pathlib import Path

import duckdb

DUCKDB_MEMORY_LIMIT = "512MB"
DUCKDB_THREADS = 2

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS stock_basic (
    ts_code      VARCHAR PRIMARY KEY,
    name         VARCHAR,
    industry     VARCHAR,
    market       VARCHAR,
    list_date    DATE,
    is_st        BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS trade_calendar (
    trade_date   DATE PRIMARY KEY,
    is_open      BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_kline (
    ts_code      VARCHAR,
    trade_date   DATE,
    open         DOUBLE,
    high         DOUBLE,
    low          DOUBLE,
    close        DOUBLE,
    volume       DOUBLE,
    amount       DOUBLE,
    pct_change   DOUBLE,
    turn_rate    DOUBLE,
    PRIMARY KEY (ts_code, trade_date)
);

CREATE TABLE IF NOT EXISTS index_weights (
    index_code   VARCHAR,
    ts_code      VARCHAR,
    weight       DOUBLE,
    in_date      DATE,
    out_date     DATE,
    PRIMARY KEY (index_code, ts_code, in_date)
);

CREATE TABLE IF NOT EXISTS simulator_session (
    id            VARCHAR PRIMARY KEY,
    name          VARCHAR,
    start_date    DATE,
    end_date      DATE,
    initial_capital DOUBLE,
    cursor_date   DATE,
    portfolio_json TEXT,
    reference_strategy VARCHAR,
    status        VARCHAR DEFAULT 'active',
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS factor_values (
    factor_name  VARCHAR,
    ts_code      VARCHAR,
    trade_date   DATE,
    value        DOUBLE,
    PRIMARY KEY (factor_name, ts_code, trade_date)
);

CREATE TABLE IF NOT EXISTS ic_series (
    factor_name    VARCHAR,
    trade_date     DATE,
    forward_period INTEGER,
    ic             DOUBLE,
    rank_ic        DOUBLE,
    sample_size    INTEGER,
    PRIMARY KEY (factor_name, trade_date, forward_period)
);

CREATE TABLE IF NOT EXISTS financials (
    ts_code      VARCHAR,
    end_date     DATE,
    ann_date     DATE,
    pe           DOUBLE,
    pb           DOUBLE,
    roe          DOUBLE,
    revenue_yoy  DOUBLE,
    profit_yoy   DOUBLE,
    dividend_yield DOUBLE,
    PRIMARY KEY (ts_code, end_date)
);

CREATE TABLE IF NOT EXISTS run (
    run_id          VARCHAR PRIMARY KEY,
    kind            VARCHAR NOT NULL,
    params_json     TEXT NOT NULL,
    status          VARCHAR NOT NULL,
    progress        DOUBLE DEFAULT 0.0,
    message         VARCHAR DEFAULT '',
    trigger         VARCHAR NOT NULL DEFAULT 'manual',
    idempotency_key VARCHAR,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at      TIMESTAMP,
    finished_at     TIMESTAMP,
    error           VARCHAR
);

CREATE TABLE IF NOT EXISTS run_log (
    run_id   VARCHAR NOT NULL,
    seq      INTEGER NOT NULL,
    ts       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    level    VARCHAR NOT NULL DEFAULT 'info',
    message  TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);

CREATE TABLE IF NOT EXISTS artifact (
    artifact_id VARCHAR PRIMARY KEY,
    run_id      VARCHAR NOT NULL,
    kind        VARCHAR NOT NULL,
    storage     VARCHAR NOT NULL,
    ref         VARCHAR NOT NULL,
    row_count   BIGINT,
    meta_json   TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Backtest results, one set of four tables per run. Keyed by ``run_id`` first
-- so a whole run is addressable by a prefix scan. ``benchmark`` is nullable:
-- a missing index day must not delete the strategy's own NAV row.
CREATE TABLE IF NOT EXISTS backtest_nav (
    run_id      VARCHAR,
    trade_date  DATE,
    nav         DOUBLE,
    benchmark   DOUBLE,
    drawdown    DOUBLE,
    PRIMARY KEY (run_id, trade_date)
);

CREATE TABLE IF NOT EXISTS backtest_trade (
    run_id       VARCHAR,
    seq          INTEGER,
    trade_date   DATE,
    action       VARCHAR,
    ts_code      VARCHAR,
    shares       INTEGER,
    price        DOUBLE,
    commission   DOUBLE,
    stamp_duty   DOUBLE,
    transfer_fee DOUBLE,
    PRIMARY KEY (run_id, seq)
);

-- One run's signal orders, shaped exactly like ``backtest_trade``: both hold an
-- ordered list of trade intents belonging to a run, so there is no reason for
-- them to look different. ``strategy`` is duplicated onto every row rather than
-- living in a run-level table — every read path needs it, and recovering it
-- from ``run.params_json`` would mean parsing a payload to render a column.
CREATE TABLE IF NOT EXISTS strategy_signal (
    run_id     VARCHAR,
    seq        INTEGER,
    trade_date DATE,
    strategy   VARCHAR,
    ts_code    VARCHAR,
    direction  VARCHAR,
    target_pct DOUBLE,
    reason     VARCHAR,
    PRIMARY KEY (run_id, seq)
);

-- Key/value rather than fixed columns: a new metric changes the compute
-- function, never the table.
CREATE TABLE IF NOT EXISTS backtest_metric (
    run_id       VARCHAR,
    metric_name  VARCHAR,
    metric_value DOUBLE,
    PRIMARY KEY (run_id, metric_name)
);

CREATE TABLE IF NOT EXISTS backtest_position (
    run_id        VARCHAR,
    ts_code       VARCHAR,
    shares        INTEGER,
    avg_cost      DOUBLE,
    current_price DOUBLE,
    market_value  DOUBLE,
    PRIMARY KEY (run_id, ts_code)
);

-- Model evaluation results, one set of three tables per training run.
-- ``model_ic_series`` is deliberately not the factor-domain ``ic_series``:
-- that one is keyed by ``factor_name`` and a model is not a factor. Storing
-- model IC there would mean encoding the run id into a key name and parsing
-- it back out on every read.
CREATE TABLE IF NOT EXISTS model_ic_series (
    run_id     VARCHAR,
    trade_date DATE,
    rank_ic    DOUBLE,
    PRIMARY KEY (run_id, trade_date)
);

CREATE TABLE IF NOT EXISTS model_feature_importance (
    run_id     VARCHAR,
    factor     VARCHAR,
    importance DOUBLE,
    std        DOUBLE,
    PRIMARY KEY (run_id, factor)
);

-- Key/value rather than fixed columns: a new metric changes the compute
-- function, never the table. Same shape as ``backtest_metric``.
CREATE TABLE IF NOT EXISTS model_metric (
    run_id       VARCHAR,
    metric_name  VARCHAR,
    metric_value DOUBLE,
    PRIMARY KEY (run_id, metric_name)
);
"""

RUN_INDEXES_SQL = """
-- Idempotency is enforced per task type. Empty keys must not block each other,
-- so rows with a NULL key never collide; DuckDB treats NULLs as distinct in a
-- unique index, matching PostgreSQL.
CREATE UNIQUE INDEX IF NOT EXISTS run_kind_idempotency_key
    ON run (kind, idempotency_key);

CREATE INDEX IF NOT EXISTS run_created_at ON run (created_at);
CREATE INDEX IF NOT EXISTS artifact_run_id ON artifact (run_id);
"""
"""Indexes created after the tables — DuckDB rejects them inside the schema batch."""


def init_db(db_path: str) -> duckdb.DuckDBPyConnection:
    """Initialize database schema. Idempotent — uses IF NOT EXISTS. Auto-creates parent dir."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(db_path)
    conn.execute(SCHEMA_SQL)
    conn.execute(RUN_INDEXES_SQL)
    conn.execute(f"SET memory_limit = '{DUCKDB_MEMORY_LIMIT}'")
    conn.execute(f"SET threads = {DUCKDB_THREADS}")
    return conn
