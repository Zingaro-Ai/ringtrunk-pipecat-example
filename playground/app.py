"""Serve the local UI, read-only examples, and fixed-prompt ZIPs.

Run from the repository root: uv run uvicorn playground.app:app --port 4173
Phone verification uses Firebase; agent modules are never imported by this app.
"""

import asyncio
import io
import json
import os
import re
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from playground.callbacks import CallbackService
from playground.phone_auth import phone_public_config, verified_callback_identity

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "playground" / "static"
MAX_REQUEST_BYTES = 32_768
# An explicit manifest is the only way a file enters the viewer or a ZIP.
# Never recursively archive a directory: it could contain a developer's .env.
SHARED_FILES = {
    "config.py": "examples/shared/config.py",
    "call.py": "examples/shared/call.py",
    "livekit/outbound-trunk.json": "examples/shared/outbound-trunk.json",
    "agent-config.json": "examples/shared/agent-config.json",
    ".env.example": "examples/shared/env.example.txt",
    ".gitignore": "examples/shared/gitignore.txt",
    "LICENSE": "LICENSE",
}
EXAMPLES = {
    "pipecat": {
        "name": "Pipecat",
        "version": "1.9.0",
        "entrypoint": "bot.py",
        "description": "An explicit speech pipeline, connected through LiveKit Cloud.",
        "files": {
            "bot.py": "examples/pipecat/bot.py",
            "server.py": "examples/pipecat/server.py",
            **SHARED_FILES,
            "requirements.txt": "examples/pipecat/requirements.txt",
            "livekit/inbound-trunk.json": "examples/shared/inbound-trunk.json",
            "livekit/dispatch-rule.json": "examples/pipecat/dispatch-rule.json",
            "README.md": "examples/pipecat/README.md",
        },
    },
    "livekit-agents": {
        "name": "LiveKit Agents",
        "version": "1.8.1",
        "entrypoint": "agent.py",
        "description": "A native agent session, dispatched by LiveKit Cloud.",
        "files": {
            "agent.py": "examples/livekit-agents/agent.py",
            **SHARED_FILES,
            "requirements.txt": "examples/livekit-agents/requirements.txt",
            "livekit/inbound-trunk.json": "examples/shared/inbound-trunk.json",
            "livekit/dispatch-rule.json": "examples/livekit-agents/dispatch-rule.json",
            "README.md": "examples/livekit-agents/README.md",
        },
    },
}


class DownloadRequest(BaseModel):
    # Only a framework may be selected. Prompts/code are owned by the server.
    model_config = ConfigDict(extra="forbid", strict=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    service = CallbackService.configured()
    app.state.callbacks = service
    try:
        if service:
            await service.start()
        yield
    finally:
        if service:
            await service.close()


app = FastAPI(title="RingTrunk local playground", docs_url=None, redoc_url=None,
              lifespan=lifespan)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["localhost", "127.0.0.1", "testserver", "playground-local.ringtrunk.com"],
)


@app.middleware("http")
async def response_headers(request: Request, call_next):
    response = await call_next(request)
    # Firebase and reCAPTCHA load only when a visitor starts phone verification.
    auth_csp = (
        "default-src 'self'; "
        "script-src 'self' https://www.google.com/recaptcha/ https://www.gstatic.com/recaptcha/; "
        "style-src 'self'; img-src 'self' data: https://www.gstatic.com https://www.google.com; "
        "font-src 'self'; connect-src 'self' https://identitytoolkit.googleapis.com "
        "https://securetoken.googleapis.com https://www.google.com/recaptcha/; "
        "frame-src https://www.google.com/recaptcha/ https://recaptcha.google.com/recaptcha/; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    )
    response.headers.update(
        {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex, nofollow",
            "Cache-Control": "no-store",
            "Content-Security-Policy": auth_csp,
            "Permissions-Policy": "microphone=(), camera=(), geolocation=()",
        }
    )
    return response


def example_files(framework: str) -> dict[str, str]:
    example = EXAMPLES.get(framework)
    if example is None:
        raise HTTPException(404, "Unknown framework.")
    files = {
        name: (ROOT / source).read_text(encoding="utf-8")
        for name, source in example["files"].items()
    }
    config = json.loads(files["agent-config.json"])
    config["framework"] = framework
    files["agent-config.json"] = json.dumps(config, indent=2, ensure_ascii=False) + "\n"
    return files


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "mode": "local-preview"}


@app.get("/api/config")
def public_config() -> dict:
    # The selected demo number is runtime configuration, never bundled in a ZIP.
    number = os.environ.get("RINGTRUNK_DEMO_NUMBER", "")
    if not re.fullmatch(r"\+91[0-9]{10}", number):
        number = None
    return {
        "mode": "local-preview",
        "inbound_demo_ready": False,
        "outbound_demo_ready": callbacks_ready(),
        "demo_number": number,
        "requested_channels": 3,
        "allocation_status": (
            "allocated"
            if os.environ.get("RINGTRUNK_DEMO_ALLOCATION_STATUS") == "allocated"
            else "pending"
        ),
        "prompt_editable": False,
        "call_modes": ["inbound", "outbound"],
        "phone_verification_required": True,
        "phone_verification": phone_public_config(),
        "frameworks": [
            {"id": key, **{k: v for k, v in example.items() if k != "files"}}
            for key, example in EXAMPLES.items()
        ],
    }


@app.post("/api/phone/verify")
async def verify_phone(request: Request) -> dict:
    """Accept only a Firebase bearer token and an empty body, never a destination."""
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 64:
            raise HTTPException(413, "Verification request is too large.")
    if bytes(body).strip() not in (b"", b"{}"):
        raise HTTPException(422, "The verified number must come from Firebase.")
    identity = await run_in_threadpool(verified_callback_identity, request)
    return {
        "verified": True,
        "phone_number": identity.phone_number,
        "expires_at": identity.expires_at,
        "outbound_demo_ready": callbacks_ready(),
    }


def callbacks_ready() -> bool:
    service = getattr(app.state, "callbacks", None)
    return bool(service and service.ready)


def callbacks() -> CallbackService:
    service = getattr(app.state, "callbacks", None)
    if service is None:
        raise HTTPException(503, "Demo calls are not configured for this preview.")
    return service


class CallbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    framework: Literal["pipecat", "livekit-agents"]
    request_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    )


@app.post("/api/calls", status_code=202)
async def request_callback(request: Request) -> dict:
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        raise HTTPException(415, "Send a JSON call request.")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 512:
            raise HTTPException(413, "The call request is too large.")
    try:
        payload = CallbackRequest.model_validate_json(bytes(body))
    except ValidationError:
        raise HTTPException(422, "Select an agent and a unique request ID only.") from None
    identity = await run_in_threadpool(verified_callback_identity, request)
    return await callbacks().submit(identity, payload.framework, payload.request_id)


@app.get("/api/calls/{call_id}")
async def callback_status(call_id: str, request: Request) -> dict:
    # The receipt authorizes status/cancellation of one call, never another dial.
    return await run_in_threadpool(
        callbacks().journal.status, call_id, request.headers.get("x-call-token", "")
    )


@app.post("/api/calls/{call_id}/cancel")
async def cancel_callback(call_id: str, request: Request) -> dict:
    service = callbacks()
    receipt = request.headers.get("x-call-token", "")
    await run_in_threadpool(service.journal.status, call_id, receipt)
    task = service.tasks.get(call_id)
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        status = await run_in_threadpool(service.journal.status, call_id, receipt)
        if not status["done"]:
            await service.delete_room(call_id)
            await service.phase(call_id, "cancelled", "Demo call cancelled.")
    return await run_in_threadpool(service.journal.status, call_id, receipt)


@app.get("/api/examples/{framework}")
def source_files(framework: str) -> dict:
    return {"framework": framework, "files": example_files(framework)}


def build_zip(framework: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in example_files(framework).items():
            archive.writestr(f"ringtrunk-{framework}/{name}", content)
    return buffer.getvalue()


@app.post("/api/download/{framework}")
async def download(framework: str, request: Request) -> Response:
    if framework not in EXAMPLES:
        raise HTTPException(404, "Unknown framework.")
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        raise HTTPException(415, "Send an empty JSON object.")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_REQUEST_BYTES:
            raise HTTPException(413, "The download request is too large.")
    try:
        DownloadRequest.model_validate_json(bytes(body))
    except ValidationError:
        raise HTTPException(422, "The example is read-only. Send an empty JSON object.") from None
    archive = await run_in_threadpool(build_zip, framework)
    return Response(
        content=archive,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="ringtrunk-{framework}.zip"',
        },
    )


app.mount("/static", StaticFiles(directory=STATIC), name="static")
