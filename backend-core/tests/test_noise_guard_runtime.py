"""Acoustic evidence rejects unsupported ASR without weakening AC-14 safety."""

import asyncio
from array import array
from collections import deque
from types import SimpleNamespace

import pytest

import app.api.session as runtime_api
from app.api.session import SessionRuntime
from app.domain.session import SpeechSession
from app.infrastructure.speech import RecognitionUpdate


def ac_pcm(duration_ms=80, amplitude=100):
    return array("h", [-amplitude, amplitude] * (duration_ms * 8)).tobytes()


def silence(duration_ms=20):
    return bytes(duration_ms * 32)


class ControlledStream:
    available = True
    effective_provider = "cloud"

    def __init__(self):
        self.updates = deque()
        self.vad = False

    def is_speech(self, pcm):
        return self.vad

    def accept_pcm(self, pcm):
        return self.updates.popleft() if self.updates else None

    def close(self):
        pass


class ControlledSpeech:
    settings = SimpleNamespace(llm_align_mode="off")

    def __init__(self):
        self.stream = ControlledStream()

    def status(self):
        return {"stt": "ready", "vad": "ready", "tts": "ready"}

    def create_stream(self):
        return self.stream

    async def synthesize(self, text):
        return b"bounded-in-memory-audio"


async def listening(monkeypatch, script="我会坚持自律，好好吃饭，踏实学习。"):
    clock = [1000]
    monkeypatch.setattr(runtime_api, "now_ms", lambda: clock[0])
    speech = ControlledSpeech()
    runtime = SessionRuntime(
        SpeechSession("script", script, silence_timeout_ms=1000),
        "noise regression",
        speech,
        "cloud",
    )
    await runtime.control({"type": "start"})
    await runtime.control({"type": "audio_ready"})
    return runtime, speech, clock


async def frame(runtime, speech, clock, timestamp, pcm, update=None, *, vad=False):
    clock[0] = timestamp
    speech.stream.vad = vad
    speech.stream.updates.append(update)
    await runtime.audio(pcm)


def test_screenshot_stray_final_does_not_disable_prompt_after_correct_phrase(monkeypatch):
    async def scenario():
        runtime, speech, clock = await listening(monkeypatch)
        await frame(
            runtime,
            speech,
            clock,
            1000,
            ac_pcm(),
            RecognitionUpdate(
                "我会坚持自律，好好", is_final=True, segment_id=0, start_ms=0, end_ms=80
            ),
            vad=True,
        )
        position = runtime.session.reading_position
        assert runtime.session.aligned_transcript == "我会坚持自律，好好"
        await frame(
            runtime,
            speech,
            clock,
            1250,
            silence(250),
            RecognitionUpdate("点", is_final=True, segment_id=1, start_ms=80, end_ms=330),
        )
        snapshot = runtime.snapshot()
        assert snapshot["raw_transcript"] == "点"
        assert snapshot["raw_transcript_is_final"] is True
        assert snapshot["recognition_filtered"] is True
        assert snapshot["recognition_filtered_count"] == 1
        assert snapshot["recognition_filter_reason"]
        assert snapshot["aligned_transcript"] == "我会坚持自律，好好"
        assert runtime.last_text == "我会坚持自律，好好"
        assert runtime.session.reliable
        assert runtime.session.feedback_id == 0
        assert runtime.session.reading_position == position
        await runtime.tick(1999)
        assert runtime.session.state.value == "LISTENING"
        await runtime.tick(2000)
        assert runtime.session.state.value == "PROMPTING"
        assert "吃饭" in runtime.session.prompt.text
        runtime.cancel_prompt()
        runtime.release_stream()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "raw", [silence(80), array("h", [1000] * 1280).tobytes(), ac_pcm(80, amplitude=1)]
)
def test_false_vad_on_silence_dc_or_weak_noise_cannot_stop_clock(monkeypatch, raw):
    async def scenario():
        runtime, speech, clock = await listening(monkeypatch)
        await frame(
            runtime, speech, clock, 1100, raw, RecognitionUpdate("点", is_final=True), vad=True
        )
        assert not runtime.session.speaking
        assert runtime.session.reliable
        assert runtime.session.silence_since_ms == 1000
        assert runtime.session.reading_position is None
        assert runtime.snapshot()["recognition_filtered"]
        await runtime.tick(2000)
        assert runtime.session.state.value == "PROMPTING"
        runtime.cancel_prompt()
        runtime.release_stream()

    asyncio.run(scenario())


@pytest.mark.parametrize("text", ["点", "火星飞船已经发射"])
def test_audible_unmatched_single_word_and_off_script_speech_still_trigger_ac14(monkeypatch, text):
    async def scenario():
        runtime, speech, clock = await listening(monkeypatch)
        runtime.session.handle("transcript", 0, text="我会坚持自律，好好")
        position = runtime.session.reading_position
        await frame(
            runtime, speech, clock, 1000, ac_pcm(), RecognitionUpdate(text, is_final=True), vad=True
        )
        await frame(runtime, speech, clock, 1250, silence(250))
        await runtime.tick(2000)
        assert runtime.session.state.value == "LISTENING"
        assert runtime.session.reading_position == position
        assert not runtime.session.reliable
        assert runtime.session.feedback_id == 1
        assert not runtime.snapshot()["recognition_filtered"]
        runtime.release_stream()

    asyncio.run(scenario())


def test_real_single_word_is_not_filtered_by_text_length(monkeypatch):
    async def scenario():
        runtime, speech, clock = await listening(monkeypatch, "点。继续下一句。")
        await frame(
            runtime, speech, clock, 1000, ac_pcm(), RecognitionUpdate("点", is_final=True), vad=True
        )
        assert runtime.session.reading_position == 0
        assert runtime.session.aligned_transcript == "点"
        assert runtime.session.reliable
        assert not runtime.snapshot()["recognition_filtered"]
        runtime.release_stream()

    asyncio.run(scenario())


def test_vad_missed_weak_partial_still_responds_then_finalizes_at_deadline(monkeypatch):
    async def scenario():
        runtime, speech, clock = await listening(monkeypatch)
        await frame(
            runtime,
            speech,
            clock,
            1000,
            ac_pcm(),
            RecognitionUpdate("我会坚持自律，好好", segment_id=0),
        )
        assert runtime.session.speaking
        assert runtime.session.reading_position is None
        assert runtime.last_voice_ms == 1000
        await frame(runtime, speech, clock, 1250, silence(250))
        assert not runtime.session.speaking
        assert runtime.session.silence_since_ms == 1000
        await runtime.tick(2000)
        assert runtime.session.aligned_transcript == "我会坚持自律，好好"
        assert runtime.session.state.value == "PROMPTING"
        runtime.cancel_prompt()
        runtime.release_stream()

    asyncio.run(scenario())


def test_timed_delayed_finals_keep_evidence_for_following_utterance(monkeypatch):
    async def scenario():
        runtime, speech, clock = await listening(monkeypatch, "大家好。欢迎测试。")
        await frame(runtime, speech, clock, 1000, ac_pcm())
        await frame(runtime, speech, clock, 1250, silence(200))
        # The second utterance is already on the wire before the first final.
        await frame(
            runtime,
            speech,
            clock,
            1300,
            ac_pcm(),
            RecognitionUpdate("大家好", is_final=True, segment_id=0, start_ms=0, end_ms=80),
        )
        assert runtime.session.reading_position == 2
        await frame(
            runtime,
            speech,
            clock,
            1350,
            silence(),
            RecognitionUpdate("欢迎测试", is_final=True, segment_id=1, start_ms=280, end_ms=360),
        )
        assert runtime.session.reading_position == 7
        assert runtime.session.reliable
        assert not runtime.snapshot()["recognition_filtered"]
        runtime.release_stream()

    asyncio.run(scenario())


def test_same_segment_late_final_uses_latched_weak_voice_evidence(monkeypatch):
    async def scenario():
        runtime, speech, clock = await listening(monkeypatch, "大家好。欢迎测试。")
        await frame(runtime, speech, clock, 1000, ac_pcm(), RecognitionUpdate("大家", segment_id=0))
        await frame(
            runtime,
            speech,
            clock,
            1800,
            silence(800),
            RecognitionUpdate("大家好", is_final=True, segment_id=0),
        )
        assert runtime.session.reading_position == 2
        assert runtime.session.reliable
        assert not runtime.snapshot()["recognition_filtered"]
        runtime.release_stream()

    asyncio.run(scenario())


def test_unsupported_update_does_not_cancel_refinement_or_advance_revision(monkeypatch):
    async def scenario():
        runtime, speech, clock = await listening(monkeypatch)
        pending = asyncio.create_task(asyncio.Event().wait())
        runtime.refinement = pending
        revision = runtime.transcript_revision
        await frame(
            runtime,
            speech,
            clock,
            1100,
            silence(200),
            RecognitionUpdate("点", is_final=True, segment_id=1),
        )
        assert runtime.refinement is pending
        assert not pending.cancelled()
        assert runtime.transcript_revision == revision
        assert runtime.session.raw_transcript == ""
        assert runtime.session.reliable
        runtime.release_stream()
        await asyncio.gather(pending, return_exceptions=True)

    asyncio.run(scenario())


def test_pause_resume_clears_acoustic_evidence_and_filtered_diagnostics(monkeypatch):
    async def scenario():
        runtime, speech, clock = await listening(monkeypatch)
        await frame(
            runtime, speech, clock, 1000, ac_pcm(), RecognitionUpdate("我会坚持自律", segment_id=0)
        )
        old_evidence = runtime.audio_evidence
        await runtime.control({"type": "pause"})
        await runtime.control({"type": "resume"})
        await runtime.control({"type": "audio_ready"})
        assert runtime.audio_evidence is not old_evidence
        await frame(
            runtime,
            speech,
            clock,
            1250,
            silence(250),
            RecognitionUpdate("我会坚持自律", is_final=True, segment_id=0),
        )
        assert runtime.session.reading_position is None
        assert runtime.last_text == ""
        assert runtime.snapshot()["recognition_filtered"]
        await runtime.control({"type": "pause"})
        assert not runtime.snapshot()["recognition_filtered"]
        assert runtime.filtered_count == 0
        assert runtime.audio_evidence.audio_ms == 0

    asyncio.run(scenario())


def test_vad_onset_pending_confirmation_precedes_due_prompt(monkeypatch):
    async def scenario():
        runtime, speech, clock = await listening(monkeypatch)
        await frame(runtime, speech, clock, 1999, ac_pcm(20), vad=True)
        assert runtime.audio_evidence.pending_voice
        assert runtime.session.reliable
        await runtime.tick(2000)
        assert runtime.session.state.value == "LISTENING"
        await frame(runtime, speech, clock, 2019, ac_pcm(20), vad=True)
        await runtime.tick(2019)
        assert runtime.session.state.value == "LISTENING"
        await frame(
            runtime,
            speech,
            clock,
            2039,
            ac_pcm(20),
            RecognitionUpdate("我会坚持自律，好好", is_final=True),
            vad=True,
        )
        assert runtime.session.speaking
        assert runtime.session.reliable
        assert not runtime.audio_evidence.pending_voice
        await frame(runtime, speech, clock, 2289, silence(250))
        await runtime.tick(3038)
        assert runtime.session.state.value == "LISTENING"
        await runtime.tick(3039)
        assert runtime.session.state.value == "PROMPTING"
        runtime.cancel_prompt()
        runtime.release_stream()

    asyncio.run(scenario())


def test_unconfirmed_click_does_not_restart_expired_silence_clock(monkeypatch):
    async def scenario():
        runtime, speech, clock = await listening(monkeypatch)
        await frame(runtime, speech, clock, 1999, ac_pcm(20), vad=True)
        await runtime.tick(2000)
        assert runtime.session.state.value == "LISTENING"
        await frame(runtime, speech, clock, 2019, silence())
        assert runtime.session.silence_since_ms == 1000
        assert runtime.session.reliable
        await runtime.tick(2019)
        assert runtime.session.state.value == "PROMPTING"
        runtime.cancel_prompt()
        runtime.release_stream()

    asyncio.run(scenario())


def test_empty_endpoint_preserves_filter_notice_until_supported_text_arrives(monkeypatch):
    async def scenario():
        runtime, speech, clock = await listening(monkeypatch)
        await frame(
            runtime,
            speech,
            clock,
            1100,
            silence(100),
            RecognitionUpdate("点", is_final=True, segment_id=0),
        )
        await frame(
            runtime,
            speech,
            clock,
            1200,
            silence(100),
            RecognitionUpdate("", is_final=True, segment_id=1),
        )
        assert runtime.snapshot()["recognition_filtered"]
        assert runtime.snapshot()["raw_transcript"] == "点"
        await frame(
            runtime,
            speech,
            clock,
            1300,
            ac_pcm(),
            RecognitionUpdate("我会坚持自律，好好", is_final=True, segment_id=2),
        )
        assert runtime.session.reliable
        assert runtime.session.reading_position is not None
        assert not runtime.snapshot()["recognition_filtered"]
        assert runtime.snapshot()["raw_transcript"] == "我会坚持自律，好好"
        runtime.release_stream()

    asyncio.run(scenario())
