"""Handshake readiness precedes PCM, and cancellation cannot reopen an old capture."""

import asyncio
from types import SimpleNamespace

import app.api.session as runtime_api
from app.api.session import SessionRuntime
from app.domain.session import SpeechSession
from app.infrastructure.speech import SpeechUnavailableError


class ConnectingStream:
    available = True
    effective_provider = "cloud"

    def __init__(self, *, failure=False, ignore_cancel=False):
        self.ready = False
        self.closed = False
        self.failure = failure
        self.ignore_cancel = ignore_cancel
        self.gate = asyncio.Event()
        self.frames = []

    async def wait_ready(self):
        try:
            await self.gate.wait()
        except asyncio.CancelledError:
            if not self.ignore_cancel:
                raise
        if self.failure:
            raise SpeechUnavailableError("controlled unavailable stream")
        self.ready = True
        return True

    def is_speech(self, pcm):
        return False

    def accept_pcm(self, pcm):
        self.frames.append(pcm)

    def close(self):
        self.closed = True
        self.ready = False


class Speech:
    settings = SimpleNamespace(llm_align_mode="off", cloud_stt_timeout_ms=2000)

    def __init__(self, stream):
        self.stream = stream

    def status(self):
        return {"stt": "ready", "tts": "ready", "vad": "ready"}

    def create_stream(self):
        return self.stream

    async def synthesize(self, text):
        return b"memory-audio"


def runtime(stream):
    return SessionRuntime(
        SpeechSession("s", "大家好。谢谢大家。"), "startup", Speech(stream), "cloud"
    )


def test_delayed_connection_cannot_accumulate_microphone_audio_or_spend_silence_budget(monkeypatch):
    async def scenario():
        clock = [100]
        monkeypatch.setattr(runtime_api, "now_ms", lambda: clock[0])
        stream = ConnectingStream()
        session = runtime(stream)
        await session.control({"type": "start"})
        try:
            task = session.readiness
            assert task is not None
            assert not session.snapshot()["stt_ready"]
            await session.control({"type": "audio_ready"})  # older client's early request
            for _ in range(60):
                await session.audio(bytes(640))
            assert not stream.frames
            assert not session.audio_ready
            clock[0] = 1800
            stream.gate.set()
            await task
            assert session.stt_ready
            assert session.audio_ready
            assert session.session.silence_since_ms == 1800
            await session.tick(2000)
            assert session.session.state.value == "LISTENING"
            await session.audio(bytes(640))
            assert len(stream.frames) == 1
        finally:
            session.release_stream()

    asyncio.run(scenario())


def test_ready_snapshot_still_requires_browser_microphone_acknowledgement():
    async def scenario():
        stream = ConnectingStream()
        session = runtime(stream)
        await session.control({"type": "start"})
        try:
            task = session.readiness
            assert task is not None
            stream.gate.set()
            await task
            assert session.snapshot()["stt_ready"]
            assert not session.audio_ready
            await session.audio(bytes(640))
            assert not stream.frames
            await session.control({"type": "audio_ready"})
            await session.audio(bytes(640))
            assert len(stream.frames) == 1
        finally:
            session.release_stream()

    asyncio.run(scenario())


def test_pause_cancels_connecting_stream_without_waiting_and_ignores_late_readiness():
    async def scenario():
        stream = ConnectingStream(ignore_cancel=True)
        session = runtime(stream)
        await session.control({"type": "start"})
        task = session.readiness
        assert task is not None
        await asyncio.sleep(0)
        await asyncio.wait_for(session.control({"type": "pause"}), timeout=0.1)
        await task
        assert session.session.state.value == "PAUSED"
        assert not session.stt_ready
        assert not session.audio_ready
        assert session.stream is None
        assert stream.closed
        assert not stream.frames

    asyncio.run(scenario())


def test_connection_failure_without_fallback_pauses_and_releases_resources():
    async def scenario():
        stream = ConnectingStream(failure=True)
        session = runtime(stream)
        await session.control({"type": "start"})
        task = session.readiness
        assert task is not None
        stream.gate.set()
        await task
        assert session.session.state.value == "PAUSED"
        assert not session.stt_ready
        assert session.stream is None
        assert stream.closed

    asyncio.run(scenario())


def test_explicit_close_invalidates_ready_task_in_the_same_generation():
    async def scenario():
        stream = ConnectingStream(ignore_cancel=True)
        session = runtime(stream)
        await session.control({"type": "start"})
        task = session.readiness
        assert task is not None
        await asyncio.sleep(0)
        generation = session.session.generation
        session.release_stream()
        await task
        assert session.session.generation == generation
        assert not session.stt_ready
        assert not session.audio_ready
        assert session.stream is None

    asyncio.run(scenario())
