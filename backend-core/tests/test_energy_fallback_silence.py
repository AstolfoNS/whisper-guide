"""Energy-only VAD fallback must not hold the live silence clock indefinitely."""

import asyncio
from array import array
from types import SimpleNamespace

import pytest

import app.api.session as runtime_api
from app.api.session import SessionRuntime
from app.domain.session import SpeechSession
from app.infrastructure.speech import RecognitionUpdate, SpeechStream


def pcm(amplitude=1000):
    return array("h", [-amplitude, amplitude] * 160).tobytes()


class BrokenVad:
    def is_speech(self, pcm, sample_rate):
        raise RuntimeError("controlled unavailable voice detector")


class Speech:
    settings = SimpleNamespace(llm_align_mode="off")

    def __init__(self, stream):
        self.stream = stream
        self.spoken = []

    def status(self, **kwargs):
        return {"stt": "ready", "tts": "ready"}

    def create_stream(self, **kwargs):
        return self.stream

    async def synthesize(self, text):
        self.spoken.append(text)
        return b"bounded-in-memory-prompt"


@pytest.mark.parametrize("vad", [None, BrokenVad()], ids=["vad-missing", "vad-failed"])
def test_stationary_rms_fallback_does_not_reset_the_automatic_prompt_deadline(monkeypatch, vad):
    async def scenario():
        clock = [1000]
        monkeypatch.setattr(runtime_api, "now_ms", lambda: clock[0])
        stream = SpeechStream(recognizer=lambda raw: None, vad=vad)
        speech = Speech(stream)
        runtime = SessionRuntime(SpeechSession("s", "下一句提示。", 1000), "rms", speech)
        await runtime.control({"type": "start"})
        await runtime.control({"type": "audio_ready"})
        try:
            for index in range(100):
                clock[0] = 1000 + index * 20
                await runtime.audio(pcm())
                await runtime.tick(clock[0])
                if runtime.session.state.value == "PROMPTING":
                    break
            # Protect an unclassified onset; learned steady noise cannot keep
            # restarting the silence clock forever.
            assert stream.vad_is_fallback
            assert clock[0] < 3000
            assert runtime.session.state.value == "PROMPTING"
            assert runtime.session.prompt.text == "下一句提示。"
            if runtime.synthesis:
                await runtime.synthesis
            assert speech.spoken == ["下一句提示。"]
        finally:
            runtime.cancel_prompt()
            runtime.release_stream()

    asyncio.run(scenario())


def test_supported_fallback_voice_text_can_still_locate_then_prompt_after_silence(monkeypatch):
    async def scenario():
        clock = [1000]
        monkeypatch.setattr(runtime_api, "now_ms", lambda: clock[0])
        received = [0]

        def recognizer(raw):
            received[0] += 1
            if received[0] == 4:
                return RecognitionUpdate(
                    "大家好", is_final=True, segment_id=0, start_ms=0, end_ms=80
                )
            return None

        stream = SpeechStream(recognizer=recognizer)
        runtime = SessionRuntime(
            SpeechSession("s", "大家好。下一句提示。", 1000), "voice", Speech(stream)
        )
        await runtime.control({"type": "start"})
        await runtime.control({"type": "audio_ready"})
        try:
            for index in range(4):
                clock[0] = 1000 + index * 20
                await runtime.audio(pcm(500 + index * 200))
            assert runtime.session.reading_position == 2
            assert runtime.session.reliable
            assert runtime.session.aligned_transcript == "大家好"
            for index in range(4, 55):
                clock[0] = 1000 + index * 20
                await runtime.audio(bytes(640))
                await runtime.tick(clock[0])
                if runtime.session.state.value == "PROMPTING":
                    break
            assert runtime.session.state.value == "PROMPTING"
            assert runtime.session.prompt.text == "下一句提示。"
        finally:
            runtime.cancel_prompt()
            runtime.release_stream()

    asyncio.run(scenario())
