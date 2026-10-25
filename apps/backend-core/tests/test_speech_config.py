"""The session transport stays 16 kHz across native browser device rates."""

import pytest
from pydantic import ValidationError

from app.config import MODEL_SETTINGS_FIELDS, Settings, clone_model_settings


@pytest.mark.parametrize("value", [16_000, "16000", " 16000 "])
def test_session_sample_rate_accepts_only_the_fixed_wire_rate(value, monkeypatch):
    monkeypatch.delenv("WHISPER_GUIDE_SPEECH_SAMPLE_RATE", raising=False)
    settings = Settings(speech_sample_rate=value, _env_file=None)
    assert settings.speech_sample_rate == 16_000
    assert type(settings.speech_sample_rate) is int


def test_session_sample_rate_default_is_16_khz(monkeypatch):
    monkeypatch.delenv("WHISPER_GUIDE_SPEECH_SAMPLE_RATE", raising=False)
    assert Settings(_env_file=None).speech_sample_rate == 16_000


@pytest.mark.parametrize(
    "value", [8000, 24000, 44100, 48000, 0, -1, True, None, 16000.0, "44100", "16k", ""]
)
def test_unsupported_session_sample_rates_fail_with_actionable_guidance(value):
    with pytest.raises(ValidationError) as caught:
        Settings(speech_sample_rate=value, _env_file=None)
    errors = caught.value.errors()
    assert errors[0]["loc"] == ("speech_sample_rate",)
    message = errors[0]["msg"]
    assert "WHISPER_GUIDE_SPEECH_SAMPLE_RATE" in message
    assert "16000" in message
    assert "浏览器会转换" in message


def test_environment_rate_is_normalized_and_unsupported_device_rate_is_rejected(monkeypatch):
    monkeypatch.setenv("WHISPER_GUIDE_SPEECH_SAMPLE_RATE", "16000")
    assert Settings(_env_file=None).speech_sample_rate == 16_000
    monkeypatch.setenv("WHISPER_GUIDE_SPEECH_SAMPLE_RATE", "48000")
    with pytest.raises(ValidationError, match="WHISPER_GUIDE_SPEECH_SAMPLE_RATE"):
        Settings(_env_file=None)


def test_dotenv_rate_obeys_same_fixed_protocol_validation(tmp_path, monkeypatch):
    monkeypatch.delenv("WHISPER_GUIDE_SPEECH_SAMPLE_RATE", raising=False)
    dotenv = tmp_path / "sample-rate.env"
    dotenv.write_text("WHISPER_GUIDE_SPEECH_SAMPLE_RATE=16000\n")
    assert Settings(_env_file=dotenv).speech_sample_rate == 16_000
    dotenv.write_text("WHISPER_GUIDE_SPEECH_SAMPLE_RATE=44100\n")
    with pytest.raises(ValidationError, match="会话音频协议固定为 16000 Hz"):
        Settings(_env_file=dotenv)


def test_model_profile_cannot_change_the_session_wire_rate(monkeypatch):
    monkeypatch.delenv("WHISPER_GUIDE_SPEECH_SAMPLE_RATE", raising=False)
    original = Settings(_env_file=None)
    assert "speech_sample_rate" not in MODEL_SETTINGS_FIELDS
    cloned = clone_model_settings(original, {"speech_sample_rate": 48000, "llm_align_mode": "off"})
    assert cloned.speech_sample_rate == original.speech_sample_rate == 16_000
