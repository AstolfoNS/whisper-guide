import asyncio
from types import SimpleNamespace

import pytest

from app.speech import (
    RecognitionUpdate,
    SpeechServices,
    SpeechStream,
    SpeechUnavailableError,
)


def test_stream_uses_duck_typed_recognizer() -> None:
    stream = SpeechStream(recognizer=lambda pcm: "识别结果")

    result = stream.accept_pcm(b"\x00\x00")

    assert result == RecognitionUpdate("识别结果")


def test_empty_and_closed_stream_do_not_emit_results() -> None:
    stream = SpeechStream(recognizer=lambda pcm: "should not be called")
    assert stream.accept_pcm(b"") is None

    stream.close()
    assert stream.accept_pcm(b"\x00\x00") is None
    assert not stream.is_speech(b"\x00\x00")


def test_vad_fallback_uses_pcm_energy() -> None:
    stream = SpeechStream()

    assert not stream.is_speech(b"\x00\x00" * 160)
    assert stream.is_speech(b"\xff\x7f" * 160)


def test_create_stream_without_model_is_safe(tmp_path) -> None:
    settings = SimpleNamespace(speech_model_dir=tmp_path / "missing")
    services = SpeechServices(settings)

    stream = services.create_stream()

    assert not stream.available
    assert services.status()["stt"] == "unavailable"


def test_empty_tts_text_does_not_require_engine() -> None:
    services = SpeechServices()

    assert asyncio.run(services.synthesize("   ")) == b""


def test_tts_reports_missing_optional_dependency(monkeypatch) -> None:
    # Keep this deterministic even when edge-tts happens to be installed.
    import builtins

    real_import = builtins.__import__

    def fail_edge_tts(name, *args, **kwargs):
        if name == "edge_tts":
            raise ImportError("test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_edge_tts)
    with pytest.raises(SpeechUnavailableError):
        asyncio.run(SpeechServices().synthesize("你好"))
