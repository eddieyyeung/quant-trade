"""Application configuration loaded from environment and YAML files."""

import os
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()


class DataConfig(BaseModel):
    """Data source configuration."""

    db_path: str = "data/quant.db"
    cache_dir: str = "data/cache"
    primary_source: str = "akshare"
    backup_sources: list[str] = Field(default_factory=lambda: ["tushare"])


class FactorConfig(BaseModel):
    """Factor computation configuration."""

    enabled: list[str] = Field(
        default_factory=lambda: [
            "momentum_20d",
            "momentum_60d",
            "ma_deviation",
            "pb_ratio",
            "pe_ratio",
            "dividend_yield",
            "roe_ttm",
            "revenue_yoy",
        ]
    )
    params: dict[str, dict[str, Any]] = Field(default_factory=dict)


class StrategyConfig(BaseModel):
    """Strategy configuration."""

    name: str = "factor_ranking"
    top_n: int = 15
    rebalance_freq: str = "weekly"
    max_industry_weight: float = 0.30
    factor_weights: dict[str, float] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)


class BacktestConfig(BaseModel):
    """Backtest engine configuration."""

    initial_capital: float = 100_000
    commission_rate: float = 0.00025
    min_commission: float = 5.0
    stamp_duty_rate: float = 0.0005  # sell only
    transfer_fee_rate: float = 0.00001
    start_date: date = date(2015, 1, 1)
    benchmark: str = "000300.SH"


class ReportConfig(BaseModel):
    """Weekly report configuration."""

    output_dir: str = "reports"
    template: str = "weekly_report.html.j2"


class AppConfig(BaseModel):
    """Root application configuration."""

    data: DataConfig = Field(default_factory=DataConfig)
    factor: FactorConfig = Field(default_factory=FactorConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)
    report: ReportConfig = Field(default_factory=ReportConfig)

    @classmethod
    def from_yaml(cls, path: Path | str) -> "AppConfig":
        """Load configuration from a YAML file, overriding with env vars."""
        with open(path) as f:
            raw: dict[str, Any] = yaml.safe_load(f)
        return cls(**raw)


def get_config_path() -> Path:
    """Resolve config path: env var QUANT_CONFIG or default."""
    env_path = os.getenv("QUANT_CONFIG")
    if env_path:
        return Path(env_path)
    return Path("config/default.yaml")


DEFAULT_CONFIG_PATH = get_config_path()
