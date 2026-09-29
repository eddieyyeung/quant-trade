"""Model-driven stock selection strategy (ML ranking)."""

from datetime import date
from pathlib import Path

import pandas as pd
from loguru import logger

from quant_trade.data.store import DataStore
from quant_trade.models.train import load_predictions
from quant_trade.strategies.base import Order, SignalResult, Strategy
from quant_trade.strategies.registry import register_strategy


@register_strategy("model_ranking")
class ModelStrategy(Strategy):
    """Select top-N stocks by model prediction score, equal weight.

    Prediction scores are loaded from a persisted parquet produced by
    :func:`quant_trade.services.models.train_model`. Dates without predictions
    return an empty signal (no rebalance).
    """

    name = "model_ranking"

    def __init__(
        self,
        predictions_path: str | Path = "data/predictions/model_ranking.parquet",
        top_n: int = 15,
        max_industry_weight: float = 0.30,
        store: DataStore | None = None,
    ):
        self.predictions_path = predictions_path
        self.top_n = top_n
        self.max_industry_weight = max_industry_weight
        # Narrowed to non-optional: this strategy always ends up with a store,
        # whether the caller supplied one or not.
        self.store: DataStore = store or DataStore()
        self._predictions: pd.DataFrame | None = None

    @property
    def predictions(self) -> pd.DataFrame:
        if self._predictions is None:
            self._predictions = load_predictions(self.predictions_path)
        return self._predictions

    def generate_signals(
        self,
        date: date,
        universe: list[str],
        data: DataStore | None = None,
    ) -> SignalResult:
        store = data or self.store
        pred = self.predictions
        if pred.empty:
            logger.warning(f"model_ranking: no predictions loaded from {self.predictions_path}")
            return SignalResult()

        day = pred[(pred["trade_date"] == date) & (pred["ts_code"].isin(universe))]
        if day.empty:
            logger.info(f"model_ranking: no predictions for {date}, skipping rebalance")
            return SignalResult()

        ranked = day.sort_values("score", ascending=False).head(self.top_n)
        selected = pd.Series(ranked["score"].values, index=ranked["ts_code"].values)

        if self.max_industry_weight < 1.0:
            selected = self._apply_industry_constraint(selected, store)

        n = len(selected)
        if n == 0:
            return SignalResult()

        weight_per_stock = 1.0 / n
        orders: list[Order] = []
        weights: dict[str, float] = {}
        for code, score in selected.items():
            code_str = str(code)
            weights[code_str] = weight_per_stock
            orders.append(
                Order(
                    ts_code=code_str,
                    target_pct=weight_per_stock,
                    direction="BUY",
                    reason=f"模型预测分 {score:.4f}",
                )
            )
        return SignalResult(orders=orders, weights=weights)

    def _apply_industry_constraint(
        self,
        selected: pd.Series,
        store: DataStore,
    ) -> pd.Series:
        """Cap single-industry exposure at max_industry_weight (best-effort)."""
        max_count = max(1, int(self.top_n * self.max_industry_weight))
        codes = selected.index.tolist()
        placeholders = ", ".join(["?"] * len(codes))
        sql = f"""
            SELECT ts_code, industry FROM stock_basic
            WHERE ts_code IN ({placeholders})
        """
        try:
            info = store.conn.execute(sql, codes).df()
        except Exception as e:
            logger.warning(f"industry lookup failed: {e}")
            return selected

        ind_map = dict(zip(info["ts_code"], info["industry"], strict=False))
        kept: list[str] = []
        counts: dict[str, int] = {}
        for code in selected.index:
            ind = ind_map.get(code, "未知")
            if counts.get(ind, 0) >= max_count:
                continue
            kept.append(code)
            counts[ind] = counts.get(ind, 0) + 1
        return selected[selected.index.isin(kept)]
