"""Factor-domain routes: catalogue, IC series, layered backtest and correlation."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from quant_trade.data.schema import init_db
from quant_trade.data.store import DataStore
from quant_trade.factors.alpha158.storage import save_factor_values
from quant_trade.runtime.app import create_platform_app

START = date(2024, 1, 1)
N_DAYS = 60
CODES = [f"{i:06d}.SZ" for i in range(1, 21)]
FACTORS = ["MA20", "STD20"]
MAX_CORRELATION_FACTORS = 50


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "quant.db"
    conn = init_db(str(path))
    days = [START + timedelta(days=i) for i in range(N_DAYS)]
    kline, values = [], []
    for code in CODES:
        rng = np.random.default_rng(abs(hash(code)) % 2**32)
        closes = 10 + np.cumsum(rng.normal(0, 0.2, N_DAYS))
        for i, day in enumerate(days):
            kline.append((code, day, closes[i], closes[i], closes[i], closes[i], 1e6, 1e7, None, None))
            for name in FACTORS:
                values.append((name, code, day, float(rng.normal())))
    conn.executemany("INSERT INTO daily_kline VALUES (?,?,?,?,?,?,?,?,?,?)", kline)
    conn.executemany("INSERT INTO trade_calendar VALUES (?, TRUE)", [(day,) for day in days])
    conn.close()

    store = DataStore(str(path))
    save_factor_values(store, pd.DataFrame(values, columns=["factor_name", "ts_code", "trade_date", "value"]))
    store.conn.execute(
        "INSERT INTO ic_series VALUES ('MA20', ?, 5, 0.031, 0.028, 20)",
        [START + timedelta(days=1)],
    )
    store.conn.execute(
        "INSERT INTO ic_series VALUES ('MA20', ?, 5, -0.012, -0.009, 20)",
        [START + timedelta(days=2)],
    )
    store.conn.execute(
        "INSERT INTO ic_series VALUES ('MA20', ?, 20, 0.007, 0.005, 20)",
        [START + timedelta(days=1)],
    )
    store.close()
    return path


@pytest.fixture
def client(db_path: Path) -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as tmp:
        config_file = Path(tmp) / "config.yaml"
        config_file.write_text(f"data:\n  db_path: {db_path}\n", encoding="utf-8")
        with TestClient(create_platform_app(str(config_file))) as c:
            yield c


class TestCatalogue:
    def test_lists_persisted_factors_with_coverage(self, client: TestClient) -> None:
        response = client.get("/api/factors")
        assert response.status_code == 200
        body = response.json()
        names = {item["name"] for item in body["items"]}
        assert set(FACTORS) <= names
        ma20 = next(item for item in body["items"] if item["name"] == "MA20")
        assert ma20["persisted"] is True
        assert ma20["rows"] == len(CODES) * N_DAYS
        assert ma20["earliest"] == START.isoformat()

    def test_registered_but_unpersisted_factor_is_marked(self, client: TestClient) -> None:
        """Registry factors that were never written must not claim coverage."""
        body = client.get("/api/factors?limit=200").json()
        manual = [item for item in body["items"] if item["name"] == "momentum_20d"]
        assert manual, "registered factors should still be listed"
        assert manual[0]["persisted"] is False
        assert manual[0]["rows"] == 0
        assert manual[0]["earliest"] is None

    def test_pagination_bounds_the_response(self, client: TestClient) -> None:
        body = client.get("/api/factors?limit=2&offset=1").json()
        assert len(body["items"]) == 2
        assert body["total"] > 2

    def test_category_filter(self, client: TestClient) -> None:
        body = client.get("/api/factors?category=alpha158").json()
        assert body["items"]
        assert {item["category"] for item in body["items"]} == {"alpha158"}

    def test_coverage_endpoint(self, client: TestClient) -> None:
        body = client.get("/api/factors/coverage").json()
        assert {item["name"] for item in body["items"]} == set(FACTORS)


class TestICSeries:
    def test_reads_series_and_summary(self, client: TestClient) -> None:
        body = client.get("/api/factors/ic?factor=MA20&forward_period=5").json()
        assert body["count"] == 2
        assert [point["ic"] for point in body["series"]] == pytest.approx([0.031, -0.012])
        assert body["summary"]["ic_mean"] == pytest.approx(0.0095)
        assert body["summary"]["ic_positive_ratio"] == pytest.approx(0.5)

    def test_series_is_ascending_by_date(self, client: TestClient) -> None:
        body = client.get("/api/factors/ic?factor=MA20&forward_period=5").json()
        dates = [point["trade_date"] for point in body["series"]]
        assert dates == sorted(dates)

    def test_unknown_factor_returns_empty_not_error(self, client: TestClient) -> None:
        body = client.get("/api/factors/ic?factor=NOPE").json()
        assert body["count"] == 0
        assert body["series"] == []
        assert body["summary"]["ic_mean"] is None

    def test_decay_lists_every_holding_period(self, client: TestClient) -> None:
        body = client.get("/api/factors/ic/decay?factor=MA20").json()
        periods = [item["forward_period"] for item in body["items"]]
        assert periods == [5, 20]
        five = next(item for item in body["items"] if item["forward_period"] == 5)
        assert five["count"] == 2


class TestQuantile:
    def test_returns_groups_and_long_short(self, client: TestClient) -> None:
        body = client.get("/api/factors/quantile?factor=MA20&n_groups=5").json()
        assert body["n_groups"] == 5
        assert len(body["groups"]) == 5
        assert body["long_short"] is not None
        assert body["long_short"]["name"] == "long_short"
        assert body["rebalance_count"] > 0
        for series in body["groups"]:
            assert len(series["dates"]) == len(series["values"])
            assert series["values"][0] == pytest.approx(1.0)

    def test_unknown_factor_returns_empty_groups(self, client: TestClient) -> None:
        body = client.get("/api/factors/quantile?factor=NOPE").json()
        assert body["groups"] == []
        assert body["long_short"] is None

    def test_inverted_range_is_a_client_error(self, client: TestClient) -> None:
        response = client.get("/api/factors/quantile?factor=MA20&start=2024-05-01&end=2024-01-01")
        assert response.status_code == 422


class TestCorrelation:
    def test_returns_square_matrix(self, client: TestClient) -> None:
        response = client.get("/api/factors/correlation", params={"factors": FACTORS})
        assert response.status_code == 200
        body = response.json()
        assert body["factors"] == FACTORS
        assert len(body["matrix"]) == 2
        assert body["matrix"][0][0] == pytest.approx(1.0)
        assert body["date_count"] > 0

    def test_too_many_factors_is_a_client_error(self, client: TestClient) -> None:
        many = [f"F{i}" for i in range(MAX_CORRELATION_FACTORS + 1)]
        response = client.get("/api/factors/correlation", params={"factors": many})
        assert response.status_code == 422

    def test_missing_factors_param_is_rejected(self, client: TestClient) -> None:
        assert client.get("/api/factors/correlation").status_code == 422


class TestRouting:
    def test_factor_paths_do_not_fall_through_to_the_spa(self, client: TestClient) -> None:
        """The SPA catch-all must not swallow API paths."""
        response = client.get("/api/factors")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")

    def test_router_exposes_no_write_endpoint(self, client: TestClient) -> None:
        """Computing factors is a run; the read router has no POST."""
        assert client.post("/api/factors").status_code == 405
