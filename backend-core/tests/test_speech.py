import asyncio
from types import SimpleNamespace

import pytest

from app.infrastructure.speech import (
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


def test_stream_normalizes_repeated_streaming_prefix() -> None:
    stream = SpeechStream(recognizer=lambda pcm: "大大大大家好好好好")

    result = stream.accept_pcm(b"\x00\x00")

    assert result is not None
    assert result.text == "大家好"
    assert not result.is_final
    assert result.segment_id == 0


def test_recognition_update_carries_segment_and_filler_metadata() -> None:
    result = RecognitionUpdate(
        text="嗯",
        is_final=False,
        segment_id=3,
        is_filler_only=True,
    )

    assert result.segment_id == 3
    assert result.is_filler_only


def test_cloud_provider_requires_explicit_opt_in() -> None:
    from types import SimpleNamespace

    from app.infrastructure.speech import ProviderSpeechServices

    services = ProviderSpeechServices(
        SimpleNamespace(
            speech_model_dir=None,
            stt_provider="hybrid",
            cloud_opt_in=False,
            cloud_endpoint="https://example.invalid",
            cloud_access_token="token",
            cloud_app_id="app",
        )
    )

    status = services.status()
    assert status["provider"] == "local"
    assert status["cloud_enabled"] is False


def test_generic_provider_registry_exposes_capabilities_without_credentials() -> None:
    from app.infrastructure.speech import ProviderSpeechServices

    services = ProviderSpeechServices(
        SimpleNamespace(
            speech_model_dir=None,
            stt_provider="generic-model",
            stt_provider_configs=[
                {
                    "id": "generic-model",
                    "label": "通用流式模型",
                    "endpoint": "https://example.invalid/stt",
                    "access_token": "secret",
                    "model": "streaming-v1",
                }
            ],
            cloud_opt_in=True,
        )
    )

    catalog = services.provider_options()
    remote = next(item for item in catalog if item["id"] == "generic-model")
    assert remote["available"] is True
    assert remote["model"] == "streaming-v1"
    assert "access_token" not in remote
    assert services.status()["provider"] == "generic-model"


def test_remote_provider_can_start_without_local_model() -> None:
    from app.infrastructure.speech import ProviderSpeechServices

    services = ProviderSpeechServices(
        SimpleNamespace(
            speech_model_dir=None,
            stt_provider="generic-model",
            stt_provider_configs=[
                {
                    "id": "generic-model",
                    "endpoint": "https://example.invalid/stt",
                    "access_token": "secret",
                }
            ],
            cloud_opt_in=True,
        )
    )
    stream = services.create_stream(provider_name="generic-model", turn_token=7)
    assert stream.available
    stream.close()
