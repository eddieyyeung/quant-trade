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
"""


def init_db(db_path: str) -> duckdb.DuckDBPyConnection:
    """Initialize database schema. Idempotent — uses IF NOT EXISTS. Auto-creates parent dir."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(db_path)
    conn.execute(SCHEMA_SQL)
    conn.execute(f"SET memory_limit = '{DUCKDB_MEMORY_LIMIT}'")
    conn.execute(f"SET threads = {DUCKDB_THREADS}")
    return conn
