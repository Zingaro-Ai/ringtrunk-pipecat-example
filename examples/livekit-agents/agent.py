"""Native LiveKit Agents example: answer inbound RingTrunk phone calls.

The SIP dispatch rule selects this named agent, with one room per caller.
Run: python agent.py download-files, then python agent.py dev.
"""

import asyncio
import os

from config import GREETING, load_config
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    AutoSubscribe,
    JobContext,
    TurnHandlingOptions,
    cli,
)
from livekit.agents.voice import room_io
from livekit.plugins import cartesia, deepgram, openai, silero

from livekit import api, rtc

server = AgentServer()


@server.rtc_session(agent_name="ringtrunk-livekit-demo")
async def inbound_agent(ctx: JobContext) -> None:
    # Scope this example to the rooms created by its own inbound dispatch rule.
    if not ctx.room.name.startswith("ringtrunk-livekit-"):
        ctx.shutdown(reason="This agent accepts its inbound demo rooms only")
        return

    config = await asyncio.to_thread(load_config)
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    try:
        caller = await asyncio.wait_for(
            ctx.wait_for_participant(kind=rtc.ParticipantKind.PARTICIPANT_KIND_SIP),
            timeout=30,
        )
    except TimeoutError:
        ctx.shutdown(reason="No inbound SIP caller joined")
        return

    ended = asyncio.Event()

    @ctx.room.on("participant_disconnected")
    def caller_left(participant: rtc.RemoteParticipant) -> None:
        if participant.identity == caller.identity:
            ended.set()

    @ctx.room.on("disconnected")
    def room_disconnected(_reason: rtc.DisconnectReason) -> None:
        ended.set()

    session = AgentSession(
        vad=await asyncio.to_thread(silero.VAD.load),
        # Use speech/silence endpointing instead of the default turn detector model.
        turn_handling=TurnHandlingOptions(turn_detection="vad"),
        stt=deepgram.STT(model="nova-3", language="en-US"),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=cartesia.TTS(model="sonic-3", voice=os.environ["CARTESIA_VOICE_ID"]),
    )

    @session.on("close")
    def session_closed(_event) -> None:
        ended.set()

    try:
        async with asyncio.timeout(config.max_call_seconds):
            await session.start(
                room=ctx.room,
                agent=Agent(instructions=config.system_prompt),
                room_options=room_io.RoomOptions(
                    participant_identity=caller.identity,
                    text_input=False,
                    text_output=False,
                    close_on_disconnect=True,
                ),
                record=False,
            )
            if not ended.is_set():
                session.say(GREETING, allow_interruptions=True)
            await ended.wait()
    except TimeoutError:
        pass
    finally:
        await session.aclose()
        try:
            await ctx.api.room.remove_participant(
                api.RoomParticipantIdentity(room=ctx.room.name, identity=caller.identity)
            )
        except api.TwirpError:
            pass  # A normal caller hangup already removed the SIP participant.
        ctx.shutdown(reason="Inbound demo complete")


if __name__ == "__main__":
    cli.run_app(server)
