"""Callback admission and SIP lifecycle checks; no test dials a real number."""

import asyncio
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient

from livekit import api
from playground.app import app
from playground.callbacks import CallbackService, CallJournal
from playground.phone_auth import PhoneIdentity

PHONE = "+91" + "9" * 10


def identity(suffix: int = 0) -> PhoneIdentity:
    return PhoneIdentity(f"fixture-{suffix}", "+91" + str(9000000000 + suffix),
                         int(time.time()) + 600)


class JournalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.journal = CallJournal(self.directory, "fixture-key" * 4)

    def reserve(self, person=None):
        return self.journal.reserve(person or identity(), "pipecat", str(uuid4()))[0]

    def test_concurrent_admission_is_atomic_and_limited_to_three(self) -> None:
        def reserve(index):
            try:
                return self.reserve(identity(index))["id"]
            except HTTPException as error:
                return error.status_code
        with ThreadPoolExecutor(max_workers=8) as pool:
            result = list(pool.map(reserve, range(8)))
        self.assertEqual(result.count(429), 5)
        self.assertEqual(len(self.journal.unfinished()), 3)

    def test_duplicate_requests_survive_restart_without_redial(self) -> None:
        key = str(uuid4())
        row, created = self.journal.reserve(identity(), "pipecat", key)
        self.assertTrue(created)
        other = CallJournal(self.directory, "fixture-key" * 4)
        same, created = other.reserve(identity(), "pipecat", key)
        self.assertFalse(created)
        self.assertEqual(row, same)
        with self.assertRaises(HTTPException) as error:
            other.reserve(identity(), "livekit-agents", key)
        self.assertEqual(error.exception.status_code, 409)

    def test_phone_cannot_bypass_limits_with_a_different_uid(self) -> None:
        row = self.reserve()
        self.journal.update(row["id"], "completed", "Ended")
        with self.assertRaises(HTTPException) as error:
            self.reserve(PhoneIdentity("another-uid", identity().phone_number, 9999999999))
        self.assertEqual(error.exception.status_code, 429)

    def test_daily_limit_and_private_storage(self) -> None:
        started = time.time()
        for i in range(3):
            with patch("playground.callbacks.time.time", return_value=started + i * 70):
                row = self.reserve()
                self.journal.update(row["id"], "completed", "Ended")
        with patch("playground.callbacks.time.time", return_value=started + 220):
            with self.assertRaises(HTTPException) as error:
                self.reserve()
        self.assertEqual(error.exception.status_code, 429)
        raw = self.journal.path.read_bytes()
        self.assertNotIn(identity().phone_number.encode(), raw)
        self.assertNotIn(identity().uid.encode(), raw)

    def test_status_receipt_is_bound_to_one_call(self) -> None:
        first, second = self.reserve(), self.reserve(identity(1))
        proof = self.journal.receipt(first["id"])
        self.assertEqual(self.journal.status(first["id"], proof)["phase"], "preparing")
        with self.assertRaises(HTTPException):
            self.journal.status(second["id"], proof)
        with self.assertRaises(HTTPException):
            self.journal.status(first["id"], "")


class CallbackAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.service = SimpleNamespace(submit=AsyncMock(return_value={"phase": "preparing"}))
        self.old = getattr(app.state, "callbacks", None)
        app.state.callbacks = self.service
        self.addCleanup(setattr, app.state, "callbacks", self.old)

    def test_only_verified_identity_reaches_the_dial_service(self) -> None:
        person = identity()
        key = str(uuid4())
        with patch("playground.app.verified_callback_identity", return_value=person):
            response = self.client.post("/api/calls", json={
                "framework": "livekit-agents", "request_id": key,
            })
        self.assertEqual(response.status_code, 202)
        self.service.submit.assert_awaited_once_with(person, "livekit-agents", key)

    def test_destination_prompt_uid_and_invalid_framework_are_rejected(self) -> None:
        for extra in ({"phone_number": PHONE}, {"uid": "other"}, {"prompt": "change"},
                      {"code": "print('hi')"}, {"framework": "other"}):
            response = self.client.post("/api/calls", json={
                "framework": "pipecat", "request_id": str(uuid4()), **extra,
            })
            self.assertEqual(response.status_code, 422)
        self.service.submit.assert_not_awaited()

    def test_missing_invalid_and_expired_proofs_never_dial(self) -> None:
        body = {"framework": "pipecat", "request_id": str(uuid4())}
        with patch("playground.app.verified_callback_identity", side_effect=HTTPException(401)):
            self.assertEqual(self.client.post("/api/calls", json=body).status_code, 401)
        self.assertEqual(self.client.post("/api/calls", content="x" * 513,
                                         headers={"content-type": "application/json"}).status_code,
                         413)
        self.service.submit.assert_not_awaited()


class CallLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = CallbackService({
            "state_dir": self.temp.name, "hash_key": "fixture-key" * 4,
            "outbound_trunk_id": "ST_fixture", "caller_id": PHONE,
        })
        self.service.client = SimpleNamespace(
            room=SimpleNamespace(create_room=AsyncMock(), delete_room=AsyncMock(),
                                 list_participants=AsyncMock()),
            sip=SimpleNamespace(create_sip_participant=AsyncMock()),
            agent_dispatch=SimpleNamespace(create_dispatch=AsyncMock()),
        )
        self.person = identity()
        self.row, _ = self.service.journal.reserve(self.person, "livekit-agents", str(uuid4()))
        self.call_id = self.row["id"]

    def status(self):
        return self.service.journal.status(self.call_id,
                                           self.service.journal.receipt(self.call_id))

    async def test_answer_gates_dispatch_and_completion_cleans_room(self) -> None:
        async def answer(*args, **kwargs):
            self.assertEqual(self.status()["phase"], "ringing")
            self.service.client.agent_dispatch.create_dispatch.assert_not_awaited()
        self.service.client.sip.create_sip_participant.side_effect = answer
        self.service.client.room.list_participants.return_value = SimpleNamespace(participants=[])
        await self.service.run(self.call_id, "livekit-agents", self.person.phone_number)
        request = self.service.client.sip.create_sip_participant.call_args.args[0]
        self.assertEqual(request.sip_call_to, self.person.phone_number)
        self.assertTrue(request.wait_until_answered)
        self.assertEqual(request.max_call_duration.seconds, 120)
        self.assertEqual(request.ringing_timeout.seconds, 45)
        self.service.client.agent_dispatch.create_dispatch.assert_awaited_once()
        self.service.client.room.delete_room.assert_awaited_once()
        self.assertTrue(self.status()["answered"])
        self.assertEqual(self.status()["phase"], "completed")

    async def test_unanswered_call_never_dispatches_or_claims_answer(self) -> None:
        self.service.client.sip.create_sip_participant.side_effect = api.SipCallError(
            "deadline_exceeded", "fixture", status=408, metadata={"sip_status_code": "480"}
        )
        await self.service.run(self.call_id, "livekit-agents", self.person.phone_number)
        self.service.client.agent_dispatch.create_dispatch.assert_not_awaited()
        self.service.client.room.delete_room.assert_awaited_once()
        self.assertFalse(self.status()["answered"])
        self.assertEqual(self.status()["phase"], "failed")

    async def test_missing_trunk_is_failure_not_completed(self) -> None:
        self.service.client.sip.create_sip_participant.side_effect = api.TwirpError(
            "not_found", "fixture", status=404
        )
        await self.service.run(self.call_id, "livekit-agents", self.person.phone_number)
        self.assertEqual(self.status()["phase"], "failed")
        self.assertFalse(self.status()["answered"])

    async def test_cancelling_a_ringing_call_deletes_its_room(self) -> None:
        ringing = asyncio.Event()
        async def dial(*args, **kwargs):
            ringing.set()
            await asyncio.Event().wait()
        self.service.client.sip.create_sip_participant.side_effect = dial
        task = asyncio.create_task(self.service.run(self.call_id, "livekit-agents",
                                                  self.person.phone_number))
        await ringing.wait()
        task.cancel()
        await task
        self.assertEqual(self.status()["phase"], "cancelled")
        self.service.client.room.delete_room.assert_awaited_once()

    async def test_cleanup_refuses_unrelated_rooms(self) -> None:
        with self.assertRaises(ValueError):
            await self.service.delete_room("customer-production-room")
        self.service.client.room.delete_room.assert_not_awaited()

    async def test_stalled_dial_has_a_total_deadline_and_is_never_replayed(self) -> None:
        async def stalled(*args, **kwargs):
            await asyncio.Event().wait()
        self.service.client.sip.create_sip_participant.side_effect = stalled
        with patch("playground.callbacks.DIAL_TIMEOUT_SECONDS", 0.02):
            await asyncio.wait_for(
                self.service.run(self.call_id, "livekit-agents", self.person.phone_number), 1
            )
        self.service.client.sip.create_sip_participant.assert_awaited_once()
        self.service.client.agent_dispatch.create_dispatch.assert_not_awaited()
        self.service.client.room.delete_room.assert_awaited_once()
        self.assertEqual(self.status()["phase"], "failed")
        self.assertFalse(self.status()["answered"])


if __name__ == "__main__":
    unittest.main()
