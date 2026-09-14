"""Run a server-owned example in its own Python environment, without phone inputs."""

import asyncio
import logging
import os
import sys


def main() -> None:
    framework = sys.argv[1]
    sys.path.insert(0, os.environ["RINGTRUNK_AGENT_DIRECTORY"])
    logging.basicConfig(level=logging.WARNING)
    if framework == "pipecat":
        from loguru import logger

        logger.remove()
        logger.add(sys.stderr, level="WARNING")
        from bot import run_bot

        print("RINGTRUNK_AGENT_READY", flush=True)
        # The parent sends only a random, server-created room ID after SIP answer.
        room = sys.stdin.readline().strip()
        if not room.startswith("ringtrunk-pipecat-out-") or len(room) > 100:
            raise ValueError("Invalid demo room")
        asyncio.run(run_bot(room, "demo-callee"))
    elif framework == "livekit-agents":
        from agent import inbound_agent
        from livekit.agents import AgentServer, cli

        server = AgentServer(
            host="127.0.0.1", port=0, num_idle_processes=1, drain_timeout=5,
            initialize_process_timeout=30,
        )
        server.rtc_session(agent_name="ringtrunk-playground-livekit")(inbound_agent)

        @server.on("worker_registered")
        def registered(_worker_id, _server_info) -> None:
            print("RINGTRUNK_AGENT_READY", flush=True)

        sys.argv = [sys.argv[0], "start", "--log-level", "warn"]
        cli.run_app(server)
    else:
        raise ValueError("Unknown example")


if __name__ == "__main__":
    main()
