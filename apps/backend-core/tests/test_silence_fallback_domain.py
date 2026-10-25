"""Silence deadlines help from saved anchors without inventing recognized progress."""

import pytest

from app.domain.session import SpeechSession

SCRIPT = "大家好。欢迎参加本次测试。最后谢谢大家。"


def start(timeout=3000):
    session = SpeechSession("script", SCRIPT, silence_timeout_ms=timeout)
    session.handle("start", 0)
    return session


def test_snapshot_identifies_saved_position_timeout_policy():
    session = start()
    assert session.snapshot()["silence_prompt_policy"] == "saved_position"
    session.handle("speech_start", 100)
    session.handle("speech_end", 200)
    assert session.handle("tick", 3200)["silence_prompt_policy"] == "saved_position"


def alignment(session):
    return {
        "reading": session.reading_position,
        "prompt": session.prompt_position,
        "raw": session.raw_transcript,
        "last": session.last_transcript,
        "aligned": session.aligned_transcript,
        "confidence": session.match_confidence,
        "reliable": session.reliable,
        "last_reliable": session._last_reliable,
        "status": session.snapshot()["alignment_status"],
        "attempts": session.recovery_attempts,
    }


@pytest.mark.parametrize("timeout", [1000, 3000, 6000])
def test_no_transcript_after_voice_prompts_only_at_deadline_from_saved_start(timeout):
    session = start(timeout)
    session.handle("speech_start", 100)
    session.handle("speech_end", 200)
    before = alignment(session)
    generation = session.generation
    assert session.handle("tick", 200 + timeout - 1)["state"] == "LISTENING"
    prompted = session.handle("tick", 200 + timeout)
    assert prompted["state"] == "PROMPTING"
    assert prompted["transition_trigger"] == "recognition_wait_timeout"
    assert prompted["prompt"]["text"] == "大家好。"
    assert session.generation == generation + 1
    assert alignment(session) == before
    assert before["status"] == "pending"
    assert not before["reliable"]
    assert before["reading"] is None
    assert "尚未确认" in prompted["message"]
    assert "已保存的提示起点" in prompted["message"]
    feedback = session.feedback_id
    assert session.handle("tick", 99999) == prompted
    assert session.feedback_id == feedback


def test_pending_voice_after_correct_text_uses_next_saved_fragment_without_claiming_new_reading():
    session = start()
    session.handle("transcript", 50, text="大家好", evidence_id=0)
    session.handle("speech_start", 100)
    session.handle("speech_end", 200)
    before = alignment(session)
    snapshot = session.handle("tick", 3200)
    assert snapshot["state"] == "PROMPTING"
    assert snapshot["prompt"]["text"] == "欢迎参加本次测试。"
    assert snapshot["prompt"]["start"] == SCRIPT.index("欢")
    assert alignment(session) == before
    assert before["reading"] == SCRIPT.index("好")
    assert before["aligned"] == "大家好"
    assert before["status"] == "pending"


def test_genuine_mismatch_prompts_saved_anchor_with_warning_and_keeps_mismatch_latched():
    session = start()
    session.handle("transcript", 50, text="大家好", evidence_id=0)
    session.handle("speech_start", 100)
    session.handle("transcript", 150, text="完全无关的背景识别内容", evidence_id=1)
    session.handle("speech_end", 200)
    before = alignment(session)
    snapshot = session.handle("tick", 3200)
    assert snapshot["state"] == "PROMPTING"
    assert snapshot["prompt"]["text"] == "欢迎参加本次测试。"
    assert snapshot["transition_trigger"] == "recognition_wait_timeout"
    assert alignment(session) == before
    assert before["status"] == "mismatch"
    assert not before["reliable"] and not before["last_reliable"]
    assert session._location_warning_reported


def test_withdrawn_alignment_is_not_revalidated_by_saved_anchor_prompt():
    session = start()
    session.handle("transcript", 50, text="大家好", evidence_id="revoked")
    session.retract_evidence("revoked")
    before = alignment(session)
    snapshot = session.handle("tick", 3000)
    assert snapshot["state"] == "PROMPTING"
    assert snapshot["prompt"]["start"] == before["prompt"]
    assert alignment(session) == before
    assert before["confidence"] is None
    assert before["status"] == "mismatch"


def test_active_voice_never_times_out_to_a_saved_anchor():
    session = start()
    session.handle("speech_start", 2999)
    for now in (3000, 10000, 100000):
        snapshot = session.handle("tick", now)
        assert snapshot["state"] == "LISTENING"
        assert snapshot["prompt"] is None
        assert snapshot["speaking"]
    session.handle("speech_end", 100001)
    assert session.handle("tick", 103000)["state"] == "LISTENING"
    assert session.handle("tick", 103001)["state"] == "PROMPTING"


def test_fresh_voice_at_the_deadline_wins_over_timeout_prompt():
    session = start()
    session.handle("speech_start", 100)
    session.handle("speech_end", 200)
    snapshot = session.handle_batch([("tick", {}), ("speech_start", {})], 3200)
    assert snapshot["state"] == "LISTENING"
    assert snapshot["prompt"] is None
    assert snapshot["speaking"]
    session.handle("speech_end", 3400)
    assert session.handle("tick", 6399)["state"] == "LISTENING"
    assert session.handle("tick", 6400)["state"] == "PROMPTING"


def test_valid_settlement_at_deadline_is_used_before_prompting():
    session = start()
    session.handle("speech_start", 100)
    session.handle("speech_end", 200)
    snapshot = session.handle_batch(
        [("tick", {}), ("transcript", {"text": "大家好", "evidence_id": 0})], 3200
    )
    assert snapshot["state"] == "PROMPTING"
    assert snapshot["transition_trigger"] == "silence_threshold_reached"
    assert snapshot["reliable"]
    assert snapshot["prompt"]["text"] == "欢迎参加本次测试。"


@pytest.mark.parametrize("terminal_event", ["pause", "service_fault", "end"])
def test_pause_fault_and_end_still_prevent_deadline_prompting(terminal_event):
    session = start()
    session.handle("speech_start", 100)
    session.handle("speech_end", 200)
    snapshot = session.handle_batch([("tick", {}), (terminal_event, {})], 3200)
    assert snapshot["state"] == ("TERMINATED" if terminal_event == "end" else "PAUSED")
    assert snapshot["prompt"] is None


def test_end_of_script_does_not_repeat_last_unit_or_emit_timeout_feedback():
    session = start()
    session.handle("transcript", 50, text="最后谢谢大家", evidence_id=0)
    assert session.prompt_position == len(SCRIPT)
    session.handle("speech_start", 100)
    session.handle("speech_end", 200)
    before = alignment(session)
    feedback = session.feedback_id
    snapshot = session.handle("tick", 3200)
    assert snapshot["state"] == "LISTENING"
    assert snapshot["prompt"] is None
    assert snapshot["at_end"]
    assert alignment(session) == before
    assert session.feedback_id == feedback


def test_playback_advances_only_prompt_anchor_and_late_old_generation_text_cannot_move_it():
    session = start()
    session.handle("speech_start", 100)
    session.handle("speech_end", 200)
    old_generation = session.generation
    session.handle("tick", 3200)
    first = session.prompt
    before_reading = session.reading_position
    assert first is not None
    session.handle("transcript", 3201, text="最后谢谢大家", generation=old_generation)
    assert session.reading_position == before_reading
    assert session.prompt == first
    session.handle("playback_finished", 3300, prompt_id=first.id)
    assert session.reading_position is None
    assert session.aligned_transcript == ""
    assert not session.reliable
    assert session.prompt_position == first.next_position
    assert session.snapshot()["alignment_status"] == "pending"
    assert session.handle("tick", 6299)["state"] == "LISTENING"
    snapshot = session.handle("tick", 6300)
    assert snapshot["prompt"]["text"] == "欢迎参加本次测试。"
    assert snapshot["transition_trigger"] == "recognition_wait_timeout"
    assert session.reading_position is None
    assert session.recovery_attempts == 0


def test_manual_reposition_provides_saved_anchor_without_later_voice_guessing_progress():
    session = start()
    session.handle("reposition", 50, position=SCRIPT.index("参"))
    session.handle("speech_start", 100)
    session.handle("speech_end", 200)
    before = alignment(session)
    snapshot = session.handle("tick", 3200)
    assert snapshot["state"] == "PROMPTING"
    assert snapshot["prompt"]["text"] == "参加本次测试。"
    assert alignment(session) == before
