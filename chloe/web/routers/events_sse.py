"""Server-Sent Events endpoint for live dashboard updates."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.responses import StreamingResponse

from chloe.web.deps import get_broadcaster
from chloe.web.events import EventBroadcaster

router = APIRouter(tags=["events"])


@router.get("/api/events")
async def event_stream(
    broadcaster: EventBroadcaster = Depends(get_broadcaster),
) -> StreamingResponse:
    q = broadcaster.subscribe()
    return StreamingResponse(
        broadcaster.stream(q),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
