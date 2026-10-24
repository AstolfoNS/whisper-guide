"""A warmed local transcript keeps its acoustic interval across cloud timeouts."""

import asyncio
from array import array
from types import SimpleNamespace

import app.api.session as runtime_api
from app.api.session import SessionRuntime
from app.domain.session import SpeechSession
from app.infrastructure.speech import (
    FallbackSpeechStream,
    SpeechStream,
    SpeechUnavailableError,
    _SherpaRecognizerStream,
)


class NativeStream:
    def __init__(self):
        self.samples = 0

    def accept_waveform(self, sample_rate, waveform):
        assert sample_rate == 16000
        self.samples += len(waveform)

    @property
    def milliseconds(self):
        return self.samples // 16


class NativeRecognizer:
    """One true sentence, an empty endpoint, then one silent hallucination."""

    def __init__(self):
        self.resets = []

    def create_stream(self):
        self.stream = NativeStream()
        return self.stream

    def is_ready(self, stream):
        return False

    def get_result(self, stream):
        if 200 <= stream.milliseconds <= 1600:
            return "大家好"
        if stream.milliseconds == 5200:
            return "点"
        return ""

    def is_endpoint(self, stream):
        return stream.milliseconds in {1600, 4000, 5200}

    def reset(self, stream):
        self.resets.append(stream.milliseconds)


class QuietRemote:
    available = True
    effective_provider = "cloud"

    def __init__(self):
        self.samples = 0
        self.closed = False

    def accept_pcm(self, pcm):
        self.samples += len(pcm) // 2
        if self.samples >= 80000:
            raise SpeechUnavailableError("controlled cloud timeout")
        return None

    def close(self):
        self.closed = True


class FrameVad:
    def is_speech(self, pcm, sample_rate):
        return any(pcm)


def frame(voiced=False):
    return array("h", [-100, 100] * 160).tobytes() if voiced else bytes(640)


def native_stream():
    native = NativeRecognizer()
    adapter = _SherpaRecognizerStream(native, 16000)
    return native, adapter, SpeechStream(recognizer=adapter, vad=FrameVad())


def test_native_endpoint_and_explicit_reset_preserve_connection_pcm_clock():
    native, adapter, stream = native_stream()
    assert stream.uses_connection_timestamps
    updates = [stream.accept_pcm(frame(True)) for _ in range(80)]
    assert updates[-1].is_final
    assert (updates[-1].start_ms, updates[-1].end_ms) == (0, 1600)
    empty_endpoint = None
    for _ in range(120):
        empty_endpoint = stream.accept_pcm(frame())
    assert empty_endpoint.is_final
    assert not empty_endpoint.text
    assert (empty_endpoint.start_ms, empty_endpoint.end_ms) == (1600, 4000)
    for _ in range(10):
        stream.accept_pcm(frame())
    adapter.reset()
    final = None
    for _ in range(50):
        final = stream.accept_pcm(frame())
    assert final.is_final and final.text == "点"
    assert (final.start_ms, final.end_ms) == (4200, 5200)
    assert native.resets == [1600, 4000, 4200, 5200]
    _, _, fresh_stream = native_stream()
    first = None
    for _ in range(10):
        first = fresh_stream.accept_pcm(frame(True))
    assert (first.start_ms, first.end_ms) == (0, 200)


def test_delayed_cloud_fallback_uses_old_valid_local_interval_but_filters_new_silent_final(
    monkeypatch,
):
    async def scenario():
        clock = [1000]
        monkeypatch.setattr(runtime_api, "now_ms", lambda: clock[0])
        _, _, local = native_stream()
        remote = QuietRemote()
        fallback = FallbackSpeechStream(remote, local)

        class Services:
            settings = SimpleNamespace(llm_align_mode="off")

            def status(self):
                return {"stt": "ready", "vad": "ready", "tts": "ready"}

            def create_stream(self):
                return fallback

            async def synthesize(self, text):
                return b"test-audio-in-memory"

        runtime = SessionRuntime(
            SpeechSession("script", "大家好。继续下一句。", silence_timeout_ms=1000),
            "local evidence clock",
            Services(),
            "cloud",
        )
        await runtime.control({"type": "start"})
        await runtime.control({"type": "audio_ready"})
        for index in range(250):
            clock[0] = 1000 + (index + 1) * 20
            await runtime.audio(frame(index < 20))
        assert remote.closed and fallback.fallback_active
        assert runtime.session.reading_position == 2
        assert runtime.session.aligned_transcript == "大家好"
        assert runtime.session.reliable
        assert not runtime.snapshot()["recognition_filtered"]
        for index in range(10):
            clock[0] = 6020 + index * 20
            await runtime.audio(frame())
        assert runtime.snapshot()["raw_transcript"] == "点"
        assert runtime.snapshot()["recognition_filtered"]
        assert runtime.session.reading_position == 2
        assert runtime.session.reliable
        await runtime.tick(6999)
        assert runtime.session.state.value == "LISTENING"
        await runtime.tick(7000)
        assert runtime.session.state.value == "PROMPTING"
        assert runtime.session.prompt.text == "好。"
        runtime.cancel_prompt()
        runtime.release_stream()

    asyncio.run(scenario())
