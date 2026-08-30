"""
WebSocket endpoint for real-time research progress streaming.

Architecture:
  1. Accept connection
  2. Retrieve current job state (replay for late subscribers)
  3. If job already terminal → send final snapshot → close gracefully
  4. Otherwise → subscribe to Redis pub/sub channel for live updates
  5. Run concurrent tasks: pub/sub listener, incoming reader (ping/pong), and heartbeat
  6. On disconnect → cancel tasks → unsubscribe automatically

This design guarantees:
  - No race conditions between job completion and WS connection timing
  - Multiple concurrent subscribers each get their own pub/sub listener
  - Late subscribers (connecting after job completes) receive final state immediately
  - Connections are kept alive with bidirectional ping/pong
  - Connections are cleaned up deterministically on client disconnect
"""
from __future__ import annotations

import asyncio
import json

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from searchops.bootstrap.container import ApplicationContainer

log = structlog.get_logger(__name__)

router = APIRouter(tags=["websocket"])

_HEARTBEAT_INTERVAL_SECONDS = 30
_TERMINAL_STATUSES = frozenset({"completed", "failed"})


@router.websocket("/ws/research/{job_id}")
async def research_progress_ws(
    websocket: WebSocket,
    job_id: str,
) -> None:
    """Stream research job progress via WebSocket.

    Handles late subscribers: if the job has already completed when this
    connection opens, the final state is sent immediately and the connection
    closes cleanly without waiting for pub/sub messages that will never arrive.
    """
    await websocket.accept()
    log.info("WebSocket client connected", job_id=job_id)

    # Resolve the shared singleton from app state
    from searchops.bootstrap.container import get_container
    try:
        container: ApplicationContainer = websocket.app.state.container
    except AttributeError:
        container = get_container()
    job_state_manager = container.job_state_manager

    try:
        # ── Step 1: Late-subscriber replay ────────────────────────────────────
        current_state = await job_state_manager.get_or_replay(job_id)

        if current_state is None:
            # Job not found — send a 404-equivalent and close
            await websocket.send_text(json.dumps({
                "job_id": job_id,
                "error": f"Research job '{job_id}' not found.",
                "status": "not_found",
            }))
            await websocket.close(code=4404)
            log.info("WebSocket closed: job not found", job_id=job_id)
            return

        # Always send current state snapshot first (replay)
        await websocket.send_text(json.dumps(current_state))

        if current_state.get("status") in _TERMINAL_STATUSES:
            # Job already complete — late subscriber gets final state, close cleanly
            await websocket.close()
            log.info(
                "WebSocket closed after replay (job already terminal)",
                job_id=job_id,
                status=current_state.get("status"),
            )
            return

        # ── Step 2: Live streaming via pub/sub + bidirectional ping/pong ──────
        listener_task = asyncio.create_task(
            _pubsub_listener(websocket, job_id, job_state_manager)
        )
        heartbeat_task = asyncio.create_task(
            _heartbeat(websocket, job_id)
        )
        reader_task = asyncio.create_task(
            _incoming_reader(websocket, job_id)
        )

        done, pending = await asyncio.wait(
            {listener_task, heartbeat_task, reader_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Cancel surviving tasks cleanly
        for task in pending:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        # Check for unexpected exceptions
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, (WebSocketDisconnect, asyncio.CancelledError)):
                log.error("WebSocket task raised unexpected error", job_id=job_id, error=str(exc))

    except WebSocketDisconnect:
        log.info("WebSocket connection closed by client", job_id=job_id)
    except asyncio.CancelledError:
        log.info("WebSocket handler cancelled", job_id=job_id)
    except Exception as exc:
        log.error("WebSocket handler error", job_id=job_id, error=str(exc))
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
    finally:
        log.info("WebSocket connection closed", job_id=job_id)


async def _pubsub_listener(
    websocket: WebSocket,
    job_id: str,
    job_state_manager: object,
) -> None:
    """Forward pub/sub messages to the WebSocket until job reaches terminal state."""
    async for state in job_state_manager.subscribe(job_id):
        try:
            await websocket.send_text(json.dumps(state))
        except (WebSocketDisconnect, RuntimeError):
            log.info("Client disconnected during stream", job_id=job_id)
            return

        if state.get("status") in _TERMINAL_STATUSES:
            # Job completed — close cleanly after delivering final state
            try:
                await websocket.close()
            except Exception:
                pass
            return


async def _heartbeat(websocket: WebSocket, job_id: str) -> None:
    """Send periodic ping frames to keep connection alive."""
    while True:
        await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
        try:
            await websocket.send_text(json.dumps({"type": "ping", "job_id": job_id}))
        except (WebSocketDisconnect, RuntimeError):
            return


async def _incoming_reader(websocket: WebSocket, job_id: str) -> None:
    """Read incoming frames from client (handles client ping -> server pong)."""
    while True:
        try:
            text = await websocket.receive_text()
            data = json.loads(text)
            if data.get("type") == "ping":
                await websocket.send_text(json.dumps({"type": "pong", "job_id": job_id}))
        except (WebSocketDisconnect, RuntimeError):
            return
        except Exception as exc:
            log.debug("Error reading client message", job_id=job_id, error=str(exc))
