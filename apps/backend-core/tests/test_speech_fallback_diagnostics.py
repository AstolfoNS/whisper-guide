"""Readiness and fallback expose bounded diagnostics without replaying PCM."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.infrastructure.speech import (
    FallbackSpeechStream,
    ProviderSpeechServices,
    RecognitionUpdate,
    SpeechStream,
    SpeechUnavailableError,
    _model_files,
)
from app.infrastructure.speech_websocket import WebSocketSpeechError


class DeferredRecognizer:
    def __init__(self, failure=None):
        self.config = SimpleNamespace(timeout_seconds=2.0)
        self.ready = False
        self.failure = failure
        self.initialized = asyncio.Event()
        self.frames = []
        self.budgets = []
        self.closed = False

    async def wait_ready(self, timeout=None):
        self.budgets.append(timeout)
        await self.initialized.wait()
        if self.failure:
            raise self.failure
        self.ready = True
        return True

    def __call__(self, pcm):
        self.frames.append(pcm)
        if self.failure:
            raise self.failure
        return RecognitionUpdate("ready")

    def close(self):
        self.closed = True
        self.ready = False


def test_local_stream_is_ready_without_a_network_readiness_hook():
    async def scenario():
        stream = SpeechStream(recognizer=lambda pcm: "local")
        assert stream.ready
        assert await stream.wait_ready()
        stream.close()
        assert not stream.ready
        with pytest.raises(SpeechUnavailableError):
            await stream.wait_ready()

    asyncio.run(scenario())


def test_readiness_wait_delegates_without_sending_or_decoding_pcm():
    async def scenario():
        recognizer = DeferredRecognizer()
        stream = SpeechStream(recognizer=recognizer)
        task = asyncio.create_task(stream.wait_ready(timeout=0.5))
        await asyncio.sleep(0)
        assert not stream.ready
        assert recognizer.frames == []
        recognizer.initialized.set()
        assert await task
        assert stream.ready
        assert recognizer.budgets == [0.5]
        stream.close()

    asyncio.run(scenario())


def test_handshake_failure_switches_to_local_with_safe_reason():
    async def scenario():
        error = WebSocketSpeechError(
            "HTTP_ERROR", stage="connect", http_status=401, provider_error_code=45000001
        )
        recognizer = DeferredRecognizer(error)
        recognizer.initialized.set()
        remote = SpeechStream(recognizer=recognizer, provider_name="cloud")
        local = SpeechStream(recognizer=lambda pcm: "local")
        stream = FallbackSpeechStream(remote, local)
        assert not stream.ready
        assert await stream.wait_ready()
        assert stream.ready
        assert stream.fallback_active
        assert stream.fallback_reason == {
            "error_code": "HTTP_ERROR",
            "stage": "connect",
            "http_status": 401,
            "provider_error_code": 45000001,
            "timeout_ms": 2000,
        }
        assert recognizer.frames == []
        assert recognizer.closed
        reason = stream.fallback_reason.copy()
        assert await stream.wait_ready()
        assert stream.fallback_reason == reason
        stream.close()

    asyncio.run(scenario())


def test_readiness_cancellation_does_not_trigger_local_takeover():
    async def scenario():
        recognizer = DeferredRecognizer()
        stream = FallbackSpeechStream(
            SpeechStream(recognizer=recognizer, provider_name="cloud"),
            SpeechStream(recognizer=lambda pcm: "local"),
        )
        task = asyncio.create_task(stream.wait_ready())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not stream.fallback_active
        assert stream.fallback_reason is None
        assert not recognizer.closed
        stream.close()
        assert not stream.ready

    asyncio.run(scenario())


def test_failed_readiness_without_a_local_model_remains_an_explicit_failure():
    async def scenario():
        recognizer = DeferredRecognizer(WebSocketSpeechError("TIMEOUT", stage="initialize"))
        recognizer.initialized.set()
        stream = FallbackSpeechStream(SpeechStream(recognizer=recognizer), SpeechStream())
        with pytest.raises(SpeechUnavailableError) as failure:
            await stream.wait_ready()
        assert failure.value.failure_reason["stage"] == "initialize"
        assert failure.value.failure_reason["timeout_ms"] == 2000
        assert not stream.fallback_active
        stream.close()

    asyncio.run(scenario())


def test_result_failure_preserves_diagnostics_and_sends_each_pcm_frame_once():
    recognizer = DeferredRecognizer(WebSocketSpeechError("TIMEOUT", stage="result"))
    remote = SpeechStream(recognizer=recognizer, provider_name="cloud")
    local_frames = []

    def local(pcm):
        local_frames.append(pcm)
        return RecognitionUpdate("local", is_final=True, segment_id=0)

    stream = FallbackSpeechStream(remote, SpeechStream(recognizer=local))
    assert stream.accept_pcm(b"first").text == "local"
    assert stream.fallback_reason["stage"] == "result"
    assert stream.fallback_reason["error_code"] == "TIMEOUT"
    assert stream.fallback_reason["timeout_ms"] == 2000
    stream.accept_pcm(b"second")
    assert recognizer.frames == [b"first"]
    assert local_frames == [b"first", b"second"]
    stream.close()


def test_malformed_exception_metadata_cannot_leak_into_fallback_reason():
    failure = RuntimeError("private token and upstream response body")
    failure.error_code = ["private token"]
    failure.stage = {"private": "body"}
    failure.http_status = True
    failure.provider_error_code = "private token"
    failure.timeout_ms = 10**100

    def fail(_pcm):
        raise failure

    stream = FallbackSpeechStream(
        SpeechStream(recognizer=fail), SpeechStream(recognizer=lambda pcm: "local")
    )
    assert stream.accept_pcm(b"audio").text == "local"
    assert stream.fallback_reason == {
        "error_code": "UNKNOWN_ERROR",
        "stage": "unknown",
        "http_status": None,
        "provider_error_code": None,
        "timeout_ms": None,
    }
    assert "private" not in json.dumps(stream.fallback_reason)
    assert all(not isinstance(value, Exception) for value in stream.fallback_reason.values())
    stream.close()


def test_creation_capacity_failure_retains_reason_on_local_stream(monkeypatch):
    import app.infrastructure.speech_websocket as websocket_speech

    def exhausted(*args, **kwargs):
        raise WebSocketSpeechError("NETWORK_ERROR", stage="capacity")

    monkeypatch.setattr(websocket_speech, "WebSocketStreamingRecognizer", exhausted)
    settings = SimpleNamespace(
        speech_model_dir=None,
        stt_provider="cloud",
        cloud_opt_in=True,
        cloud_stt_protocol="volcengine-ws",
        cloud_stt_endpoint="wss://speech.example/asr",
        cloud_stt_auth_mode="api-key",
        cloud_stt_resource_id="resource",
        cloud_stt_access_token="synthetic-test-token",
        cloud_stt_timeout_ms=2000,
    )
    services = ProviderSpeechServices(settings)
    local = SpeechStream(recognizer=lambda pcm: "local")
    monkeypatch.setattr(services.registry.local, "create_stream", lambda **kwargs: local)
    assert services.create_stream() is local
    assert local.fallback_reason["stage"] == "capacity"
    assert local.fallback_reason["timeout_ms"] == 2000
    local.close()


@pytest.mark.parametrize("keep_fp32", [True, False])
def test_model_selection_keeps_fp32_decoder_with_int8_encoder_and_joiner(tmp_path, keep_fp32):
    (tmp_path / "tokens.txt").write_text("tokens")
    for component in ("encoder", "decoder", "joiner"):
        (tmp_path / f"{component}-epoch.int8.onnx").write_bytes(b"int8")
        (tmp_path / f"{component}-epoch.onnx").write_bytes(b"fp32")
    if not keep_fp32:
        (tmp_path / "decoder-epoch.onnx").write_bytes(b"")
    selected = _model_files(tmp_path)
    assert selected["encoder"].name == "encoder-epoch.int8.onnx"
    assert selected["joiner"].name == "joiner-epoch.int8.onnx"
    assert selected["decoder"].name == (
        "decoder-epoch.onnx" if keep_fp32 else "decoder-epoch.int8.onnx"
    )


def test_partial_hypotheses_replace_text_without_accumulation():
    phrases = ["我将", "我将继续", "我将继续立足", "我将继续立足岗位"]
    updates = iter(RecognitionUpdate(text, segment_id=0) for text in phrases)
    stream = SpeechStream(recognizer=lambda pcm: next(updates))
    assert [stream.accept_pcm(b"one-frame").text for _ in phrases] == phrases
