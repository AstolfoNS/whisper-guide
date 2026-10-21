"""Shared session vocabulary; no speech engine or state transitions are implemented yet."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, StrictInt


class SessionState(StrEnum):
    READY = "READY"
    LISTENING = "LISTENING"
    PROMPTING = "PROMPTING"
    PAUSED = "PAUSED"
    TERMINATED = "TERMINATED"


class SessionOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    silence_timeout_ms: StrictInt = Field(default=3000, gt=0)
