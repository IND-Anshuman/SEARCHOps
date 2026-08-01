"""
WebSocket endpoint for real-time research progress streaming.
"""

from __future__ import annotations

import json

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from searchops.application.research_service import ResearchApplicationService

log = structlog.get_logger(__name__)

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/research/{job_id}")
async def research_progress_ws(
    websocket: WebSocket,
    job_id: str,
) -> None:
    """Stream research job progress via WebSocket until completion or disconnect."""
    await websocket.accept()
    log.info("WebSocket client connected", job_id=job_id)

    service = ResearchApplicationService()  # DI container wires this in production

    try:
        async for update in service.stream_progress(job_id):
            await websocket.send_text(json.dumps(update))
            if update.get("status") in ("completed", "failed"):
                break
    except WebSocketDisconnect:
        log.info("WebSocket client disconnected", job_id=job_id)
    except Exception as exc:
        log.error("WebSocket error", job_id=job_id, error=str(exc))
        await websocket.send_text(json.dumps({"error": str(exc)}))
    finally:
        await websocket.close()
