"""Pending recognition and true alignment failures remain separate concerns."""

import asyncio

import pytest
from test_noise_guard_runtime import ac_pcm, frame, listening, silence

from app.domain.session import SpeechSession
from app.infrastructure.speech import RecognitionUpdate


def matched_session():
    session = SpeechSession("script", "大家好。欢迎测试。继续下一句。", silence_timeout_ms=1000)
    session.handle("start", 0)
    session.handle("transcript", 10, text="大家好")
    assert session.reliable
    return session


def test_multiple_voice_onsets_preserve_last_confirmed_reliability_for_filler():
    session = matched_session()
    position = session.reading_position
    for timestamp in (100, 400, 700):
        session.handle("speech_start", timestamp)
        session.handle("speech_end", timestamp + 100)
    session.handle("filler", 850)
    assert session.reliable
    assert session.reading_position == position
    assert session.feedback_id == 0


def test_real_unmatched_final_stays_protected_after_voice_and_filler():
    session = matched_session()
    position = session.reading_position
    session.handle("speech_start", 100)
    session.handle("transcript", 120, text="来")
    session.handle("speech_end", 200)
    for timestamp in (400, 700):
        session.handle("speech_start", timestamp)
        session.handle("speech_end", timestamp + 100)
        session.handle("filler", timestamp + 110)
    session.handle("tick", 5000)
    assert not session.reliable
    assert session.reading_position == position
    assert session.feedback_id == 1
    assert session.state.value == "LISTENING"
    assert session.snapshot()["alignment_status"] == "mismatch"


def test_alignment_status_distinguishes_pending_from_reliable_position():
    session = matched_session()
    assert session.snapshot()["alignment_status"] == "reliable"
    session.handle("speech_start", 100)
    assert session.snapshot()["alignment_status"] == "pending"
    session.handle("speech_end", 200)
    assert session.snapshot()["alignment_status"] == "pending"
    session.handle("transcript", 250, text="欢迎测试")
    assert session.snapshot()["alignment_status"] == "reliable"


@pytest.mark.parametrize("recovery", ["transcript", "reposition"])
def test_mismatch_status_latches_through_vad_until_explicit_recovery(recovery):
    session = matched_session()
    session.handle("transcript", 100, text="来")
    assert session.snapshot()["alignment_status"] == "mismatch"
    for timestamp in (200, 500):
        session.handle("speech_start", timestamp)
        assert session.snapshot()["alignment_status"] == "mismatch"
        session.handle("speech_end", timestamp + 100)
        assert session.snapshot()["alignment_status"] == "mismatch"
    payload = {"text": "大家好"} if recovery == "transcript" else {"position": 0}
    session.handle(recovery, 800, **payload)
    assert session.snapshot()["alignment_status"] == "reliable"
    assert session.reliable


def test_valid_pending_partial_survives_another_vad_burst_until_silence_deadline(monkeypatch):
    async def scenario():
        runtime, speech, clock = await listening(monkeypatch, "大家好。欢迎测试。")
        try:
            await frame(
                runtime,
                speech,
                clock,
                1000,
                ac_pcm(),
                RecognitionUpdate("大家好", segment_id=0),
                vad=True,
            )
            assert runtime.session.reading_position is None
            await frame(runtime, speech, clock, 1250, silence(250))
            await frame(runtime, speech, clock, 1500, ac_pcm(), vad=True)
            await frame(runtime, speech, clock, 1750, silence(250))
            await runtime.tick(2499)
            assert runtime.session.reading_position is None
            assert runtime.session.state.value == "LISTENING"
            await runtime.tick(2500)
            assert runtime.session.reading_position == 2
            assert runtime.session.aligned_transcript == "大家好"
            assert runtime.session.state.value == "PROMPTING"
            assert runtime.session.prompt.text.startswith("欢迎测试")
        finally:
            runtime.cancel_prompt()
            runtime.release_stream()

    asyncio.run(scenario())


def test_settled_unmatched_partial_is_not_committed_again_after_vad_burst(monkeypatch):
    async def scenario():
        runtime, speech, clock = await listening(monkeypatch, "大家好。欢迎测试。")
        commits = []
        original = runtime.commit_transcript

        def commit(text, timestamp):
            commits.append(text)
            original(text, timestamp)

        monkeypatch.setattr(runtime, "commit_transcript", commit)
        try:
            update = RecognitionUpdate("来", segment_id=0)
            await frame(runtime, speech, clock, 1000, ac_pcm(), update, vad=True)
            await frame(runtime, speech, clock, 1250, silence(250))
            await runtime.tick(2000)
            await frame(runtime, speech, clock, 2100, ac_pcm(), update, vad=True)
            await frame(runtime, speech, clock, 2350, silence(250))
            await runtime.tick(3100)
            assert commits == ["来"]
            assert runtime.session.feedback_id == 1
            assert not runtime.session.reliable
            assert runtime.session.state.value == "LISTENING"
        finally:
            runtime.cancel_prompt()
            runtime.release_stream()

    asyncio.run(scenario())


def test_finalized_old_segment_cannot_replay_after_new_voice_without_text(monkeypatch):
    async def scenario():
        runtime, speech, clock = await listening(monkeypatch, "大家好。欢迎测试。继续下一句。")
        try:
            first = RecognitionUpdate("大家好", is_final=True, segment_id=0)
            await frame(runtime, speech, clock, 1000, ac_pcm(), first, vad=True)
            await frame(runtime, speech, clock, 1250, silence(250))
            await frame(
                runtime,
                speech,
                clock,
                1300,
                ac_pcm(),
                RecognitionUpdate("欢迎测试", is_final=True, segment_id=1),
                vad=True,
            )
            position = runtime.session.reading_position
            assert runtime.session.aligned_transcript == "欢迎测试"
            await frame(runtime, speech, clock, 1550, silence(250))
            await frame(runtime, speech, clock, 1600, ac_pcm(), vad=True)
            await frame(runtime, speech, clock, 1850, silence(250), first)
            await runtime.tick(3000)
            assert runtime.session.reading_position == position
            assert runtime.session.aligned_transcript == "欢迎测试"
            assert runtime.session.feedback_id == 0
            assert runtime.session.state.value == "LISTENING"
        finally:
            runtime.cancel_prompt()
            runtime.release_stream()

    asyncio.run(scenario())


def test_new_vad_marks_previous_raw_result_historical_until_current_update(monkeypatch):
    async def scenario():
        runtime, speech, clock = await listening(monkeypatch, "大家好。欢迎测试。")
        try:
            await frame(
                runtime,
                speech,
                clock,
                1000,
                ac_pcm(),
                RecognitionUpdate("大家好", is_final=True, segment_id=0),
                vad=True,
            )
            assert runtime.snapshot()["raw_transcript_is_current"] is True
            await frame(runtime, speech, clock, 1250, silence(250))
            await frame(runtime, speech, clock, 1500, ac_pcm(), vad=True)
            snapshot = runtime.snapshot()
            assert snapshot["raw_transcript_is_current"] is False
            assert snapshot["alignment_status"] == "pending"
            await frame(
                runtime,
                speech,
                clock,
                1520,
                ac_pcm(),
                RecognitionUpdate("欢迎", segment_id=1),
                vad=True,
            )
            assert runtime.snapshot()["raw_transcript_is_current"] is True
            assert runtime.snapshot()["raw_transcript"] == "欢迎"
            assert runtime.session.reading_position == 2
        finally:
            runtime.cancel_prompt()
            runtime.release_stream()

    asyncio.run(scenario())


def test_pause_resume_discards_pending_text_and_old_generation_audio(monkeypatch):
    async def scenario():
        runtime, speech, clock = await listening(monkeypatch, "大家好。欢迎测试。")
        try:
            await frame(
                runtime,
                speech,
                clock,
                1000,
                ac_pcm(),
                RecognitionUpdate("大家好", segment_id=0),
                vad=True,
            )
            old_generation = runtime.session.generation
            await runtime.control({"type": "pause"})
            clock[0] = 2000
            await runtime.control({"type": "resume"})
            await runtime.control({"type": "audio_ready"})
            assert runtime.session.generation != old_generation
            await runtime.audio(ac_pcm(), generation=old_generation)
            assert runtime.audio_evidence.audio_ms == 0
            await frame(runtime, speech, clock, 2250, silence(250))
            await runtime.tick(4000)
            assert runtime.session.reading_position is None
            assert runtime.session.aligned_transcript == ""
            assert runtime.snapshot()["raw_transcript_is_current"] is False
        finally:
            runtime.cancel_prompt()
            runtime.release_stream()

    asyncio.run(scenario())


def test_voice_without_any_valid_transcript_does_not_guess_a_reliable_position(monkeypatch):
    async def scenario():
        runtime, speech, clock = await listening(monkeypatch, "大家好。欢迎测试。")
        try:
            await frame(runtime, speech, clock, 1000, ac_pcm(), vad=True)
            await frame(runtime, speech, clock, 1250, silence(250))
            await runtime.tick(5000)
            assert runtime.session.reading_position is None
            assert not runtime.session.reliable
            assert runtime.session.state.value == "LISTENING"
            assert runtime.snapshot()["alignment_status"] == "pending"
        finally:
            runtime.cancel_prompt()
            runtime.release_stream()

    asyncio.run(scenario())


@pytest.mark.parametrize("is_final", [False, True])
def test_same_segment_filler_revision_retracts_old_partial_without_advancing(monkeypatch, is_final):
    async def scenario():
        runtime, speech, clock = await listening(monkeypatch, "大家好。欢迎测试。")
        try:
            await frame(
                runtime,
                speech,
                clock,
                1000,
                ac_pcm(),
                RecognitionUpdate("大家好", segment_id=0),
                vad=True,
            )
            await frame(
                runtime,
                speech,
                clock,
                1250,
                silence(250),
                RecognitionUpdate("嗯", is_final=is_final, segment_id=0, is_filler_only=True),
            )
            await runtime.tick(2000)
            assert runtime.session.reading_position is None
            assert runtime.session.aligned_transcript == ""
            assert runtime.session.feedback_id == 0
            assert runtime.session.state.value == "PROMPTING"
            assert runtime.session.prompt.text.startswith("大家好")
        finally:
            runtime.cancel_prompt()
            runtime.release_stream()

    asyncio.run(scenario())


def test_same_segment_explicit_empty_final_retracts_old_partial(monkeypatch):
    async def scenario():
        runtime, speech, clock = await listening(monkeypatch, "大家好。欢迎测试。")
        try:
            await frame(
                runtime,
                speech,
                clock,
                1000,
                ac_pcm(),
                RecognitionUpdate("大家好", segment_id=0),
                vad=True,
            )
            # Unlike None (no new hypothesis), this is an explicit final revision.
            await frame(
                runtime,
                speech,
                clock,
                1250,
                silence(250),
                RecognitionUpdate("", is_final=True, segment_id=0),
            )
            await runtime.tick(2000)
            assert runtime.session.reading_position is None
            assert runtime.session.aligned_transcript == ""
            assert runtime.session.feedback_id == 0
        finally:
            runtime.cancel_prompt()
            runtime.release_stream()

    asyncio.run(scenario())


@pytest.mark.parametrize("text,script", [("来", "大家好。欢迎测试。"), ("大家好", "大家好。")])
def test_identical_final_after_settled_partial_does_not_recommit(monkeypatch, text, script):
    async def scenario():
        runtime, speech, clock = await listening(monkeypatch, script)
        commits = []
        original = runtime.commit_transcript

        def commit(value, timestamp):
            commits.append(value)
            original(value, timestamp)

        monkeypatch.setattr(runtime, "commit_transcript", commit)
        try:
            await frame(
                runtime,
                speech,
                clock,
                1000,
                ac_pcm(),
                RecognitionUpdate(text, segment_id=0),
                vad=True,
            )
            await frame(runtime, speech, clock, 1250, silence(250))
            await runtime.tick(2000)
            assert runtime.session.state.value == "LISTENING"
            assert commits == [text]
            position = runtime.session.reading_position
            await frame(
                runtime,
                speech,
                clock,
                2100,
                silence(100),
                RecognitionUpdate(text, is_final=True, segment_id=0),
            )
            await runtime.tick(3000)
            assert commits == [text]
            assert runtime.session.reading_position == position
            if text == "来":
                assert runtime.session.feedback_id == 1
                assert not runtime.session.reliable
            else:
                assert runtime.session.feedback_id == 0
                assert runtime.session.reliable
        finally:
            runtime.cancel_prompt()
            runtime.release_stream()

    asyncio.run(scenario())


def test_revised_final_in_same_segment_can_repair_settled_mismatch(monkeypatch):
    async def scenario():
        runtime, speech, clock = await listening(monkeypatch, "大家好。欢迎测试。")
        try:
            await frame(
                runtime,
                speech,
                clock,
                1000,
                ac_pcm(),
                RecognitionUpdate("来", segment_id=0),
                vad=True,
            )
            await frame(runtime, speech, clock, 1250, silence(250))
            await runtime.tick(2000)
            assert not runtime.session.reliable
            assert runtime.session.feedback_id == 1
            await frame(
                runtime,
                speech,
                clock,
                2100,
                ac_pcm(),
                RecognitionUpdate("大家好", is_final=True, segment_id=0),
                vad=True,
            )
            assert runtime.session.reliable
            assert runtime.session.reading_position == 2
            assert runtime.session.feedback_id == 1
            await frame(runtime, speech, clock, 2350, silence(250))
            await runtime.tick(3100)
            assert runtime.session.state.value == "PROMPTING"
            assert runtime.session.prompt.text.startswith("欢迎测试")
        finally:
            runtime.cancel_prompt()
            runtime.release_stream()

    asyncio.run(scenario())


def test_empty_revision_marks_fallback_to_last_committed_text_as_historical(monkeypatch):
    async def scenario():
        runtime, speech, clock = await listening(monkeypatch, "大家好。欢迎测试。")
        try:
            await frame(
                runtime,
                speech,
                clock,
                1000,
                ac_pcm(),
                RecognitionUpdate("大家好", is_final=True, segment_id=0),
                vad=True,
            )
            await frame(runtime, speech, clock, 1250, silence(250))
            await frame(
                runtime,
                speech,
                clock,
                1500,
                ac_pcm(),
                RecognitionUpdate("欢迎", segment_id=1),
                vad=True,
            )
            await frame(
                runtime,
                speech,
                clock,
                1750,
                silence(250),
                RecognitionUpdate("", is_final=True, segment_id=1),
            )
            snapshot = runtime.snapshot()
            assert snapshot["raw_transcript"] == "大家好"
            assert snapshot["raw_transcript_is_current"] is False
            assert snapshot["alignment_status"] == "pending"
            await runtime.tick(5000)
            assert runtime.session.reading_position == 2
            assert runtime.session.state.value == "LISTENING"
        finally:
            runtime.cancel_prompt()
            runtime.release_stream()

    asyncio.run(scenario())
