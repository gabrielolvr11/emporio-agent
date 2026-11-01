"""Settings from the environment and the .env file."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from emporio_agent import rules

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = ROOT / "runtime"
DEFAULT_MODEL = "anthropic:claude-haiku-5-5"


class ConfigError(Exception):
    """A setting is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    model: str
    """PydanticAI model string, e.g. anthropic:claude-haiku-5-5."""
    agent_now: str | None
    """Fixed "now" (ISO 8601, Campo Grande time) for demos and evals; None for the real clock."""


def load_settings() -> Settings:
    load_dotenv(ROOT / ".env")
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
    model = os.environ.get("MODEL") or DEFAULT_MODEL
    if model.startswith("anthropic:") and not os.environ.get("ANTHROPIC_API_KEY"):
        raise ConfigError("ANTHROPIC_API_KEY is not set: copy .env.example to .env and fill it in")
    agent_now = os.environ.get("AGENT_NOW") or None
    try:
        rules.current_time(agent_now)
    except ValueError as exc:
        raise ConfigError(f"AGENT_NOW is not an ISO 8601 date-time: {agent_now!r}") from exc
    return Settings(model, agent_now)
