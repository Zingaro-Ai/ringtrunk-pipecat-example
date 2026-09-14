"""Call your own phone using your RingTrunk trunk and the fixed example agent.

Run from the extracted ZIP: python call.py +91XXXXXXXXXX --confirm-own-number
This is a local operator CLI, not a public HTTP endpoint. A public callback
service must verify phone ownership server-side and enforce its own usage limits.
"""

import argparse
import asyncio
import inspect
import os
import re
from uuid import uuid4

from config import load_config
from google.protobuf.duration_pb2 import Duration

from livekit import api


def indian_mobile(value: str) -> str:
    number = re.sub(r"[\s()-]", "", value)
    if re.fullmatch(r"[6-9][0-9]{9}", number):
        number = "+91" + number
    if not re.fullmatch(r"\+91[6-9][0-9]{9}", number):
        raise ValueError("Use an Indian mobile number in +91 format or 10-digit form.")
    return number


async def place_call(destination: str) -> None:
    destination = indian_mobile(destination)
    config = await asyncio.to_thread(load_config)
    caller_id = indian_mobile(os.environ["CALLER_ID"])
    if destination == caller_id:
        raise ValueError("The destination must differ from your RingTrunk caller ID.")
    trunk_id = os.environ.get("OUTBOUND_TRUNK_ID", "").strip()
    if not trunk_id or trunk_id.startswith("<"):
        raise ValueError("Set OUTBOUND_TRUNK_ID to your LiveKit outbound trunk ID.")
    framework = "pipecat" if config.framework == "pipecat" else "livekit"
    room_name = f"ringtrunk-{framework}-out-{uuid4().hex}"
    identity = "demo-callee"
    # API 1.2 adds automatic regional retries. Dial requests must never be replayed.
    options = (
        {"failover": False} if "failover" in inspect.signature(api.LiveKitAPI).parameters else {}
    )
    async with api.LiveKitAPI(**options) as client:
        await client.room.create_room(api.CreateRoomRequest(name=room_name, empty_timeout=30))
        try:
            print("Calling your phone; waiting for it to be answered…")
            # Both ringing and the connected phone leg have explicit upper bounds.
            async with asyncio.timeout(55):
                await client.sip.create_sip_participant(
                    api.CreateSIPParticipantRequest(
                        sip_trunk_id=trunk_id,
                        sip_call_to=destination,
                        sip_number=caller_id[3:],
                        room_name=room_name,
                        participant_identity=identity,
                        participant_name="Demo caller",
                        wait_until_answered=True,
                        ringing_timeout=Duration(seconds=45),
                        max_call_duration=Duration(seconds=config.max_call_seconds),
                    ), timeout=55,
                )
            print("Answered. Connecting the AI demo agent…")
            if config.framework == "pipecat":
                from bot import run_bot

                # The webhook deliberately ignores this outbound room prefix.
                await run_bot(room_name, identity)
            else:
                await client.agent_dispatch.create_dispatch(
                    api.CreateAgentDispatchRequest(
                        agent_name="ringtrunk-livekit-demo", room=room_name
                    )
                )
                # Keep the CLI alive until hangup; clean up if the worker is unavailable.
                agent_seen = False
                start = asyncio.get_running_loop().time()
                async with asyncio.timeout(config.max_call_seconds + 5):
                    while True:
                        participants = await client.room.list_participants(
                            api.ListParticipantsRequest(room=room_name)
                        )
                        if not any(p.identity == identity for p in participants.participants):
                            break
                        agent_seen = agent_seen or any(
                            p.kind == api.ParticipantInfo.AGENT for p in participants.participants
                        )
                        if not agent_seen and asyncio.get_running_loop().time() - start > 15:
                            raise RuntimeError("The named LiveKit agent worker did not join.")
                        await asyncio.sleep(0.5)
        finally:
            try:
                await client.room.delete_room(api.DeleteRoomRequest(room=room_name))
            except api.TwirpError:
                pass  # A completed call may already have closed the room.


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phone", help="Your own Indian mobile number")
    parser.add_argument("--confirm-own-number", action="store_true")
    args = parser.parse_args()
    if not args.confirm_own_number:
        parser.error("Use --confirm-own-number only when this is your own phone.")
    try:
        asyncio.run(place_call(args.phone))
    except (ValueError, KeyError, RuntimeError) as error:
        parser.exit(1, f"Configuration error: {error}\n")
    except (api.TwirpError, TimeoutError):
        # Provider error strings can include numbers or infrastructure details.
        parser.exit(1, "Call did not complete. Check the trunk and agent worker in your project.\n")


if __name__ == "__main__":
    main()
