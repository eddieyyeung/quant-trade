"""Multi-factor ranking strategy — equal-weight top-N selection."""

from datetime import date

import pandas as pd
from loguru import logger

from quant_trade.data.store import DataStore
from quant_trade.factors.preprocess import preprocess
from quant_trade.factors.registry import registry as factor_registry
from quant_trade.strategies.base import Order, SignalResult, Strategy
from quant_trade.strategies.registry import register_strategy


@register_strategy("factor_ranking")
class FactorRankingStrategy(Strategy):
    """
    Multi-factor equal-weight composite ranking strategy.

    1. Compute all enabled factors for the universe
    2. Preprocess each factor (winsorize + standardize)
    3. Combine into a composite score (weighted sum)
    4. Select top-N stocks at equal weight
    5. Apply industry concentration constraint
    """

    name = "factor_ranking"

    def __init__(
        self,
        enabled_factors: list[str] | None = None,
        top_n: int = 15,
        factor_weights: dict[str, float] | None = None,
        max_industry_weight: float = 0.30,
        store: DataStore | None = None,
    ):
        self.enabled_factors = enabled_factors or [
            "momentum_20d",
            "momentum_60d",
            "ma_deviation",
            "pb_ratio",
            "pe_ratio",
            "dividend_yield",
            "roe_ttm",
            "revenue_yoy",
        ]
        self.top_n = top_n
        self.factor_weights = factor_weights or {}
        self.max_industry_weight = max_industry_weight
        self.store = store or DataStore()

    def generate_signals(
        self,
        date: date,
        universe: list[str],
        data: DataStore | None = None,
    ) -> SignalResult:
        store = data or self.store

        # Step 1: Compute all factor values
        factor_scores: dict[str, pd.Series] = {}
        for fname in self.enabled_factors:
            factor = factor_registry.get(fname)
            if factor is None:
                logger.warning(f"Factor {fname} not found in registry, skipping")
                continue
            try:
                raw = factor.compute(date, universe)
                # Standardize: winsorize + z-score
                processed = preprocess(raw, steps=["winsorize", "standardize"])
                factor_scores[fname] = processed
            except Exception as e:
                logger.warning(f"Factor {fname} computation failed: {e}")

        if not factor_scores:
            return SignalResult()

        # Step 2: Combine into composite score
        default_weight = 1.0 / len(factor_scores) if factor_scores else 0.0

        # Align all factors to the universe index.
        score_df = pd.DataFrame({fname: scores.reindex(universe) for fname, scores in factor_scores.items()})
        # Drop stocks with no usable data across ALL factors — a neutral 0 score
        # would otherwise let data-missing names outrank genuinely negative ones.
        score_df = score_df.dropna(how="all")
        if score_df.empty:
            return SignalResult()

        composite = pd.Series(0.0, index=score_df.index)
        for fname in score_df.columns:
            w = self.factor_weights.get(fname, default_weight)
            # Missing values for a single factor contribute 0 (neutral), but the
            # stock itself must still have data for at least one factor.
            composite = composite.add(score_df[fname].fillna(0) * w, fill_value=0)

        # Step 3: Rank and pick top-N
        ranked = composite.sort_values(ascending=False)
        selected = ranked.head(self.top_n)

        # Step 4: Industry concentration constraint (best-effort)
        if self.max_industry_weight < 1.0:
            selected = self._apply_industry_constraint(selected, store)

        # Step 5: Build equal-weight orders
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
                    reason=f"综合得分 {score:.2f}",
                )
            )

        return SignalResult(orders=orders, weights=weights)

    def _apply_industry_constraint(
        self,
        selected: pd.Series,
        store: DataStore,
    ) -> pd.Series:
        """Cap single-industry exposure at max_industry_weight."""
        max_count = max(1, int(self.top_n * self.max_industry_weight))

        # Get industry for each stock
        codes = selected.index.tolist()
        placeholders = ", ".join(["?"] * len(codes))
        sql = f"""
            SELECT ts_code, industry FROM stock_basic
            WHERE ts_code IN ({placeholders})
        """
        try:
            ind_df = store.conn.execute(sql, codes).df()
            ind_map = dict(zip(ind_df["ts_code"], ind_df["industry"], strict=False))
        except Exception:
            return selected

        # Count per industry
        industry_counts: dict[str, list[str]] = {}
        for code in codes:
            ind = ind_map.get(code, "未知")
            if ind not in industry_counts:
                industry_counts[ind] = []
            industry_counts[ind].append(code)

        # Filter: keep only top max_count per industry (by score order)
        keep: list[str] = []
        for _ind, ind_codes in industry_counts.items():
            # Sort by selection order (which is score order)
            sorted_codes = [c for c in codes if c in ind_codes][:max_count]
            keep.extend(sorted_codes)

        # If we dropped some, backfill from the full ranked list
        # For simplicity, just cap — the effect is fewer stocks if one industry dominates
        return selected[selected.index.isin(keep)]
