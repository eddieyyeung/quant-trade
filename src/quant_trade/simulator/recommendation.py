"""Turn reference-strategy signals into orders the decision endpoint accepts.

The mapping lives here rather than in the frontend because it carries domain
decisions: the sell entries the snapshot appends for holdings that fell out of
the target portfolio, and the buy-before-sell convention the decision endpoint
already shares with hand-typed submissions.

Kept free of I/O so it can be exercised without a database.
"""

from __future__ import annotations

from quant_trade.simulator.types import OrderRequest, StrategySignalItem


def to_recommended_orders(signals: list[StrategySignalItem] | None) -> list[OrderRequest] | None:
    """Map signals to submit-ready orders, preserving ``None`` as ``None``.

    ``None`` means no reference strategy was configured; ``[]`` means one was
    configured and had nothing to say. Collapsing them would leave the caller
    unable to tell "not set up" from "quiet", which the decision desk shows as
    two different states.

    Buy orders come first. The engine scans sells before buys regardless of
    submission order, so this changes no fill — it keeps a recommendation
    indistinguishable from a hand-typed submission, which orders the same way.
    """
    if signals is None:
        return None

    # Target weights pass through untouched: re-normalising here would quietly
    # disagree with the strategy whose NAV is drawn on the comparison chart.
    return [
        OrderRequest(
            ts_code=signal.ts_code,
            target_pct=signal.target_pct,
            direction=signal.direction,
            reason=signal.reason,
        )
        for direction in ("BUY", "SELL")
        for signal in signals
        if signal.direction == direction
    ]
