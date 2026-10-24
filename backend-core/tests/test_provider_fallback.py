import base64
from concurrent.futures import Future
from types import SimpleNamespace

import pytest

from app.infrastructure.speech import (
    FallbackSpeechStream,
    HttpStreamingProvider,
    ProviderSpeechServices,
    RecognitionUpdate,
    RemoteProviderConfig,
    SpeechStream,
    SpeechUnavailableError,
    _HTTPStreamingRecognizer,
)


class FakeStream:
    def __init__(self, updates=(), *, available=True):
        self.updates = iter(updates)
        self.available = available
        self.frames = []
        self.closed = False

    def accept_pcm(self, pcm):
        self.frames.append(pcm)
        result = next(self.updates, None)
        if isinstance(result, Exception):
            raise result
        return result

    def is_speech(self, pcm):
        return True

    def close(self):
        self.closed = True

    def reset(self):
        pass


class ControlledExecutor:
    def __init__(self):
        self.calls = []

    def submit(self, function, *args):
        future = Future()
        self.calls.append((function, args, future))
        return future


def config(**kwargs):
    return RemoteProviderConfig(
        id="cloud",
        label="Cloud",
        endpoint=kwargs.pop("endpoint", "https://speech.example/stt"),
        access_token="private-token",
        **kwargs,
    )


def test_remote_failure_uses_warm_local_and_monotonic_segment_ids():
    local = FakeStream(
        [
            RecognitionUpdate("大家", segment_id=0),
            RecognitionUpdate("大家好", is_final=True, segment_id=0),
            RecognitionUpdate("今天", segment_id=1),
        ]
    )
    remote = FakeStream(
        [
            RecognitionUpdate("大家好", is_final=True, segment_id=7),
            SpeechUnavailableError("offline"),
        ]
    )
    stream = FallbackSpeechStream(remote, local)
    # Existing providers need not implement the optional voice notification hook.
    assert stream.is_speech(b"first")
    assert stream.accept_pcm(b"first").segment_id == 7
    recovered = stream.accept_pcm(b"second")
    assert recovered.text == "大家好"
    assert recovered.is_final
    assert recovered.segment_id == 8
    assert stream.accept_pcm(b"third").segment_id == 9
    assert local.frames == [b"first", b"second", b"third"]
    assert remote.frames == [b"first", b"second"]
    assert remote.closed


def test_first_local_result_after_empty_fallback_gets_fresh_segment():
    local = FakeStream([None, None, RecognitionUpdate("大家好", segment_id=0)])
    remote = FakeStream(
        [
            RecognitionUpdate("大家", segment_id=6),
            SpeechUnavailableError("offline"),
        ]
    )
    stream = FallbackSpeechStream(remote, local)
    assert stream.accept_pcm(b"one").segment_id == 6
    assert stream.accept_pcm(b"two") is None
    assert stream.accept_pcm(b"three").segment_id == 7


def test_local_failure_does_not_disrupt_healthy_cloud():
    stream = FallbackSpeechStream(
        FakeStream([RecognitionUpdate("大家好")]),
        FakeStream([SpeechUnavailableError("local unavailable")]),
    )
    assert stream.accept_pcm(b"audio").text == "大家好"


def test_remote_failure_with_no_local_model_is_explicit():
    stream = FallbackSpeechStream(
        FakeStream([SpeechUnavailableError("offline")]),
        FakeStream(available=False),
    )
    with pytest.raises(SpeechUnavailableError):
        stream.accept_pcm(b"audio")


@pytest.mark.parametrize("requires_opt_in", [True, False])
def test_provider_cannot_bypass_global_privacy_opt_in(requires_opt_in):
    provider = HttpStreamingProvider(
        config(requires_opt_in=requires_opt_in), opt_in=False, sample_rate=16000
    )
    assert not provider.configured
    assert provider.descriptor()["requires_opt_in"] is True
    with pytest.raises(SpeechUnavailableError):
        provider.create_stream(vad=None, turn_token=1)


@pytest.mark.parametrize(
    "endpoint,allowed",
    [
        ("https://speech.example/stt", True),
        ("http://speech.example/stt", False),
        ("http://127.0.0.1:9000/stt", True),
        ("http://localhost:9000/stt", True),
        ("https://user:password@speech.example/stt", False),
        ("file:///tmp/stt", False),
    ],
)
def test_microphone_transport_requires_tls_except_loopback(endpoint, allowed):
    provider = HttpStreamingProvider(config(endpoint=endpoint), opt_in=True, sample_rate=16000)
    assert provider.configured is allowed


def test_runtime_cloud_settings_override_legacy_registry_cloud_entry():
    services = ProviderSpeechServices(
        SimpleNamespace(
            speech_model_dir=None,
            stt_provider="cloud",
            cloud_opt_in=True,
            cloud_stt_endpoint="https://new.example/stt",
            cloud_stt_access_token="new-token",
            stt_provider_configs=[
                {"id": "cloud", "endpoint": "https://old.example/stt", "access_token": "old"},
                {"id": "other", "endpoint": "https://other.example/stt", "access_token": "other"},
            ],
        )
    )
    provider = services.registry.provider("cloud")
    assert provider.config.endpoint == "https://new.example/stt"
    assert provider.config.access_token == "new-token"
    assert services.registry.provider("other") is not None


def test_cloud_requests_coalesce_frames_and_bound_pending_pcm(monkeypatch):
    import app.infrastructure.speech as speech

    executor = ControlledExecutor()
    monkeypatch.setattr(speech, "_CLOUD_REQUESTS", executor)
    recognizer = _HTTPStreamingRecognizer(config(), 16000, 3)
    for _ in range(19):
        assert recognizer(bytes(640)) is None
    assert not executor.calls
    assert recognizer(bytes(640)) is None
    assert len(executor.calls) == 1
    assert len(executor.calls[0][1][0]) == 12800
    for _ in range(50):
        assert recognizer(bytes(640)) is None
    assert len(recognizer._buffer) == 32000
    with pytest.raises(SpeechUnavailableError):
        recognizer(bytes(640))
    assert len(executor.calls) == 1
    recognizer.close()


def test_close_discards_running_remote_result_without_more_requests(monkeypatch):
    import app.infrastructure.speech as speech

    executor = ControlledExecutor()
    monkeypatch.setattr(speech, "_CLOUD_REQUESTS", executor)
    recognizer = _HTTPStreamingRecognizer(config(), 16000, 3)
    recognizer(bytes(12800))
    future = executor.calls[0][2]
    assert future.set_running_or_notify_cancel()
    recognizer.close()
    future.set_result(RecognitionUpdate("stale", is_final=True))
    assert recognizer(bytes(12800)) is None
    assert len(executor.calls) == 1
    assert not recognizer._buffer


def test_completed_cloud_endpoint_advances_next_request_segment(monkeypatch):
    import app.infrastructure.speech as speech

    executor = ControlledExecutor()
    monkeypatch.setattr(speech, "_CLOUD_REQUESTS", executor)
    recognizer = _HTTPStreamingRecognizer(config(), 16000, 8)
    recognizer(bytes(12800))
    executor.calls[0][2].set_result(RecognitionUpdate("大家好", segment_id=3, is_final=True))
    result = recognizer(bytes(12800))
    assert result.is_final
    assert result.segment_id == 3
    assert executor.calls[1][1][1] == 4
    recognizer.close()


def test_gateway_contract_includes_stream_and_turn_identity(monkeypatch):
    import httpx

    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"text": "大家好", "is_final": True, "segment_id": 2}

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, endpoint, **kwargs):
            calls.append((endpoint, kwargs))
            return Response()

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    recognizer = _HTTPStreamingRecognizer(config(), 16000, 9)
    result = recognizer._request(b"pcm", 2)
    assert result == RecognitionUpdate("大家好", is_final=True, segment_id=2)
    payload = calls[0][1]["json"]
    assert payload["turn_token"] == 9
    assert payload["stream_id"] == recognizer.stream_id
    assert payload["audio"] == base64.b64encode(b"pcm").decode("ascii")
    assert payload["sample_rate"] == 16000
    assert calls[0][1]["headers"]["Authorization"] == "Bearer private-token"


def test_gateway_failure_falls_back_without_waiting_for_another_network_call(monkeypatch):
    import app.infrastructure.speech as speech

    executor = ControlledExecutor()
    monkeypatch.setattr(speech, "_CLOUD_REQUESTS", executor)
    recognizer = _HTTPStreamingRecognizer(config(), 16000, 8)
    remote = speech.SpeechStream(recognizer=recognizer)
    local = FakeStream([RecognitionUpdate("大家"), RecognitionUpdate("大家好", is_final=True)])
    stream = FallbackSpeechStream(remote, local)
    assert stream.accept_pcm(bytes(12800)) is None
    executor.calls[0][2].set_exception(SpeechUnavailableError("timeout"))
    result = stream.accept_pcm(bytes(640))
    assert result.text == "大家好"
    assert result.is_final
    assert stream.active is local
    assert len(executor.calls) == 1


def test_cloud_request_enforces_total_deadline(monkeypatch):
    import asyncio
    import time

    import httpx

    class SlowClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, *args, **kwargs):
            await asyncio.sleep(10)

    monkeypatch.setattr(httpx, "AsyncClient", SlowClient)
    recognizer = _HTTPStreamingRecognizer(config(timeout_seconds=0.02), 16000, 9)
    started = time.monotonic()
    with pytest.raises(SpeechUnavailableError):
        recognizer._request(b"pcm", 0)
    assert time.monotonic() - started < 0.5


def test_closed_fallback_does_not_decode_or_send_audio():
    local = FakeStream([RecognitionUpdate("unexpected")])
    remote = FakeStream([RecognitionUpdate("unexpected")])
    stream = FallbackSpeechStream(remote, local)
    stream.close()
    assert stream.accept_pcm(b"pcm") is None
    assert local.frames == remote.frames == []


@pytest.mark.parametrize("local_final_frame", [0, 1])
def test_fallback_does_not_replay_cached_local_final_before_latest_cloud_commit(local_final_frame):
    old = RecognitionUpdate("大家好", is_final=True, segment_id=0)
    local_updates = [None, None, None, None, RecognitionUpdate("接下来", segment_id=1)]
    local_updates[local_final_frame] = old
    local = FakeStream(local_updates)
    remote = FakeStream(
        [
            RecognitionUpdate("大家好", is_final=True, segment_id=8),
            RecognitionUpdate("今天开始", is_final=True, segment_id=9),
            None,
            SpeechUnavailableError("offline"),
        ]
    )
    stream = FallbackSpeechStream(remote, local)
    assert stream.accept_pcm(b"one").segment_id == 8
    assert stream.accept_pcm(b"two").segment_id == 9
    assert stream.accept_pcm(b"three") is None
    assert stream.accept_pcm(b"four") is None
    assert stream.fallback_active
    assert remote.closed
    # No stale final was invented; remapping waits for new local speech.
    recovered = stream.accept_pcm(b"five")
    assert recovered == RecognitionUpdate("接下来", segment_id=10)


def test_fallback_can_reuse_local_update_newer_than_last_cloud_commit():
    local = FakeStream([None, RecognitionUpdate("新的句子", is_final=True, segment_id=1), None])
    remote = FakeStream(
        [
            RecognitionUpdate("之前的句子", is_final=True, segment_id=3),
            None,
            SpeechUnavailableError("offline"),
        ]
    )
    stream = FallbackSpeechStream(remote, local)
    assert stream.accept_pcm(b"one").segment_id == 3
    assert stream.accept_pcm(b"two") is None
    assert stream.accept_pcm(b"three") == RecognitionUpdate("新的句子", is_final=True, segment_id=4)


def test_fallback_does_not_reuse_old_partial_before_cloud_final():
    local = FakeStream([RecognitionUpdate("大家", segment_id=0), None, None])
    remote = FakeStream(
        [
            None,
            RecognitionUpdate("大家好", is_final=True, segment_id=7),
            SpeechUnavailableError("offline"),
        ]
    )
    stream = FallbackSpeechStream(remote, local)
    assert stream.accept_pcm(b"one") is None
    assert stream.accept_pcm(b"two").is_final
    assert stream.accept_pcm(b"three") is None


@pytest.mark.parametrize("wire_result", [False, True])
def test_speech_facade_preserves_audio_interval_through_normalization(wire_result):
    from app.infrastructure.speech_protocols import ProtocolTranscript

    result = (
        ProtocolTranscript(" 好好， ", False, 2, 1200, 1800)
        if wire_result
        else RecognitionUpdate(" 好好， ", False, 2, False, 1200, 1800)
    )
    update = SpeechStream(recognizer=lambda pcm: result).accept_pcm(b"audio")
    assert update.text == "好好，"
    assert update.segment_id == 2
    assert not update.is_final
    assert update.start_ms == 1200
    assert update.end_ms == 1800


def test_provider_fallback_keeps_cloud_interval_but_drops_local_relative_clock():
    local = FakeStream(
        [
            RecognitionUpdate("大家", False, 0, False, 0, 400),
            RecognitionUpdate("接下来", False, 1, False, 0, 600),
            RecognitionUpdate("继续朗读", True, 1, False, 0, 1000),
        ]
    )
    remote = FakeStream(
        [
            RecognitionUpdate("大家好", True, 7, False, 1200, 1800),
            SpeechUnavailableError("offline"),
        ]
    )
    stream = FallbackSpeechStream(remote, local)
    cloud_update = stream.accept_pcm(b"one")
    assert cloud_update == RecognitionUpdate("大家好", True, 7, False, 1200, 1800)
    local_update = stream.accept_pcm(b"two")
    assert local_update == RecognitionUpdate("接下来", segment_id=8)
    next_update = stream.accept_pcm(b"three")
    assert next_update == RecognitionUpdate("继续朗读", is_final=True, segment_id=8)
    assert stream.fallback_active
