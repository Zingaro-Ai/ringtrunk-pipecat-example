"""Verified inbound webhook admission. Run a single worker: uvicorn server:app.

This small example keeps admission state in memory. Multiple replicas need a
shared, atomic room claim before starting any bot (see README.md).
"""

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

from bot import run_bot
from fastapi import FastAPI, Header, HTTPException, Request

from livekit import api

LOGGER = logging.getLogger(__name__)
ROOM_PREFIX = "ringtrunk-pipecat-"
MAX_CONCURRENT_CALLS = 4
running: dict[str, asyncio.Task[None]] = {}
seen: dict[str, float] = {}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.receiver = api.WebhookReceiver(
        api.TokenVerifier(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
    )
    yield
    tasks = list(running.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    running.clear()
    seen.clear()


app = FastAPI(lifespan=lifespan)


def finished(room: str, task: asyncio.Task[None]) -> None:
    if running.get(room) is task:
        running.pop(room, None)
    if not task.cancelled() and task.exception() is not None:
        LOGGER.error("Inbound demo agent failed; check provider configuration and connectivity.")


@app.post("/livekit-webhook")
async def webhook(request: Request, authorization: str = Header(default="")) -> dict:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 65_536:
            raise HTTPException(413, "Webhook body is too large.")
    try:
        event = request.app.state.receiver.receive(bytes(body).decode("utf-8"), authorization)
    except Exception:
        raise HTTPException(401, "Invalid LiveKit webhook.") from None

    room = event.room.name
    # Outbound call.py starts its own pipeline only after the callee answers.
    # Ignoring these rooms also prevents webhook/CLI duplicate agents.
    if not room.startswith(ROOM_PREFIX) or room.startswith(f"{ROOM_PREFIX}out-"):
        return {"ok": True}
    if event.event == "room_finished":
        if task := running.get(room):
            task.cancel()
        return {"ok": True}
    if event.event != "participant_joined" or event.participant.kind != api.ParticipantInfo.SIP:
        return {"ok": True}
    if not event.participant.identity:
        return {"ok": True}

    now = time.monotonic()
    for name, timestamp in list(seen.items()):
        if now - timestamp > 3_600 and name not in running:
            seen.pop(name, None)
    if room in seen or room in running:
        return {"ok": True}
    if len(running) >= MAX_CONCURRENT_CALLS or len(seen) >= 1_024:
        raise HTTPException(503, "Demo capacity reached; retry later.")

    # No await between claiming a room and recording the task.
    seen[room] = now
    task = asyncio.create_task(run_bot(room, event.participant.identity))
    running[room] = task
    task.add_done_callback(lambda done: finished(room, done))
    return {"ok": True}


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
