"""Virtual portfolio management for backtesting and paper trading."""

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Optional

from loguru import logger


@dataclass
class Holding:
    """A single stock holding."""

    ts_code: str
    shares: int
    avg_cost: float  # Average cost per share
    current_price: float = 0.0
    buy_date: date | None = None  # Most recent buy date (for T+1 check)


@dataclass
class Portfolio:
    """Virtual portfolio tracking cash, holdings, and NAV."""

    cash: float
    holdings: dict[str, Holding] = field(default_factory=dict)
    initial_capital: float = 0.0
    trade_log: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.initial_capital == 0.0:
            self.initial_capital = self.cash

    @property
    def total_value(self) -> float:
        """NAV = cash + market value of all holdings."""
        mv = sum(h.current_price * h.shares for h in self.holdings.values())
        return self.cash + mv

    @property
    def total_return(self) -> float:
        """Cumulative return since inception."""
        if self.initial_capital <= 0:
            return 0.0
        return (self.total_value / self.initial_capital) - 1.0

    def update_prices(self, prices: dict[str, float]) -> None:
        """Update current prices for all holdings."""
        for code, price in prices.items():
            if code in self.holdings:
                self.holdings[code].current_price = price

    def can_sell(self, code: str, trade_date: date) -> bool:
        """Check T+1: can only sell if bought before today."""
        if code not in self.holdings:
            return False
        h = self.holdings[code]
        if h.buy_date is None:
            return True
        return h.buy_date < trade_date

    def buy(
        self,
        code: str,
        price: float,
        amount: float,  # Target yuan amount (before fees)
        trade_date: date,
        commission_rate: float = 0.00025,
        min_commission: float = 5.0,
        transfer_fee_rate: float = 0.00001,
    ) -> tuple[int, float]:
        """
        Buy stock. Returns (shares_bought, total_cost).

        A-shares: minimum 100 shares, integer multiples.
        """
        commission = max(amount * commission_rate, min_commission)
        transfer_fee = amount * transfer_fee_rate
        total_cost = amount + commission + transfer_fee

        if total_cost > self.cash:
            # Adjust: buy what we can afford
            amount = self.cash - commission - transfer_fee
            if amount <= 0:
                return 0, 0.0
            total_cost = amount + commission + transfer_fee

        # Round down to nearest 100 shares
        shares = int(amount / price / 100) * 100
        if shares == 0:
            return 0, 0.0

        actual_amount = shares * price
        actual_commission = max(actual_amount * commission_rate, min_commission)
        actual_transfer = actual_amount * transfer_fee_rate
        actual_total_cost = actual_amount + actual_commission + actual_transfer

        if actual_total_cost > self.cash:
            return 0, 0.0

        self.cash -= actual_total_cost

        if code in self.holdings:
            h = self.holdings[code]
            total_shares = h.shares + shares
            h.avg_cost = (h.avg_cost * h.shares + price * shares) / total_shares
            h.shares = total_shares
            h.buy_date = trade_date
            h.current_price = price
        else:
            self.holdings[code] = Holding(
                ts_code=code,
                shares=shares,
                avg_cost=price,
                current_price=price,
                buy_date=trade_date,
            )

        self.trade_log.append(
            {
                "date": str(trade_date),
                "action": "BUY",
                "ts_code": code,
                "shares": shares,
                "price": price,
                "commission": actual_commission,
                "transfer_fee": actual_transfer,
            }
        )
        return shares, actual_total_cost

    def sell(
        self,
        code: str,
        price: float,
        shares: int | None = None,
        trade_date: date | None = None,
        commission_rate: float = 0.00025,
        min_commission: float = 5.0,
        stamp_duty_rate: float = 0.0005,
        transfer_fee_rate: float = 0.00001,
    ) -> tuple[int, float]:
        """
        Sell stock. Returns (shares_sold, proceeds_after_fees).

        If shares is None, sell all.
        """
        if code not in self.holdings:
            return 0, 0.0

        h = self.holdings[code]
        sell_shares = shares if shares is not None else h.shares
        sell_shares = min(sell_shares, h.shares)

        amount = sell_shares * price
        commission = max(amount * commission_rate, min_commission)
        stamp_duty = amount * stamp_duty_rate
        transfer_fee = amount * transfer_fee_rate
        proceeds = amount - commission - stamp_duty - transfer_fee

        self.cash += proceeds
        h.shares -= sell_shares

        if h.shares == 0:
            del self.holdings[code]
        elif h.shares < 0:
            h.shares = 0

        self.trade_log.append(
            {
                "date": str(trade_date) if trade_date else "",
                "action": "SELL",
                "ts_code": code,
                "shares": sell_shares,
                "price": price,
                "commission": commission,
                "stamp_duty": stamp_duty,
                "transfer_fee": transfer_fee,
            }
        )
        return sell_shares, proceeds

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON persistence."""
        return {
            "cash": self.cash,
            "initial_capital": self.initial_capital,
            "holdings": {
                code: {
                    "ts_code": h.ts_code,
                    "shares": h.shares,
                    "avg_cost": h.avg_cost,
                    "current_price": h.current_price,
                    "buy_date": str(h.buy_date) if h.buy_date else None,
                }
                for code, h in self.holdings.items()
            },
            "trade_log": self.trade_log[-100:],  # Keep last 100 trades
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Portfolio":
        """Deserialize from dict."""
        p = cls(cash=d["cash"], initial_capital=d.get("initial_capital", d["cash"]))
        for code, hd in d.get("holdings", {}).items():
            buy_date = date.fromisoformat(hd["buy_date"]) if hd.get("buy_date") else None
            p.holdings[code] = Holding(
                ts_code=hd["ts_code"],
                shares=hd["shares"],
                avg_cost=hd["avg_cost"],
                current_price=hd.get("current_price", 0.0),
                buy_date=buy_date,
            )
        p.trade_log = d.get("trade_log", [])
        return p

    def save(self, path: Path) -> None:
        """Persist portfolio to JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False))
        logger.info(f"Portfolio saved to {path}")

    @classmethod
    def load(cls, path: Path) -> Optional["Portfolio"]:
        """Load portfolio from JSON file. Returns None if file doesn't exist."""
        if not path.exists():
            return None
        try:
            d = json.loads(path.read_text())
            return cls.from_dict(d)
        except Exception as e:
            logger.error(f"Failed to load portfolio from {path}: {e}")
            return None
