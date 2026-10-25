import pytest
from pydantic import ValidationError

from app.config import Settings
from app.domain.session import SessionOptions, SpeechSession


@pytest.mark.parametrize("value", [0, -1, 1, 999, 1.5, 1000.5, 1000.0, "3000", True, None])
def test_silence_timeout_rejects_below_minimum_or_non_integer_values(value: object) -> None:
    with pytest.raises(ValidationError):
        SessionOptions.model_validate({"silence_timeout_ms": value})


@pytest.mark.parametrize("value", [1000, 1250, 3000])
def test_silence_timeout_accepts_integer_milliseconds(value: int) -> None:
    assert SessionOptions(silence_timeout_ms=value).silence_timeout_ms == value


def test_default_silence_timeout_environment_enforces_minimum(monkeypatch) -> None:
    monkeypatch.setenv("WHISPER_GUIDE_DEFAULT_SILENCE_TIMEOUT_MS", "999")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
    monkeypatch.setenv("WHISPER_GUIDE_DEFAULT_SILENCE_TIMEOUT_MS", "1000")
    assert Settings(_env_file=None).default_silence_timeout_ms == 1000


def test_minimum_timeout_prompts_only_after_full_second() -> None:
    session = SpeechSession("test", "大家好。", silence_timeout_ms=1000)
    session.handle("start", 0)
    assert session.handle("tick", 999)["state"] == "LISTENING"
    assert session.handle("tick", 1000)["state"] == "PROMPTING"
