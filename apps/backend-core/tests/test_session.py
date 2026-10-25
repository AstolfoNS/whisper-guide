import pytest
from pydantic import ValidationError

from app.domain.session import SessionError, SessionState, SpeechSession

SCRIPT = "今天我们讨论人工智能，接着介绍应用场景。"


@pytest.fixture
def session() -> SpeechSession:
    return SpeechSession("script-1", SCRIPT)


def start_prompt(session: SpeechSession, now: int = 3000) -> dict:
    session.handle("start", 0)
    return session.handle("tick", now)


def test_ready_never_times_out_and_first_silence_prompts_start(session) -> None:
    assert session.handle("tick", 100000)["state"] == "READY"
    session.handle("start", 100000)
    assert session.handle("tick", 102999)["state"] == "LISTENING"
    snapshot = session.handle("tick", 103000)
    assert snapshot["state"] == "PROMPTING"
    assert snapshot["prompt"]["text"] == "今天我们讨论人工智能，"
    assert snapshot["reading_position"] is None


def test_speech_cancels_timer_and_uses_last_voice_end(session) -> None:
    session.handle("start", 0)
    session.handle("speech_start", 2999)
    session.handle("transcript", 3000, text="今天我们讨论人工")
    assert session.handle("tick", 9999)["state"] == "LISTENING"
    session.handle("speech_end", 10000)
    assert session.handle("tick", 12999)["state"] == "LISTENING"
    assert session.handle("tick", 13000)["prompt"]["text"] == "工智能，"


@pytest.mark.parametrize("text,last", [("欢迎 AI", "AI"), ("欢迎 AI 2026", "2026")])
def test_prompt_begins_at_whole_last_word_or_number(text, last) -> None:
    session = SpeechSession("mixed", "欢迎 AI 2026 大会。")
    session.handle("start", 0)
    session.handle("transcript", 1, text=text)
    snapshot = session.handle("tick", 3000)
    assert snapshot["prompt"]["text"].startswith(last)


def test_completed_playback_advances_only_prompt_position(session) -> None:
    first = start_prompt(session)["prompt"]
    session.handle("playback_finished", 10000, prompt_id=first["id"])
    assert session.reading_position is None
    assert session.handle("tick", 12999)["state"] == "LISTENING"
    second = session.handle("tick", 13000)["prompt"]
    assert second["text"] == "接着介绍应用场景。"
    session.handle("playback_finished", 20000, prompt_id=second["id"])
    assert session.handle("tick", 999999)["state"] == "LISTENING"
    assert session.snapshot()["at_end"]


def test_interruption_replays_current_segment_and_ignores_old_completion(session) -> None:
    first = start_prompt(session)["prompt"]
    session.handle("interrupt", 3100)
    session.handle("playback_finished", 3200, prompt_id=first["id"])
    assert session.prompt_position == first["start"]
    assert session.handle("tick", 6099)["state"] == "LISTENING"
    replay = session.handle("tick", 6100)["prompt"]
    assert replay["text"] == first["text"]
    assert replay["id"] != first["id"]


def test_pause_does_not_inherit_old_silence(session) -> None:
    session.handle("start", 0)
    session.handle("pause", 2500)
    assert session.handle("tick", 100000)["state"] == "PAUSED"
    session.handle("resume", 100000)
    assert session.handle("tick", 102999)["state"] == "LISTENING"
    assert session.handle("tick", 103000)["state"] == "PROMPTING"


def test_pause_while_prompting_restarts_in_listening_and_retains_segment(session) -> None:
    first = start_prompt(session)["prompt"]
    session.handle("pause", 3100)
    session.handle("playback_finished", 3200, prompt_id=first["id"])
    assert session.prompt_position == first["start"]
    assert session.handle("resume", 10000)["state"] == "LISTENING"
    assert session.handle("tick", 13000)["prompt"]["text"] == first["text"]


def test_options_only_change_ready_or_paused_and_remain_strict(session) -> None:
    session.handle("update_options", 0, silence_timeout_ms=1000)
    session.handle("start", 0)
    with pytest.raises(SessionError):
        session.handle("update_options", 10, silence_timeout_ms=1500)
    session.handle("tick", 1000)
    with pytest.raises(SessionError):
        session.handle("update_options", 1001, silence_timeout_ms=1500)
    session.handle("pause", 1100)
    with pytest.raises(ValidationError):
        session.handle("update_options", 1101, silence_timeout_ms=True)
    assert session.options.silence_timeout_ms == 1000
    session.handle("update_options", 1102, silence_timeout_ms=1500)
    session.handle("resume", 10000)
    assert session.handle("tick", 11499)["state"] == "LISTENING"
    assert session.handle("tick", 11500)["state"] == "PROMPTING"


def test_location_loss_warns_once_then_prompts_saved_anchor_without_recovering_position(
    session,
) -> None:
    session.handle("start", 0)
    session.handle("speech_start", 1)
    first = session.handle("transcript", 2, text="一段毫无关系的话语")
    session.handle("transcript", 3, text="另一段无关文本")
    assert session.feedback_id == first["feedback_id"] == 1
    session.handle("speech_end", 4)
    before = (session.reading_position, session.prompt_position, session.aligned_transcript)
    snapshot = session.handle("tick", 3004)
    assert snapshot["state"] == "PROMPTING"
    assert snapshot["transition_trigger"] == "recognition_wait_timeout"
    assert snapshot["alignment_status"] == "mismatch"
    assert not session.reliable
    assert (session.reading_position, session.prompt_position, session.aligned_transcript) == before
    assert snapshot["prompt"]["start"] == before[1]
    assert "已保存的提示起点" in snapshot["message"]


def test_pending_stt_prompts_saved_anchor_after_deadline_without_guessing_a_position(
    session,
) -> None:
    session.handle("start", 0)
    session.handle("speech_start", 1)
    session.handle("speech_end", 100)
    assert session.handle("tick", 3099)["state"] == "LISTENING"
    snapshot = session.handle("tick", 3100)
    assert snapshot["state"] == "PROMPTING"
    assert snapshot["transition_trigger"] == "recognition_wait_timeout"
    assert snapshot["alignment_status"] == "pending"
    assert not session.reliable
    assert session.reading_position is None
    assert session.prompt_position == 0


def test_user_can_resume_near_prompt_after_system_has_read_ahead() -> None:
    text = "开场欢迎。" + "甲乙丙丁戊己庚辛壬癸，" * 12 + "最后介绍应用场景。"
    session = SpeechSession("long", text)
    session.handle("start", 0)
    session.prompt_position = text.index("最后")
    session.handle("speech_start", 100)
    snapshot = session.handle("transcript", 101, text="最后介绍应用场景")
    assert snapshot["reliable"]
    assert snapshot["reading_position"] == text.index("景")


def test_relocation_and_reliable_user_speech_restore_automatic_prompt(session) -> None:
    session.handle("start", 0)
    session.handle("transcript", 1, text="无关内容")
    session.handle("reposition", 2, position=SCRIPT.index("工"))
    assert session.handle("tick", 3002)["prompt"]["text"] == "工智能，"
    session.handle("interrupt", 3003)
    session.handle("transcript", 3004, text="接着介绍应用场景")
    assert session.reliable
    assert session.reading_position == SCRIPT.index("景")
    assert session.prompt_position == len(SCRIPT)


def test_stale_recognition_results_cannot_modify_position_after_pause(session) -> None:
    session.handle("start", 0)
    generation = session.generation
    session.handle("pause", 10)
    session.handle("resume", 20)
    session.handle("transcript", 21, text="今天我们讨论人工", generation=generation)
    assert session.reading_position is None
    session.handle("speech_start", 22, generation=generation)
    assert not session.speaking


def test_batch_event_priorities(session) -> None:
    session.handle("start", 0)
    assert session.handle_batch([("tick", {}), ("pause", {})], 3000)["state"] == "PAUSED"
    session.handle("resume", 4000)
    snapshot = session.handle_batch([("tick", {}), ("speech_start", {})], 7000)
    assert snapshot["state"] == "LISTENING"
    session.handle("transcript", 7001, text="今天我们")
    session.handle("speech_end", 7002)
    prompt = session.handle("tick", 10002)["prompt"]
    snapshot = session.handle_batch(
        [("playback_finished", {"prompt_id": prompt["id"]}), ("interrupt", {})], 11000
    )
    assert snapshot["prompt_position"] == prompt["start"]


def test_service_failure_pauses_until_explicit_resume(session) -> None:
    start_prompt(session)
    snapshot = session.handle("service_fault", 3001, reason="网络中断")
    assert snapshot["state"] == "PAUSED"
    assert snapshot["pause_reason"] == "网络中断"
    with pytest.raises(SessionError):
        session.handle("resume", 3002, service_available=False)
    assert session.state == SessionState.PAUSED
    assert session.handle("resume", 3003)["state"] == "LISTENING"


@pytest.mark.parametrize("state", ["ready", "listening", "prompting", "paused"])
def test_end_is_available_everywhere_and_old_callbacks_are_ignored(session, state) -> None:
    if state != "ready":
        session.handle("start", 0)
    if state in {"prompting", "paused"}:
        session.handle("tick", 3000)
    if state == "paused":
        session.handle("pause", 3001)
    terminated = session.handle("end", 4000)
    for event in ["start", "resume", "tick", "speech_start", "playback_finished"]:
        assert session.handle(event, 99999) == terminated


def test_invalid_commands_and_relocation_are_rejected(session) -> None:
    with pytest.raises(SessionError):
        session.handle("resume", 0)
    with pytest.raises(SessionError):
        session.handle("unknown", 0)
    for position in [-1, len(SCRIPT) + 1, True, "3"]:
        with pytest.raises(SessionError):
            session.handle("reposition", 0, position=position)


def test_filler_transcript_does_not_disable_automatic_prompt(session) -> None:
    session.handle("start", 0)
    session.handle("speech_start", 1)

    snapshot = session.handle("transcript", 2, text="嗯")
    assert snapshot["reliable"]

    session.handle("speech_end", 3)
    assert session.handle("tick", 3003)["state"] == "PROMPTING"
