"""Answer an inbound RingTrunk caller in their own LiveKit Cloud room.

server.py starts this pipeline after verifying a LiveKit SIP-join webhook.
Caller -> LiveKit -> Deepgram -> OpenAI -> Cartesia -> LiveKit -> caller.
"""

import asyncio
import os
from datetime import timedelta

from config import GREETING, load_config
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.livekit.transport import LiveKitParams, LiveKitTransport
from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from livekit import api


async def run_bot(room_name: str, caller_identity: str) -> None:
    config = await asyncio.to_thread(load_config)
    async with api.LiveKitAPI() as client:
        # A delayed/retried webhook must not create a bot for a caller who already left.
        participants = await client.room.list_participants(
            api.ListParticipantsRequest(room=room_name)
        )
        if not any(
            p.identity == caller_identity and p.kind == api.ParticipantInfo.SIP
            for p in participants.participants
        ):
            return

        token = (
            api.AccessToken()
            .with_identity("ringtrunk-pipecat-agent")
            .with_name("RingTrunk AI demo")
            .with_ttl(timedelta(minutes=15))
            .with_grants(api.VideoGrants(room_join=True, room=room_name))
            .to_jwt()
        )
        transport = LiveKitTransport(
            url=os.environ["LIVEKIT_URL"],
            token=token,
            room_name=room_name,
            params=LiveKitParams(audio_in_enabled=True, audio_out_enabled=True),
        )
        stt = DeepgramSTTService(
            api_key=os.environ["DEEPGRAM_API_KEY"],
            settings=DeepgramSTTService.Settings(model="nova-3"),
        )
        llm = OpenAILLMService(
            api_key=os.environ["OPENAI_API_KEY"],
            settings=OpenAILLMService.Settings(model="gpt-4o-mini"),
        )
        tts = CartesiaTTSService(
            api_key=os.environ["CARTESIA_API_KEY"],
            settings=CartesiaTTSService.Settings(
                model="sonic-3", voice=os.environ["CARTESIA_VOICE_ID"]
            ),
        )
        context = LLMContext(
            messages=[
                {"role": "system", "content": config.system_prompt},
                {"role": "assistant", "content": GREETING},
            ]
        )
        aggregators = LLMContextAggregatorPair(
            context,
            user_params=LLMUserAggregatorParams(
                vad_analyzer=await asyncio.to_thread(SileroVADAnalyzer),
                # Use a fixed silence window; do not load the default Smart Turn model.
                user_turn_strategies=UserTurnStrategies(
                    stop=[SpeechTimeoutUserTurnStopStrategy()]
                ),
            ),
        )
        pipeline = Pipeline(
            [
                transport.input(),
                stt,
                aggregators.user(),
                llm,
                tts,
                transport.output(),
                aggregators.assistant(),
            ]
        )
        task = PipelineTask(pipeline, params=PipelineParams())

        @transport.event_handler("on_first_participant_joined")
        async def greet(_transport: LiveKitTransport, participant_id: str) -> None:
            if participant_id == caller_identity:
                await task.queue_frames([TTSSpeakFrame(GREETING)])

        @transport.event_handler("on_participant_left")
        async def caller_left(
            _transport: LiveKitTransport, participant_id: str, _reason: str
        ) -> None:
            if participant_id == caller_identity:
                await task.cancel()

        @transport.event_handler("on_disconnected")
        async def disconnected(_transport: LiveKitTransport) -> None:
            await task.cancel()

        try:
            # Bound the demo's duration and end the phone leg when time is up.
            async with asyncio.timeout(config.max_call_seconds):
                await PipelineRunner(handle_sigint=False).run(task)
        except TimeoutError:
            await task.cancel()
        finally:
            try:
                await client.room.remove_participant(
                    api.RoomParticipantIdentity(room=room_name, identity=caller_identity)
                )
            except api.TwirpError:
                pass  # The caller may already have hung up.
