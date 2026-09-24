"""Platform runtime: health probe, frontend hosting, and argument handling."""

from __future__ import annotations

import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quant_trade.__main__ import DEFAULT_HOST, DEFAULT_PORT, main, parse_args
from quant_trade.config import AppConfig
from quant_trade.runtime.app import _probe_database, create_platform_app


def _config_file(tmp_dir: str, db_path: Path) -> str:
    """Write a minimal config pointing at ``db_path``."""
    path = Path(tmp_dir) / "config.yaml"
    path.write_text(f"data:\n  db_path: {db_path}\n", encoding="utf-8")
    return str(path)


@pytest.fixture
def dist_dir() -> Iterator[Path]:
    """A stand-in for ``web/dist`` with one page and one asset."""
    with tempfile.TemporaryDirectory() as tmp:
        dist = Path(tmp) / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text("<!doctype html><title>SPA</title>", encoding="utf-8")
        (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
        yield dist


@pytest.fixture
def client(dist_dir: Path) -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as tmp:
        config = _config_file(tmp, Path(tmp) / "quant.db")
        with TestClient(create_platform_app(config, dist=dist_dir)) as c:
            yield c


class TestHealth:
    def test_healthy(self, client: TestClient) -> None:
        body = client.get("/api/health").json()
        assert body["status"] == "ok"
        assert body["db_reachable"] is True
        assert body["db_path"].endswith("quant.db")

    def test_probe_reports_failure_for_unusable_path(self, tmp_path: Path) -> None:
        """The probe must report a failure rather than raise."""
        blocking = tmp_path / "quant.db"
        blocking.mkdir()  # a directory cannot be opened as a DuckDB file

        config = AppConfig()
        config.data.db_path = str(blocking)
        reachable, detail = _probe_database(config)

        assert reachable is False
        assert detail

    def test_app_fails_fast_when_database_cannot_open(self, dist_dir: Path) -> None:
        """An unusable database surfaces at startup, not as a silently degraded service."""
        with tempfile.TemporaryDirectory() as tmp:
            blocking = Path(tmp) / "quant.db"
            blocking.mkdir()
            config = _config_file(tmp, blocking)
            with pytest.raises(Exception, match="Is a directory"):
                create_platform_app(config, dist=dist_dir)


class TestFrontendHosting:
    def test_serves_index(self, client: TestClient) -> None:
        response = client.get("/")
        assert response.status_code == 200
        assert "SPA" in response.text

    def test_serves_static_asset(self, client: TestClient) -> None:
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        assert "console.log" in response.text

    def test_spa_route_falls_back_to_index(self, client: TestClient) -> None:
        response = client.get("/factors/ic")
        assert response.status_code == 200
        assert "SPA" in response.text

    def test_unknown_api_path_is_not_swallowed(self, client: TestClient) -> None:
        response = client.get("/api/does-not-exist")
        assert response.status_code == 404
        assert "SPA" not in response.text

    def test_missing_dist_explains_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _config_file(tmp, Path(tmp) / "quant.db")
            empty = Path(tmp) / "no-dist"
            with TestClient(create_platform_app(config, dist=empty)) as c:
                page = c.get("/")
                assert page.status_code == 200
                assert "npm run build" in page.text
                # The API must stay usable with no frontend built.
                assert c.get("/api/health").status_code == 200

    def test_path_traversal_does_not_escape_dist(self, client: TestClient) -> None:
        response = client.get("/../../etc/passwd")
        assert response.status_code in (200, 404)
        assert "root:" not in response.text


class TestSimulatorStillMounted:
    def test_session_list_reachable(self, client: TestClient) -> None:
        response = client.get("/api/sessions")
        assert response.status_code == 200
        assert isinstance(response.json(), list)


class TestArgumentParsing:
    def test_defaults(self) -> None:
        opts = parse_args([])
        assert (opts.host, opts.port, opts.reload) == (DEFAULT_HOST, DEFAULT_PORT, False)
        assert opts.unknown == []

    def test_overrides(self) -> None:
        opts = parse_args(["--host", "0.0.0.0", "--port", "9000", "--reload"])
        assert (opts.host, opts.port, opts.reload) == ("0.0.0.0", 9000, True)

    def test_equals_form(self) -> None:
        opts = parse_args(["--port=9001", "--host=127.0.0.1"])
        assert (opts.host, opts.port) == ("127.0.0.1", 9001)

    def test_default_host_is_loopback(self) -> None:
        # No authentication exists, so the default must not expose the platform
        # to the network. Binding wider has to be an explicit choice.
        assert DEFAULT_HOST == "127.0.0.1"

    def test_subcommands_are_collected(self) -> None:
        opts = parse_args(["data", "sync"])
        assert opts.unknown == ["data", "sync"]

    def test_subcommand_exits_nonzero(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["data", "sync"]) == 2
        captured = capsys.readouterr()
        assert "不接受子命令" in captured.err
        assert "data sync" in captured.err

    def test_bad_port_is_rejected(self) -> None:
        opts = parse_args(["--port", "not-a-number"])
        assert opts.port == DEFAULT_PORT
        assert opts.unknown == ["--port=not-a-number"]

    def test_reload_uses_the_uvicorn_factory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Reload needs an import string, not a prebuilt app object."""
        calls: list[dict[str, object]] = []
        monkeypatch.setitem(sys.modules, "uvicorn", _FakeUvicorn(calls))

        assert main(["--reload", "--host", "127.0.0.1", "--port", "9"]) == 0
        assert calls[0]["reload"] is True
        assert calls[0]["factory"] is True
        assert calls[0]["app"] == "quant_trade.runtime.app:create_platform_app"

    def test_without_reload_builds_the_app_directly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[dict[str, object]] = []
        monkeypatch.setitem(sys.modules, "uvicorn", _FakeUvicorn(calls))
        sentinel = object()
        monkeypatch.setattr("quant_trade.runtime.app.create_platform_app", lambda *a, **k: sentinel)

        assert main([]) == 0
        assert calls[0]["app"] is sentinel
        assert "reload" not in calls[0]

    def test_main_returns_zero_when_not_launched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A bare invocation reaches uvicorn; stub it so the test stays fast."""
        calls: list[dict[str, object]] = []
        monkeypatch.setitem(sys.modules, "uvicorn", _FakeUvicorn(calls))
        monkeypatch.setattr("quant_trade.runtime.app.create_platform_app", lambda *a, **k: object())

        assert main(["--port", "9999"]) == 0
        assert calls and calls[0]["port"] == 9999


class _FakeUvicorn:
    """Minimal stand-in for the uvicorn module."""

    def __init__(self, calls: list[dict[str, object]]) -> None:
        self._calls = calls

    def run(self, app: object, **kwargs: object) -> None:
        self._calls.append({"app": app, **kwargs})
