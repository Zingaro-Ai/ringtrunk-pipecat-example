"""Read the downloaded prompt as data, and load only this example's .env file."""

import json
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env", override=False)

GREETING = "Hi! I'm an AI assistant in a RingTrunk demo. How can I help you today?"


@dataclass(frozen=True)
class AgentConfig:
    system_prompt: str
    max_call_seconds: int
    framework: str


def load_config() -> AgentConfig:
    path = BASE / "agent-config.json"
    if path.stat().st_size > 32_768:
        raise ValueError("agent-config.json is too large")
    data = json.loads(path.read_text(encoding="utf-8"))
    prompt = data.get("system_prompt")
    duration = data.get("max_call_seconds", 120)
    framework = data.get("framework")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4_000:
        raise ValueError("system_prompt must contain 1 to 4,000 characters")
    if type(duration) is not int or not 30 <= duration <= 600:
        raise ValueError("max_call_seconds must be an integer from 30 to 600")
    if framework not in {"pipecat", "livekit-agents"}:
        raise ValueError("framework must be pipecat or livekit-agents")
    return AgentConfig(system_prompt=prompt, max_call_seconds=duration, framework=framework)
