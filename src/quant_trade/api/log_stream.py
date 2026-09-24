"""Server-Sent Events stream of a run's log lines.

The stream reads ``run_log`` and polls for new rows rather than subscribing to
an in-memory queue. The table is the only source of truth: a run's logs must
still be replayable from a connection opened after the run finished, or after
the process that produced them restarted.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncIterator
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from quant_trade.runs.models import RunLogLine
from quant_trade.runs.store import RunStore

POLL_INTERVAL_SECONDS = 0.5
"""How often the stream checks for new rows. Half a second reads as instant."""

RETRY_MILLISECONDS = 2000
"""Reconnect delay handed to ``EventSource`` clients."""

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # Proxies that buffer would defeat the whole point of streaming.
    "X-Accel-Buffering": "no",
}

_active_streams = 0
_active_lock = threading.Lock()


def active_stream_count() -> int:
    """Open log streams. Used by tests to prove a disconnect releases its resources."""
    with _active_lock:
        return _active_streams


def _enter_stream() -> None:
    global _active_streams
    with _active_lock:
        _active_streams += 1


def _leave_stream() -> None:
    global _active_streams
    with _active_lock:
        _active_streams -= 1


def create_log_stream_router(runs: RunStore) -> APIRouter:
    """Build the SSE route bound to a run store."""
    router = APIRouter(prefix="/api/runs", tags=["runs"])

    @router.get("/{run_id}/logs")
    async def stream_logs(run_id: str, request: Request) -> StreamingResponse:
        """Replay a run's logs, then follow it until it reaches a terminal state."""
        if await asyncio.to_thread(runs.get, run_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"运行 {run_id} 不存在")
        return StreamingResponse(
            _log_events(runs, run_id, request),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    return router


async def _log_events(runs: RunStore, run_id: str, request: Request) -> AsyncIterator[str]:
    """Yield every log line of a run, then an ``end`` event.

    Database reads go through a worker thread: the queries are small, but they
    are blocking, and holding the event loop for them would stall every other
    request for as long as the stream lives.
    """
    _enter_stream()
    last_seq = 0
    try:
        yield f"retry: {RETRY_MILLISECONDS}\n\n"
        while True:
            for line in await asyncio.to_thread(runs.logs, run_id, last_seq):
                last_seq = line.seq
                yield _event("log", _log_payload(line))

            record = await asyncio.to_thread(runs.get, run_id)
            if record is None or record.status.is_terminal:
                # One final drain: `finish` runs after the closing log line is
                # written, so a row can land between the read above and this one.
                for line in await asyncio.to_thread(runs.logs, run_id, last_seq):
                    last_seq = line.seq
                    yield _event("log", _log_payload(line))
                final = "gone" if record is None else record.status.value
                yield _event("end", {"run_id": run_id, "status": final})
                return

            if await request.is_disconnected():
                return
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
    finally:
        _leave_stream()


def _event(name: str, payload: dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _log_payload(line: RunLogLine) -> dict[str, Any]:
    return {"seq": line.seq, "ts": _iso(line.ts), "level": line.level, "message": line.message}


def _iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value is not None else None
