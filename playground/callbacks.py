"""Local callback service: verified recipients, durable limits, isolated demo rooms.

The service owns its agent processes. A private SQLite journal preserves limits
and idempotency across preview restarts; a process lock forbids multiple servers.
Neither Firebase proofs nor phone numbers are written to this journal.
"""

import asyncio
import fcntl
import hashlib
import hmac
import json
import logging
import os
import re
import signal
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import aiohttp
from fastapi import HTTPException
from google.protobuf.duration_pb2 import Duration

from livekit import api
from playground.phone_auth import PhoneIdentity

LOGGER = logging.getLogger(__name__)
TERMINAL = {"completed", "failed", "cancelled"}
MAX_CALL_SECONDS = 120
MAX_RING_SECONDS = 45
DIAL_TIMEOUT_SECONDS = 55
MAX_CONCURRENT = 3
DAILY_LIMIT = 3
COOLDOWN_SECONDS = 60
AGENT_PROCESS = Path(__file__).with_name("agent_process.py")


class CallJournal:
    def __init__(self, directory: Path, hash_key: str) -> None:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = directory / "calls.sqlite3"
        self.key = hash_key.encode()
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS calls (
                id TEXT PRIMARY KEY, uid TEXT NOT NULL, phone TEXT NOT NULL,
                request_id TEXT NOT NULL, framework TEXT NOT NULL,
                created REAL NOT NULL, updated REAL NOT NULL, phase TEXT NOT NULL,
                message TEXT NOT NULL, answered INTEGER NOT NULL DEFAULT 0,
                UNIQUE(uid, request_id))""")
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def digest(self, value: str) -> str:
        return hmac.new(self.key, value.encode(), hashlib.sha256).hexdigest()

    def reserve(self, identity: PhoneIdentity, framework: str, request_id: str) -> tuple:
        now = time.time()
        uid, phone = self.digest("uid:" + identity.uid), self.digest(identity.phone_number)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM calls WHERE uid=? AND request_id=?", (uid, request_id)
            ).fetchone()
            if existing:
                if existing["framework"] != framework:
                    raise HTTPException(409, "This request already selected a different agent.")
                return dict(existing), False
            recent = db.execute(
                "SELECT * FROM calls WHERE (uid=? OR phone=?) AND created>?",
                (uid, phone, now - 86400),
            ).fetchall()
            if any(row["phase"] not in TERMINAL for row in recent):
                raise HTTPException(409, "A demo call to your number is already in progress.")
            if len(recent) >= DAILY_LIMIT:
                raise HTTPException(429, "You have used today's three demo calls. Try tomorrow.")
            if recent and now - max(row["created"] for row in recent) < COOLDOWN_SECONDS:
                raise HTTPException(429, "Wait one minute between demo calls.",
                                    headers={"Retry-After": "60"})
            active = db.execute(
                "SELECT count(*) FROM calls WHERE phase NOT IN ('completed','failed','cancelled')"
            ).fetchone()[0]
            if active >= MAX_CONCURRENT:
                raise HTTPException(429, "All three demo channels are busy. Try again shortly.",
                                    headers={"Retry-After": "30"})
            prefix = "pipecat" if framework == "pipecat" else "livekit"
            call_id = f"ringtrunk-{prefix}-out-{uuid4().hex}"
            db.execute(
                "INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,0)",
                (call_id, uid, phone, request_id, framework, now, now, "preparing",
                 "Preparing your demo agent…"),
            )
            row = dict(db.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone())
            return row, True

    def update(self, call_id: str, phase: str, message: str, answered: bool = False) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE calls SET phase=?,message=?,updated=?,answered=MAX(answered,?) WHERE id=?",
                (phase, message, time.time(), int(answered), call_id),
            )

    def unfinished(self) -> list[str]:
        with self.connect() as db:
            return [r[0] for r in db.execute(
                "SELECT id FROM calls WHERE phase NOT IN ('completed','failed','cancelled')"
            )]

    def receipt(self, call_id: str) -> str:
        return self.digest("receipt:" + call_id)

    def status(self, call_id: str, receipt: str) -> dict:
        if not hmac.compare_digest(self.receipt(call_id), receipt):
            raise HTTPException(404, "Call not found.")
        with self.connect() as db:
            row = db.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Call not found.")
        return {"call_id": row["id"], "framework": row["framework"], "phase": row["phase"],
                "message": row["message"], "answered": bool(row["answered"]),
                "done": row["phase"] in TERMINAL}


class AgentProcess:
    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self.process = process
        self.ready = asyncio.Event()
        self.output = asyncio.create_task(self.consume())

    async def consume(self) -> None:
        # SDK logs can contain speech or provider details. Keep only readiness;
        # drain other output without persisting it or forwarding it to HTTP logs.
        try:
            async for line in self.process.stdout:
                if line.strip() == b"RINGTRUNK_AGENT_READY":
                    self.ready.set()
        finally:
            self.ready.clear()

    async def wait_ready(self) -> None:
        async with asyncio.timeout(40):
            while not self.ready.is_set():
                if self.process.returncode is not None:
                    raise RuntimeError("Agent process exited before readiness")
                await asyncio.sleep(0.1)

    async def close(self) -> None:
        if self.process.returncode is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
                await asyncio.wait_for(self.process.wait(), 8)
            except (TimeoutError, ProcessLookupError):
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await self.process.wait()
        await self.output


class CallbackService:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.journal = CallJournal(Path(config["state_dir"]), config["hash_key"])
        self.client = None
        self.native = None
        self.tasks: dict[str, asyncio.Task] = {}
        self.dial_lock = asyncio.Lock()
        self.last_dial = 0.0
        self.healthy = False
        self.lock_file = None

    @classmethod
    def configured(cls):
        path = os.environ.get("RINGTRUNK_CALL_CONFIG")
        if not path:
            return None
        config = json.loads(Path(path).read_text())
        if not re.fullmatch(r"\+91[6-9]\d{9}", config["caller_id"]):
            raise ValueError("Invalid demo caller ID")
        if len(config["hash_key"]) < 32:
            raise ValueError("A private call journal key is required")
        for framework in ("pipecat", "livekit-agents"):
            agent = config["agents"][framework]
            if not Path(agent["python"]).is_file() or not Path(agent["directory"]).is_dir():
                raise ValueError("Agent runtime is missing")
        return cls(config)

    @property
    def ready(self) -> bool:
        return bool(self.healthy and self.native and self.native.ready.is_set()
                    and self.native.process.returncode is None)

    async def spawn(self, framework: str) -> AgentProcess:
        agent = self.config["agents"][framework]
        # Only server configuration chooses a Python executable, source or prompt.
        env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG") if key in os.environ}
        env.update(self.config["environment"])
        env["RINGTRUNK_AGENT_DIRECTORY"] = agent["directory"]
        env["PYTHONUNBUFFERED"] = "1"
        process = await asyncio.create_subprocess_exec(
            agent["python"], str(AGENT_PROCESS), framework, cwd=agent["directory"], env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, start_new_session=True,
        )
        return AgentProcess(process)

    async def start(self) -> None:
        self.lock_file = open(Path(self.config["state_dir"]) / "server.lock", "a")
        # A second process must not clean up the first process's active calls.
        fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        env = self.config["environment"]
        self.client = api.LiveKitAPI(
            env["LIVEKIT_URL"], env["LIVEKIT_API_KEY"], env["LIVEKIT_API_SECRET"],
            timeout=aiohttp.ClientTimeout(total=10), failover=False,
        )
        trunks = await self.client.sip.list_outbound_trunk(api.ListSIPOutboundTrunkRequest())
        selected = [t for t in trunks.items if t.sip_trunk_id == self.config["outbound_trunk_id"]]
        if len(selected) != 1 or list(selected[0].numbers) != [self.config["caller_id"][3:]]:
            raise RuntimeError("Dedicated demo trunk is not configured for this number")
        for call_id in await asyncio.to_thread(self.journal.unfinished):
            await self.delete_room(call_id)
            await asyncio.to_thread(self.journal.update, call_id, "failed",
                                    "The local preview restarted. Please try again.")
        self.native = await self.spawn("livekit-agents")
        await self.native.wait_ready()
        self.healthy = True

    async def close(self) -> None:
        self.healthy = False
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.native:
            await self.native.close()
        if self.client:
            await self.client.aclose()
        if self.lock_file:
            self.lock_file.close()

    async def submit(self, identity: PhoneIdentity, framework: str, request_id: str) -> dict:
        if not self.ready:
            raise HTTPException(503, "The demo agent is starting. Please try again shortly.")
        if identity.phone_number == self.config["caller_id"]:
            raise HTTPException(422, "Use your own mobile number, not the demo number.")
        row, created = await asyncio.to_thread(
            self.journal.reserve, identity, framework, request_id
        )
        call_id = row["id"]
        if created:
            task = asyncio.create_task(self.run(call_id, framework, identity.phone_number))
            self.tasks[call_id] = task
            task.add_done_callback(lambda _done: self.tasks.pop(call_id, None))
        receipt = self.journal.receipt(call_id)
        return {**self.journal.status(call_id, receipt), "call_token": receipt}

    async def phase(self, call_id: str, phase: str, message: str, answered: bool = False) -> None:
        await asyncio.to_thread(self.journal.update, call_id, phase, message, answered)

    async def delete_room(self, room: str) -> None:
        if not re.fullmatch(r"ringtrunk-(pipecat|livekit)-out-[0-9a-f]{32}", room):
            raise ValueError("Refusing to touch a room outside this demo")
        for attempt in range(3):
            try:
                await self.client.room.delete_room(api.DeleteRoomRequest(room=room))
                return
            except api.TwirpError as error:
                if error.code == "not_found":
                    return
                if attempt == 2:
                    raise
                await asyncio.sleep(0.5)

    async def run(self, call_id: str, framework: str, phone: str) -> None:
        process = None
        answered = False
        final_phase, final_message = "completed", "Demo call ended."
        try:
            if framework == "pipecat":
                process = await self.spawn(framework)
                await process.wait_ready()
            await self.client.room.create_room(api.CreateRoomRequest(
                name=call_id, empty_timeout=30, departure_timeout=10,
            ))
            async with self.dial_lock:
                await asyncio.sleep(max(0, 1.1 - (time.monotonic() - self.last_dial)))
                self.last_dial = time.monotonic()
            await self.phase(call_id, "ringing", "Calling your verified mobile. Please answer.")
            # Dialing is a side effect. Never replay an uncertain request in another
            # Cloud region, and bound the entire operation independently of SDK timeouts.
            async with asyncio.timeout(DIAL_TIMEOUT_SECONDS):
                await self.client.sip.create_sip_participant(api.CreateSIPParticipantRequest(
                    sip_trunk_id=self.config["outbound_trunk_id"], sip_call_to=phone,
                    sip_number=self.config["caller_id"][3:], room_name=call_id,
                    participant_identity="demo-callee", participant_name="Demo caller",
                    wait_until_answered=True, ringing_timeout=Duration(seconds=MAX_RING_SECONDS),
                    max_call_duration=Duration(seconds=MAX_CALL_SECONDS),
                ), timeout=DIAL_TIMEOUT_SECONDS)
            answered = True
            await self.phase(call_id, "answered", "Call answered. Connecting your agent…", True)
            if process:
                process.process.stdin.write((call_id + "\n").encode())
                await process.process.stdin.drain()
                process.process.stdin.close()
            else:
                await self.client.agent_dispatch.create_dispatch(api.CreateAgentDispatchRequest(
                    room=call_id, agent_name="ringtrunk-playground-livekit",
                ))
            started = time.monotonic()
            agent_joined = False
            while time.monotonic() - started < MAX_CALL_SECONDS:
                participants = await self.client.room.list_participants(
                    api.ListParticipantsRequest(room=call_id)
                )
                caller_present = any(p.identity == "demo-callee" for p in participants.participants)
                if not caller_present:
                    break
                joined = any(
                    p.identity == "ringtrunk-pipecat-agent" or p.kind == api.ParticipantInfo.AGENT
                    for p in participants.participants
                )
                if joined and not agent_joined:
                    agent_joined = True
                    await self.phase(call_id, "connected", "Your demo agent is on the call.", True)
                if not joined and (agent_joined or time.monotonic() - started > 20):
                    raise RuntimeError("The demo agent did not stay connected")
                if process and process.process.returncode is not None:
                    break
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            final_phase, final_message = "cancelled", "Demo call cancelled."
        except Exception as error:
            # Log only the type/code, never SIP errors containing destination numbers.
            code = str(getattr(error, "code", ""))
            sip_code = getattr(error, "sip_status_code", None)
            LOGGER.warning("Demo call failed: %s (%s, SIP %s)",
                           type(error).__name__, code, sip_code)
            if code == "not_found" and answered:
                final_phase, final_message = "completed", "Demo call ended."
            else:
                final_phase = "failed"
                final_message = "The call could not connect. Please try again in one minute."
                if isinstance(error, TimeoutError) or code == "deadline_exceeded":
                    final_message = "The call was not answered in time. Please try again."
                elif sip_code in (486, 603):
                    final_message = "Your phone was busy or the call was declined. Try again later."
                elif sip_code in (408, 480):
                    final_message = "Your phone did not answer or was unreachable. Please retry."
        finally:
            try:
                await self.delete_room(call_id)
            except Exception:
                LOGGER.error("Demo room cleanup failed; the SIP duration limit remains in force")
                self.healthy = False
            if process:
                await process.close()
            await self.phase(call_id, final_phase, final_message)
