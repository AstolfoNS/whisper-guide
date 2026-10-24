"""Automatic reacquisition must preserve prompt safety while continuing to listen."""

import asyncio

import pytest

from app.domain.session import SpeechSession
from app.domain.tracking import ScriptAligner, ScriptTracker
from app.infrastructure.speech import RecognitionUpdate


def distant_script() -> tuple[str, str, str]:
    first = "我们继续介绍实时讲稿定位的工作流程"
    second = "系统需要根据后续正文恢复可靠位置"
    return "开场白。" + "甲" * 600 + first + "。" + second + "。接下来开始演示。", first, second


def start(script: str) -> SpeechSession:
    session = SpeechSession("script", script, silence_timeout_ms=1000)
    session.handle("start", 0)
    return session


def test_audible_short_mismatch_does_not_authorize_prompt_at_old_cursor():
    session = start("大家好。欢迎测试。")
    session.handle("transcript", 1, text="大家好")
    position = session.reading_position
    session.handle("speech_start", 2)
    session.handle("transcript", 3, text="来")
    session.handle("speech_end", 4)
    session.handle("tick", 2000)
    assert session.reading_position == position
    assert not session.reliable
    assert session.state.value == "LISTENING"
    assert session.snapshot()["alignment_status"] == "mismatch"


def test_unique_nearby_text_recovers_without_reposition():
    session = start("大家好。欢迎参加本次测试。")
    session.handle("transcript", 1, text="这段文字无法匹配任何正文")
    session.handle("transcript", 2, text="大家好")
    assert session.reliable
    assert session.reading_position == 2
    assert session.recovery_status == "idle"


def test_index_finds_unique_anchor_outside_the_old_512_unit_window():
    script, first, _ = distant_script()
    aligner = ScriptAligner(ScriptTracker(script))
    candidates = aligner.recovery_candidates(first)
    assert len(candidates) == 1
    result = candidates[0]
    assert result.position == script.index(first) + len(first) - 1


def test_distant_jump_requires_two_independent_continuous_phrases():
    script, first, second = distant_script()
    session = start(script)
    session.handle("transcript", 1, text=first, evidence_id=0)
    assert not session.reliable
    assert session.reading_position is None
    assert session.recovery_status == "confirming"
    session.handle("transcript", 2, text=second, evidence_id=1)
    assert session.reliable
    assert session.reading_position == script.index(second) + len(second) - 1
    session.handle("speech_end", 3)
    session.handle("tick", 1003)
    assert session.state.value == "PROMPTING"
    assert session.prompt.text == "接下来开始演示。"


def test_same_segment_revisions_do_not_count_as_independent_recovery():
    script, first, second = distant_script()
    tail = "接下来开始演示"
    session = start(script)
    session.handle("transcript", 1, text=first, evidence_id=7)
    session.handle("transcript", 2, text=first + second, evidence_id=7)
    assert not session.reliable
    session.handle("transcript", 3, text=first + second, evidence_id=8)
    assert not session.reliable  # repeated text is not advancing evidence either
    session.handle("transcript", 4, text=tail, evidence_id=9)
    assert session.reliable
    assert session.reading_position == script.index(tail) + len(tail) - 1


def test_repeated_nearby_phrase_cannot_bypass_recovery_ambiguity_check():
    session = start("开场。" + "大家好欢迎回来。" * 3)
    session.handle("transcript", 1, text="这段文字无法匹配任何正文")
    session.handle("transcript", 2, text="大家好欢迎回来")
    assert not session.reliable
    assert session.reading_position is None
    assert session.recovery_status == "ambiguous"


def test_following_phrase_disambiguates_a_repeated_distant_passage():
    repeated = "大家好欢迎参加今天的演讲"
    following = "这一部分介绍声音证据和定位恢复"
    script = (
        "开场白。"
        + repeated
        + "。第一部分介绍界面操作。"
        + "甲" * 600
        + repeated
        + "。"
        + following
        + "。谢谢大家。"
    )
    session = start(script)
    session.handle("transcript", 1, text="完全不同的背景声音和文字")
    session.handle("transcript", 2, text=repeated, evidence_id=1)
    assert session.recovery_status == "ambiguous"
    session.handle("transcript", 3, text=following, evidence_id=2)
    assert session.reliable
    assert session.reading_position == script.index(following) + len(following) - 1


def test_far_short_text_never_recovers_a_cursor():
    session = start("开场。" + "甲" * 700 + "大家好。谢谢大家。")
    session.handle("transcript", 1, text="大家好")
    session.handle("transcript", 2, text="谢谢大家")
    assert not session.reliable
    assert session.reading_position is None


def test_manual_reanchor_invalidates_tentative_recovery_chain():
    script, first, second = distant_script()
    session = start(script)
    session.handle("transcript", 1, text=first, evidence_id=0)
    session.handle("reposition", 2, position=0)
    session.handle("transcript", 3, text=second, evidence_id=1)
    assert not session.reliable
    assert session.reading_position == 0
    assert session.recovery_status == "confirming"


def test_preview_does_not_commit_or_reenable_automatic_prompt():
    script, first, _ = distant_script()
    session = start(script)
    session.handle("transcript", 1, text="一段不相关的背景讲话内容")
    session.preview_transcript(first)
    assert session.provisional_position == script.index(first) + len(first) - 1
    assert session.reading_position is None
    assert not session.reliable
    assert session.prompt_position == 0
    session.handle("pause", 2)
    assert session.provisional_position is None


def test_mixed_units_and_phonetic_anchor_keep_original_boundaries():
    phrase = "欢迎使用 FastAPI 2026.10 完成实时定位"
    script = "开场。" + "甲" * 600 + phrase + "。"
    aligner = ScriptAligner(ScriptTracker(script))
    candidates = aligner.recovery_candidates(phrase)
    assert len(candidates) == 1
    result = candidates[0]
    assert result.matched_text == phrase
    phonetic = ScriptAligner(ScriptTracker("我们会坚持自律好好吃饭。"))
    assert len(phonetic.recovery_candidates("我们会坚持自绿好好吃饭")) == 1


def test_refinement_context_retrieves_distant_candidate_without_oversized_requests():
    script, first, _ = distant_script()
    rough = first[:-1] + "城"
    aligner = ScriptAligner(ScriptTracker(script))
    assert first in aligner.recovery_window(None, rough)
    assert len(aligner.recovery_window(None, rough)) <= 4096
    assert aligner.supports_refinement(rough, first)
    assert not aligner.supports_refinement("来", first)


@pytest.mark.parametrize("length", [257, 1000])
def test_global_recovery_bounds_recognized_text(length):
    aligner = ScriptAligner(ScriptTracker("甲" * 10000))
    assert not aligner.recovery_candidates("甲" * length)


def test_full_runtime_recovers_after_noise_and_prompts_without_manual_action(monkeypatch):
    from test_noise_guard_runtime import ac_pcm, frame, listening, silence

    async def scenario():
        script, first, second = distant_script()
        runtime, speech, clock = await listening(monkeypatch, script)
        try:
            for segment, text in enumerate(("这段背景声音无法匹配讲稿", first, second)):
                timestamp = 1000 + segment * 400
                await frame(
                    runtime,
                    speech,
                    clock,
                    timestamp,
                    ac_pcm(),
                    RecognitionUpdate(text, is_final=True, segment_id=segment),
                    vad=True,
                )
                await frame(runtime, speech, clock, timestamp + 250, silence(250))
            assert runtime.session.reliable
            assert runtime.session.reading_position == script.index(second) + len(second) - 1
            await runtime.tick(2800)
            assert runtime.session.state.value == "PROMPTING"
            assert runtime.session.prompt.text == "接下来开始演示。"
        finally:
            runtime.cancel_prompt()
            runtime.release_stream()

    asyncio.run(scenario())


def test_retracted_anchor_cannot_confirm_a_following_segment():
    script, first, second = distant_script()
    session = start(script)
    session.handle("transcript", 1, text=first, evidence_id=0)
    session.retract_evidence(0)
    session.handle("transcript", 2, text=second, evidence_id=1)
    assert not session.reliable
    assert session.reading_position is None
    assert session.recovery_status == "confirming"


@pytest.mark.parametrize("withdrawn", [0, 1])
def test_withdrawing_either_distant_anchor_invalidates_confirmed_location(withdrawn):
    script, first, second = distant_script()
    session = start(script)
    session.handle("transcript", 1, text=first, evidence_id=0)
    session.handle("transcript", 2, text=second, evidence_id=1)
    assert session.reliable
    position = session.reading_position
    session.retract_evidence(withdrawn)
    session.handle("filler", 3)
    session.handle("speech_end", 4)
    session.handle("tick", 2000)
    assert not session.reliable
    assert session.reading_position == position
    assert session.snapshot()["alignment_status"] == "mismatch"
    assert session.state.value == "LISTENING"


def test_manual_position_is_not_invalidated_by_withdrawn_old_alignment():
    session = start("大家好。欢迎测试。")
    session.handle("transcript", 1, text="大家好", evidence_id=0)
    session.handle("reposition", 2, position=4)
    session.retract_evidence(0)
    assert session.reliable
    assert session.reading_position == 4


def test_distant_readback_recovers_from_two_forward_phrases_at_the_earlier_location():
    first = "我们继续介绍实时讲稿定位的工作流程"
    second = "系统需要根据后续正文恢复可靠位置"
    script = first + "。" + second + "。" + "甲" * 600 + "最后总结全部内容。"
    session = start(script)
    session.handle("reposition", 1, position=script.index("最后"))
    session.handle("transcript", 2, text=first, evidence_id=0)
    assert not session.reliable
    session.handle("transcript", 3, text=second, evidence_id=1)
    assert session.reliable
    assert session.reading_position == script.index(second) + len(second) - 1
