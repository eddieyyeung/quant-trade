"""Tests for the Alpha158 factor library."""

import tempfile
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from quant_trade.data.schema import init_db
from quant_trade.data.store import DataStore
from quant_trade.factors.alpha158 import ALPHA158_NAMES, compute_alpha158, get_factor_values, save_factor_values
from quant_trade.factors.alpha158.bridge import Alpha158Factor
from quant_trade.factors.alpha158.templates import all_factors, kbar_factors, price_factors, volume_factors

CODES = ["000001.SZ", "600000.SH", "300001.SZ"]


def _build_synthetic(db_path: str, n_days: int = 150, vol_vary: bool = True) -> DataStore:
    """Synthetic kline: 3 stocks, ``n_days`` consecutive calendar days."""
    conn = init_db(db_path)
    rows = []
    for c in CODES:
        rng = np.random.default_rng(hash(c) % 2**32)
        closes = 10 + np.cumsum(rng.normal(0, 0.2, n_days))
        vols = rng.uniform(5e5, 2e6, n_days) if vol_vary else np.full(n_days, 1e6)
        for i in range(n_days):
            d = date(2024, 1, 1) + timedelta(days=i)
            rows.append(
                (c, d, closes[i] * 0.99, closes[i] * 1.01, closes[i] * 0.98, closes[i], vols[i], 1e7, None, None)
            )
    conn.executemany("INSERT INTO daily_kline VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    return DataStore(db_path)


class TestFactorCatalog:
    def test_158_factors_and_groups(self) -> None:
        factors = all_factors()
        assert len(factors) == 158
        assert len(kbar_factors()) == 9
        assert len(price_factors()) == 4
        assert len(volume_factors()) == 5  # volume group excluded from default set
        assert len(ALPHA158_NAMES) == 158
        assert len(set(ALPHA158_NAMES)) == 158  # no duplicate names

    def test_window_coverage(self) -> None:
        for w in [5, 10, 20, 30, 60]:
            assert f"MA{w}" in ALPHA158_NAMES
            assert f"RSV{w}" in ALPHA158_NAMES


class TestCompute:
    def test_compute_covers_all_factors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _build_synthetic(tmp + "/a.db")
            out = compute_alpha158(store, date(2024, 2, 1), date(2024, 3, 31), CODES)
            assert out["factor_name"].nunique() == 158
            assert set(out["factor_name"].unique()) == set(ALPHA158_NAMES)
            assert {"factor_name", "ts_code", "trade_date", "value"} <= set(out.columns)

    def test_values_match_reference(self) -> None:
        """MA/rank/regression/volume templates vs pandas+scipy references."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _build_synthetic(tmp + "/a.db")
            out = compute_alpha158(store, date(2024, 2, 1), date(2024, 3, 31), CODES)
            k = store.conn.execute(
                "SELECT close, volume, high, low FROM daily_kline WHERE ts_code='000001.SZ' ORDER BY trade_date"
            ).df()

            def fv(name: str, j: int) -> float:
                sub = out[(out.ts_code == "000001.SZ") & (out.factor_name == name)].sort_values("trade_date")
                return float(sub.iloc[j]["value"])

            j = 30
            i = 31 + j  # output starts 2024-02-01 == raw index 31
            y = k["close"].iloc[i - 19 : i + 1].values
            # MA20: mean(close)/close
            assert fv("MA20", j) == pytest.approx(k["close"].iloc[i - 19 : i + 1].mean() / k["close"].iloc[i], rel=1e-9)
            # BETA20: linregress slope / close
            lr = stats.linregress(np.arange(20.0), y)
            assert fv("BETA20", j) == pytest.approx(lr.slope / k["close"].iloc[i], rel=1e-9)
            # RSQR20 / RESI20
            assert fv("RSQR20", j) == pytest.approx(lr.rvalue**2, rel=1e-9)
            resid = y - (lr.intercept + lr.slope * np.arange(20.0))
            assert fv("RESI20", j) == pytest.approx(resid.std(ddof=1) / k["close"].iloc[i], rel=1e-9)
            # CORR20
            ref = np.corrcoef(y, np.log(k["volume"].iloc[i - 19 : i + 1].values + 1))[0, 1]
            assert fv("CORR20", j) == pytest.approx(ref, rel=1e-9)
            # RANK20: rankdata(y)[-1] / 20
            assert fv("RANK20", j) == pytest.approx(stats.rankdata(y)[-1] / 20, rel=1e-9)
            # RSV20 uses high/low
            lo = k["low"].iloc[i - 19 : i + 1].min()
            hi = k["high"].iloc[i - 19 : i + 1].max()
            assert fv("RSV20", j) == pytest.approx((k["close"].iloc[i] - lo) / (hi - lo + 1e-12), rel=1e-9)
            # KMID: (close-open)/open — synthetic open = close*0.99 → constant
            assert fv("KMID", j) == pytest.approx(0.01 / 0.99, rel=1e-9)

    def test_vwap_zero_amount_is_nan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _build_synthetic(tmp + "/a.db")
            store.conn.execute(
                "UPDATE daily_kline SET amount = 0 WHERE ts_code='000001.SZ' AND trade_date='2024-02-15'"
            )
            out = compute_alpha158(store, date(2024, 2, 10), date(2024, 2, 20), CODES)
            vwap = out[
                (out.ts_code == "000001.SZ") & (out.factor_name == "VWAP0") & (out.trade_date == date(2024, 2, 15))
            ]
            assert vwap.empty  # NaN dropped


class TestStorage:
    def test_roundtrip_and_incremental(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _build_synthetic(tmp + "/a.db")
            out = compute_alpha158(store, date(2024, 2, 1), date(2024, 3, 31), CODES)
            n = save_factor_values(store, out)
            assert n == len(out)

            wide = get_factor_values(store, ["MA20", "KMID"], CODES, date(2024, 2, 1), date(2024, 3, 31), wide=True)
            assert set(wide.columns) >= {"ts_code", "trade_date", "MA20", "KMID"}
            assert len(wide) > 0

            # Re-saving an overlapping slice must not duplicate rows
            save_factor_values(store, out.iloc[:100])
            cnt = store.conn.execute("SELECT COUNT(*) FROM factor_values").fetchone()[0]
            assert cnt == n


class TestBridge:
    def test_bridge_reads_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _build_synthetic(tmp + "/a.db")
            out = compute_alpha158(store, date(2024, 2, 1), date(2024, 3, 31), CODES)
            save_factor_values(store, out)

            f = Alpha158Factor("MA20", store=store)
            vals = f.compute(date(2024, 3, 29), CODES)
            assert isinstance(vals, pd.Series)
            assert len(vals) == 3
            assert f.name == "MA20"
            # The three values above are not evidence the *fixture* was read:
            # the repository's own `data/quant.db` holds these same codes on
            # this same date, so a bridge that ignored the injected store would
            # return three values too. Only the identity pins the source.
            assert f.store is store

    def test_bridge_rejects_unknown(self) -> None:
        with pytest.raises(ValueError, match="Unknown Alpha158 factor"):
            Alpha158Factor("NOT_A_FACTOR")

    def test_bridge_ic_analysis(self) -> None:
        """Alpha158Factor works end-to-end with compute_ic_series."""
        from quant_trade.factors.analysis import compute_ic_series

        with tempfile.TemporaryDirectory() as tmp:
            # IC requires >= 10 aligned stocks; use 12 codes
            codes = [f"{i:06d}.SZ" for i in range(12)]
            conn = init_db(tmp + "/ic.db")
            rows = []
            for c in codes:
                rng = np.random.default_rng(hash(c) % 2**32)
                closes = 10 + np.cumsum(rng.normal(0, 0.2, 60))
                for i in range(60):
                    d = date(2024, 1, 1) + timedelta(days=i)
                    rows.append((c, d, closes[i], closes[i], closes[i], closes[i], 1e6, 1e7, None, None))
            conn.executemany("INSERT INTO daily_kline VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
            store = DataStore(tmp + "/ic.db")

            f = Alpha158Factor("MA20", store=store)
            result = compute_ic_series(
                store,
                "MA20",
                lambda d, u: f.compute(d, u),
                codes,
                [date(2024, 2, 1)],
                forward_period=5,
            )
            assert isinstance(result["ic_mean"], float)
            assert isinstance(result["ic_series"], list)
            assert len(result["ic_series"]) == 1
