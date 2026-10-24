import asyncio
import time
from array import array

from app.api.session import SessionRuntime
from app.domain.session import SpeechSession
from app.speech import RecognitionUpdate

VOICE_PCM = array("h", [-100, 100] * 640).tobytes()  # 80 ms of AC audio


class SlowStream:
    available = True

    def is_speech(self, pcm):
        return True

    def accept_pcm(self, pcm):
        time.sleep(0.15)
        return RecognitionUpdate("欢迎大家")

    def close(self):
        pass


class FakeSpeech:
    def status(self):
        return {"stt": "ready", "tts": "ready"}

    def create_stream(self):
        return SlowStream()

    async def synthesize(self, text):
        return b"audio"


def test_pause_does_not_wait_for_inference_and_discards_late_transcript():
    async def scenario():
        runtime = SessionRuntime(SpeechSession("script", "欢迎大家。"), "demo", FakeSpeech())
        await runtime.control({"type": "start"})
        await runtime.control({"type": "audio_ready"})
        inference = asyncio.create_task(runtime.audio(VOICE_PCM))
        await asyncio.sleep(0.02)
        before = time.monotonic()
        await runtime.control({"type": "pause"})
        assert time.monotonic() - before < 0.1
        await inference
        assert runtime.session.state.value == "PAUSED"
        assert runtime.session.reading_position is None

    asyncio.run(scenario())


def test_duplicate_audio_ready_does_not_reset_silence_clock():
    async def scenario():
        runtime = SessionRuntime(SpeechSession("script", "欢迎大家。"), "demo", FakeSpeech())
        await runtime.control({"type": "start"})
        await runtime.control({"type": "audio_ready"})
        first = runtime.session.silence_since_ms
        await asyncio.sleep(0.01)
        await runtime.control({"type": "audio_ready"})
        assert runtime.session.silence_since_ms == first
        runtime.release_stream()

    asyncio.run(scenario())


class PartialFinalStream:
    available = True

    def __init__(self):
        self.updates = iter(
            [
                RecognitionUpdate("欢迎大家", is_final=False, segment_id=0),
                RecognitionUpdate("欢迎大家。", is_final=True, segment_id=0),
            ]
        )

    def is_speech(self, pcm):
        return True

    def accept_pcm(self, pcm):
        return next(self.updates)

    def close(self):
        pass


def test_partial_does_not_commit_until_final():
    async def scenario():
        class Speech(FakeSpeech):
            def create_stream(self):
                return PartialFinalStream()

        runtime = SessionRuntime(SpeechSession("script", "欢迎大家。"), "demo", Speech())
        await runtime.control({"type": "start"})
        await runtime.control({"type": "audio_ready"})
        await runtime.audio(VOICE_PCM)
        assert runtime.session.reading_position is None
        await runtime.audio(VOICE_PCM)
        assert runtime.session.reading_position is not None

    asyncio.run(scenario())
