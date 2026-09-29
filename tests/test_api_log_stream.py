"""SSE log stream: replay, live tailing, terminal shutdown and cleanup."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quant_trade.api.log_stream import (
    POLL_INTERVAL_SECONDS,
    _log_events,
    active_stream_count,
    create_log_stream_router,
)
from quant_trade.runs.models import RunStatus
from quant_trade.runs.store import RunStore


class _DisconnectingRequest:
    """A request that reports a client disconnect from the first poll onward."""

    def __init__(self) -> None:
        self.checks = 0

    async def is_disconnected(self) -> bool:
        self.checks += 1
        return True


class _App:
    """A bare FastAPI app exposing only the log stream, over a prepared store."""

    def __init__(self, store: RunStore):
        from fastapi import FastAPI

        self.store = store
        self.app = FastAPI()
        self.app.include_router(create_log_stream_router(store))


@pytest.fixture
def store(tmp_path: Path) -> Iterator[RunStore]:
    s = RunStore(str(tmp_path / "quant.db"))
    yield s
    s.close()


@pytest.fixture
def client(store: RunStore) -> Iterator[TestClient]:
    with TestClient(_App(store).app) as c:
        yield c


def _events(response: object) -> list[tuple[str, dict[str, object]]]:
    """Parse an SSE body into ``(event, payload)`` pairs."""
    parsed: list[tuple[str, dict[str, object]]] = []
    name = ""
    for block in response.iter_lines():  # type: ignore[attr-defined]
        line = block if isinstance(block, str) else block.decode()
        if line.startswith("event: "):
            name = line.removeprefix("event: ")
        elif line.startswith("data: "):
            parsed.append((name, json.loads(line.removeprefix("data: "))))
    return parsed


def _events_with_times(response: object) -> tuple[list[tuple[str, dict[str, object]]], dict[str, float]]:
    """Like :func:`_events`, but also records when each log line was read."""
    parsed: list[tuple[str, dict[str, object]]] = []
    received: dict[str, float] = {}
    name = ""
    for block in response.iter_lines():  # type: ignore[attr-defined]
        line = block if isinstance(block, str) else block.decode()
        if line.startswith("event: "):
            name = line.removeprefix("event: ")
        elif line.startswith("data: "):
            payload = json.loads(line.removeprefix("data: "))
            parsed.append((name, payload))
            if name == "log":
                received[str(payload["message"])] = time.monotonic()
    return parsed, received


def _stream(client: TestClient, run_id: str) -> list[tuple[str, dict[str, object]]]:
    with client.stream("GET", f"/api/runs/{run_id}/logs") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        return _events(response)


class TestReplay:
    def test_existing_logs_are_replayed_in_order(self, client: TestClient, store: RunStore) -> None:
        run_id = store.create("echo", "{}").run_id
        store.mark_running(run_id)
        for i in range(10):
            store.append_log(run_id, f"line {i}")
        store.finish(run_id, RunStatus.OK)

        events = _stream(client, run_id)
        logs = [payload for name, payload in events if name == "log"]

        assert [entry["seq"] for entry in logs] == list(range(1, 11))
        assert [entry["message"] for entry in logs] == [f"line {i}" for i in range(10)]

    def test_level_is_carried(self, client: TestClient, store: RunStore) -> None:
        run_id = store.create("echo", "{}").run_id
        store.append_log(run_id, "回退到备用源", level="warning")
        store.finish(run_id, RunStatus.OK)

        logs = [p for name, p in _stream(client, run_id) if name == "log"]
        assert logs[0]["level"] == "warning"

    def test_a_finished_run_still_replays(self, client: TestClient, store: RunStore) -> None:
        """History must stay readable long after the producing process is gone."""
        run_id = store.create("echo", "{}").run_id
        store.append_log(run_id, "old line")
        store.finish(run_id, RunStatus.OK)

        events = _stream(client, run_id)
        assert [p["message"] for n, p in events if n == "log"] == ["old line"]

    def test_missing_run_is_404(self, client: TestClient) -> None:
        assert client.get("/api/runs/nope/logs").status_code == 404


class TestTerminalShutdown:
    @pytest.mark.parametrize(
        "status",
        [RunStatus.OK, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.INTERRUPTED],
    )
    def test_every_terminal_status_closes_the_stream(
        self, client: TestClient, store: RunStore, status: RunStatus
    ) -> None:
        run_id = store.create("echo", "{}").run_id
        store.finish(run_id, status)

        events = _stream(client, run_id)
        assert events[-1][0] == "end"
        assert events[-1][1]["status"] == status.value

    def test_end_event_is_last(self, client: TestClient, store: RunStore) -> None:
        run_id = store.create("echo", "{}").run_id
        store.append_log(run_id, "only line")
        store.finish(run_id, RunStatus.OK)

        events = _stream(client, run_id)
        assert [name for name, _ in events] == ["log", "end"]


class TestLiveTailing:
    def test_new_logs_are_pushed_within_the_poll_window(self, client: TestClient, store: RunStore) -> None:
        """A line written mid-stream arrives on the next poll, not at the end.

        Arrival times are recorded as the body is consumed rather than after it
        ends, which is what distinguishes real tailing from a buffered response.
        """
        run_id = store.create("echo", "{}").run_id
        store.mark_running(run_id)
        store.append_log(run_id, "before connect")

        written_at: dict[str, float] = {}

        def produce() -> None:
            time.sleep(0.3)
            written_at["live line 1"] = time.monotonic()
            store.append_log(run_id, "live line 1")
            store.append_log(run_id, "live line 2")
            store.finish(run_id, RunStatus.OK)

        writer = threading.Thread(target=produce)
        writer.start()
        try:
            with client.stream("GET", f"/api/runs/{run_id}/logs") as response:
                assert response.status_code == 200
                events, received_at = _events_with_times(response)
        finally:
            writer.join()

        assert [p["message"] for n, p in events if n == "log"] == ["before connect", "live line 1", "live line 2"]
        assert events[-1][0] == "end"

        # The spec bounds delivery at one poll interval; the slack absorbs
        # transport and parsing between the server flushing and us reading.
        delay = received_at["live line 1"] - written_at["live line 1"]
        assert delay < POLL_INTERVAL_SECONDS + 1.0, f"log took {delay:.3f}s to arrive"

    def test_poll_interval_matches_the_specified_cadence(self) -> None:
        """The stream promises new logs within 500 ms."""
        assert POLL_INTERVAL_SECONDS <= 0.5


class TestCleanup:
    def test_stream_is_released_when_it_ends(self, client: TestClient, store: RunStore) -> None:
        run_id = store.create("echo", "{}").run_id
        store.finish(run_id, RunStatus.OK)

        assert active_stream_count() == 0
        _stream(client, run_id)
        assert active_stream_count() == 0

    @pytest.mark.asyncio
    async def test_stream_is_released_when_the_client_disconnects(self, store: RunStore) -> None:
        """A run that never ends must still release its stream on disconnect.

        Driven at the generator rather than through ``TestClient``: an endless
        stream cannot be closed from the outside once the test client is
        blocked waiting on it.
        """
        run_id = store.create("echo", "{}").run_id
        store.mark_running(run_id)
        store.append_log(run_id, "still going")

        request = _DisconnectingRequest()
        chunks = [chunk async for chunk in _log_events(store, run_id, request)]  # type: ignore[arg-type]

        assert request.checks > 0
        assert any("still going" in chunk for chunk in chunks)
        # No `end` event: the run is still going, the client left first.
        assert not any("event: end" in chunk for chunk in chunks)
        assert active_stream_count() == 0

    def test_read_only_endpoint_ignores_client_data(self, client: TestClient, store: RunStore) -> None:
        """SSE is server-to-client only; a request body changes nothing."""
        run_id = store.create("echo", "{}").run_id
        store.append_log(run_id, "unaffected")
        store.finish(run_id, RunStatus.OK)

        with client.stream(
            "GET", f"/api/runs/{run_id}/logs", content=b"noise", headers={"content-type": "text/plain"}
        ) as response:
            events = _events(response)

        assert [p["message"] for n, p in events if n == "log"] == ["unaffected"]
        assert store.logs(run_id)[0].message == "unaffected"
