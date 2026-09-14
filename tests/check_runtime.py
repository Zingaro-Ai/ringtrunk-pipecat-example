"""Offline SDK checks against an extracted ZIP, run in that example's environment.

python tests/check_runtime.py pipecat /path/to/extracted/ringtrunk-pipecat
python tests/check_runtime.py livekit-agents /path/to/extracted/ringtrunk-livekit-agents
All API operations are mocked; these checks never place or receive calls.
"""

import asyncio
import hashlib
import importlib
import json
import os
import sys
import unittest
from base64 import b64encode
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

FRAMEWORK = sys.argv[1]
EXAMPLE = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(EXAMPLE))
for key in [
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "OPENAI_API_KEY",
    "DEEPGRAM_API_KEY",
    "CARTESIA_API_KEY",
]:
    os.environ[key] = "offline-test-placeholder-0000000000000000"
os.environ["LIVEKIT_URL"] = "wss://offline-test.invalid"
os.environ["CARTESIA_VOICE_ID"] = "79a125e8-cd45-4c13-8a67-188112f4dd22"


class PipecatChecks(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bot = importlib.import_module("bot")
        cls.server = importlib.import_module("server")

    def test_webhook_signature_scope_duplicate_and_shutdown(self) -> None:
        from fastapi.testclient import TestClient

        from livekit import api

        bot_started = Mock()
        bot_cancelled = Mock()

        async def fake_bot(room: str, caller: str) -> None:
            bot_started(room, caller)
            try:
                await asyncio.Event().wait()
            finally:
                bot_cancelled()

        def signed(client, body: dict):
            payload = json.dumps(body)
            token = (
                api.AccessToken()
                .with_sha256(b64encode(hashlib.sha256(payload.encode()).digest()).decode())
                .to_jwt()
            )
            return client.post(
                "/livekit-webhook",
                content=payload,
                headers={"authorization": token, "content-type": "application/webhook+json"},
            )

        def joined(name: str, kind: str = "SIP") -> dict:
            return {
                "event": "participant_joined",
                "room": {"name": name},
                "participant": {"identity": "sip_test", "kind": kind},
            }

        with patch.object(self.server, "run_bot", fake_bot), TestClient(self.server.app) as client:
            self.assertEqual(client.post("/livekit-webhook", content="{}").status_code, 401)
            self.assertEqual(client.post("/livekit-webhook", content="x" * 70_000).status_code, 413)
            signed(client, joined("unrelated-room"))
            signed(client, joined("ringtrunk-pipecat-web", "STANDARD"))
            signed(client, joined("ringtrunk-pipecat-out-offline"))
            self.assertEqual(bot_started.call_count, 0)
            event = joined("ringtrunk-pipecat-offline-check")
            self.assertEqual(signed(client, event).status_code, 200)
            self.assertEqual(signed(client, event).status_code, 200)
            self.assertEqual(bot_started.call_count, 1)
            with patch.object(self.server, "MAX_CONCURRENT_CALLS", 1):
                self.assertEqual(signed(client, joined("ringtrunk-pipecat-other")).status_code, 503)
            self.assertEqual(
                signed(client, {"event": "room_finished", "room": event["room"]}).status_code, 200
            )
            # Replayed joins after the room ended must not resurrect an agent.
            self.assertEqual(signed(client, event).status_code, 200)
            self.assertEqual(bot_started.call_count, 1)
        self.assertEqual(bot_cancelled.call_count, 1)
        self.assertEqual(self.server.running, {})

    async def test_stale_join_does_not_start_pipeline(self) -> None:
        client = SimpleNamespace(
            room=SimpleNamespace(
                list_participants=AsyncMock(return_value=SimpleNamespace(participants=[]))
            )
        )
        context = AsyncMock()
        context.__aenter__.return_value = client
        with (
            patch.object(self.bot.api, "LiveKitAPI", return_value=context),
            patch.object(self.bot, "LiveKitTransport") as transport,
        ):
            await self.bot.run_bot("ringtrunk-pipecat-stale", "sip_test")
            transport.assert_not_called()

    async def test_real_provider_constructors_and_pipeline_cleanup(self) -> None:
        # Construct the actual pinned SDK classes, but never start a network transport.
        client = SimpleNamespace(
            room=SimpleNamespace(
                list_participants=AsyncMock(
                    return_value=SimpleNamespace(
                        participants=[
                            SimpleNamespace(
                                identity="sip_test", kind=self.bot.api.ParticipantInfo.SIP
                            )
                        ]
                    )
                ),
                remove_participant=AsyncMock(),
            )
        )
        context = AsyncMock()
        context.__aenter__.return_value = client
        runner = SimpleNamespace(run=AsyncMock())
        with (
            patch.object(self.bot.api, "LiveKitAPI", return_value=context),
            patch.object(self.bot, "PipelineRunner", return_value=runner),
            patch(
                "pipecat.turns.user_turn_strategies.default_user_turn_stop_strategies",
                side_effect=AssertionError("The demo must not load the Smart Turn model"),
            ),
        ):
            await self.bot.run_bot("ringtrunk-pipecat-offline", "sip_test")
        self.assertEqual(runner.run.await_count, 1)
        removed = client.room.remove_participant.call_args.args[0]
        self.assertEqual(removed.room, "ringtrunk-pipecat-offline")
        self.assertEqual(removed.identity, "sip_test")


class LiveKitChecks(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.agent = importlib.import_module("agent")

    def context(self, room_name: str = "ringtrunk-livekit-offline"):
        caller = SimpleNamespace(identity="sip_test")
        room = SimpleNamespace(name=room_name, handlers={})

        def on(name):
            def decorate(fn):
                room.handlers[name] = fn
                return fn

            return decorate

        room.on = on
        return SimpleNamespace(
            room=room,
            caller=caller,
            connect=AsyncMock(),
            wait_for_participant=AsyncMock(return_value=caller),
            shutdown=Mock(),
            api=SimpleNamespace(room=SimpleNamespace(remove_participant=AsyncMock())),
        )

    async def test_wrong_room_does_not_connect(self) -> None:
        ctx = self.context("unrelated-room")
        await self.agent.inbound_agent(ctx)
        ctx.connect.assert_not_called()
        ctx.shutdown.assert_called_once()

    async def test_no_sip_participant_exits(self) -> None:
        ctx = self.context()
        ctx.wait_for_participant.side_effect = TimeoutError
        await self.agent.inbound_agent(ctx)
        ctx.shutdown.assert_called_once()
        self.assertEqual(
            ctx.wait_for_participant.call_args.kwargs["kind"],
            self.agent.rtc.ParticipantKind.PARTICIPANT_KIND_SIP,
        )

    async def test_actual_provider_constructors(self) -> None:
        vad = await asyncio.to_thread(self.agent.silero.VAD.load)
        stt = self.agent.deepgram.STT(model="nova-3", language="en-US")
        llm = self.agent.openai.LLM(model="gpt-4o-mini")
        tts = self.agent.cartesia.TTS(model="sonic-3", voice=os.environ["CARTESIA_VOICE_ID"])
        session = self.agent.AgentSession(
            vad=vad, stt=stt, llm=llm, tts=tts,
            turn_handling=self.agent.TurnHandlingOptions(turn_detection="vad"),
        )
        self.assertEqual(session.turn_detection, "vad")
        await session.aclose()
        await llm.aclose()
        await stt.aclose()
        await tts.aclose()

    async def check_call(self, timeout: bool) -> None:
        ctx = self.context()
        session = SimpleNamespace(start=AsyncMock(), aclose=AsyncMock())
        session.on = lambda _event: lambda fn: fn
        session.say = Mock(
            side_effect=(
                None
                if timeout
                else lambda *a, **k: ctx.room.handlers["participant_disconnected"](ctx.caller)
            )
        )
        config = SimpleNamespace(
            system_prompt="Offline custom prompt", max_call_seconds=0.01 if timeout else 1
        )
        with ExitStack() as stack:
            for module, attribute in [
                (self.agent.deepgram, "STT"),
                (self.agent.openai, "LLM"),
                (self.agent.cartesia, "TTS"),
                (self.agent.silero.VAD, "load"),
            ]:
                stack.enter_context(patch.object(module, attribute))
            stack.enter_context(patch.object(self.agent, "load_config", return_value=config))
            session_factory = stack.enter_context(
                patch.object(self.agent, "AgentSession", return_value=session)
            )
            await self.agent.inbound_agent(ctx)
        self.assertEqual(session_factory.call_args.kwargs["turn_handling"]["turn_detection"], "vad")
        options = session.start.call_args.kwargs
        self.assertFalse(options["record"])
        self.assertFalse(options["room_options"].text_input)
        self.assertEqual(options["room_options"].participant_identity, "sip_test")
        session.aclose.assert_awaited_once()
        ctx.api.room.remove_participant.assert_awaited_once()
        ctx.shutdown.assert_called_once()

    async def test_caller_hangup_closes_session_and_phone_leg(self) -> None:
        await self.check_call(timeout=False)

    async def test_time_limit_closes_session_and_phone_leg(self) -> None:
        await self.check_call(timeout=True)


class OutboundChecks(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.call = importlib.import_module("call")

    def client(self):
        client = SimpleNamespace(
            room=SimpleNamespace(
                create_room=AsyncMock(),
                delete_room=AsyncMock(),
                list_participants=AsyncMock(return_value=SimpleNamespace(participants=[])),
            ),
            sip=SimpleNamespace(create_sip_participant=AsyncMock()),
            agent_dispatch=SimpleNamespace(create_dispatch=AsyncMock()),
        )
        context = AsyncMock()
        context.__aenter__.return_value = client
        return client, context

    async def test_bad_destination_never_reaches_provider(self) -> None:
        for destination in ["112", "+12025550123", "arbitrary", "1800123456"]:
            with patch.object(self.call.api, "LiveKitAPI") as client:
                with self.assertRaises(ValueError):
                    await self.call.place_call(destination)
                client.assert_not_called()

    async def test_own_caller_id_is_rejected(self) -> None:
        with (
            patch.dict(os.environ, {"CALLER_ID": "9000000001"}),
            patch.object(self.call.api, "LiveKitAPI") as client,
        ):
            with self.assertRaises(ValueError):
                await self.call.place_call("+919000000001")
            client.assert_not_called()

    async def test_unanswered_call_cleans_up_without_starting_an_agent(self) -> None:
        client, context = self.client()
        client.sip.create_sip_participant.side_effect = self.call.api.TwirpError(
            "unavailable", "offline test", status=503
        )
        with (
            patch.dict(os.environ, {"CALLER_ID": "9000000001", "OUTBOUND_TRUNK_ID": "ST_test"}),
            patch.object(self.call.api, "LiveKitAPI", return_value=context),
        ):
            with self.assertRaises(self.call.api.TwirpError):
                await self.call.place_call("+919000000002")
        client.agent_dispatch.create_dispatch.assert_not_called()
        client.room.delete_room.assert_awaited_once()

    async def test_answered_call_uses_bound_duration_and_then_starts_agent(self) -> None:
        client, context = self.client()
        order = []

        async def answer(*_args, **_kwargs):
            order.append("answer")

        async def start(*_args, **_kwargs):
            order.append("agent")

        client.sip.create_sip_participant.side_effect = answer
        client.agent_dispatch.create_dispatch.side_effect = start
        with ExitStack() as stack:
            stack.enter_context(
                patch.dict(os.environ, {"CALLER_ID": "9000000001", "OUTBOUND_TRUNK_ID": "ST_test"})
            )
            stack.enter_context(patch.object(self.call.api, "LiveKitAPI", return_value=context))
            if FRAMEWORK == "pipecat":
                bot = importlib.import_module("bot")
                stack.enter_context(patch.object(bot, "run_bot", side_effect=start))
            await self.call.place_call("+919000000002")
        self.assertEqual(order, ["answer", "agent"])
        request = client.sip.create_sip_participant.call_args.args[0]
        self.assertTrue(request.wait_until_answered)
        self.assertEqual(request.max_call_duration.seconds, 120)
        self.assertEqual(request.ringing_timeout.seconds, 45)
        self.assertEqual(request.sip_number, "9000000001")
        client.room.delete_room.assert_awaited_once()


if __name__ == "__main__":
    suite = unittest.TestSuite(
        [
            unittest.defaultTestLoader.loadTestsFromTestCase(
                PipecatChecks if FRAMEWORK == "pipecat" else LiveKitChecks
            ),
            unittest.defaultTestLoader.loadTestsFromTestCase(OutboundChecks),
        ]
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
