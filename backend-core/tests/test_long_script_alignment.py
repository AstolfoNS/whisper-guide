"""Long coherent utterances must not be mistaken for distant cursor jumps."""

import pytest

from app.domain.tracking import ScriptAligner, ScriptTracker

LONG_SENTENCE = (
    "今天我们讨论语音识别系统在长时间连续朗读过程中的稳定性以及噪声处理和讲稿位置对齐问题"
    "接下来介绍本地处理流程与云端识别服务之间的协调机制确保每次朗读都能够根据当前上下文"
    "准确找到原文中的位置并在用户停止说话以后继续提供提示"
)


@pytest.mark.parametrize("length", [36, 85])
def test_exact_long_utterance_at_script_start_advances_to_its_last_unit(length):
    tracker = ScriptTracker(LONG_SENTENCE + "。谢谢大家。")
    result = ScriptAligner(tracker).align(LONG_SENTENCE[:length], None)
    assert result is not None
    assert result.position == length - 1
    assert result.matched_text == LONG_SENTENCE[:length]


def test_exact_long_utterance_continues_from_the_previously_read_position():
    prefix = "各位来宾大家好。"
    tracker = ScriptTracker(prefix + LONG_SENTENCE + "。")
    anchor = prefix.index("好")
    result = ScriptAligner(tracker).align(LONG_SENTENCE[:85], anchor)
    assert result is not None
    assert result.position == len(prefix) + 84
    assert result.matched_text == LONG_SENTENCE[:85]


def test_long_utterance_repeated_later_uses_the_nearby_occurrence():
    spoken = LONG_SENTENCE[:36]
    text = (
        spoken + "。" + "中间段落用于介绍完全不同内容并且避免远处重复导致错误跳转。" + spoken + "。"
    )
    tracker = ScriptTracker(text)
    result = ScriptAligner(tracker).align(spoken, None)
    assert result is not None
    assert result.position == len(spoken) - 1
    later = text.rindex(spoken)
    repeated = ScriptAligner(tracker).align(spoken, later)
    assert repeated is not None
    assert repeated.position == later + len(spoken) - 1


def test_far_short_repeat_does_not_become_valid_from_span_scoring():
    text = "开场介绍" + "这是用于隔开远处候选内容的段落" * 3 + "大家好。"
    assert ScriptAligner(ScriptTracker(text)).align("大家好", None) is None


def test_long_utterance_does_not_turn_unrelated_noise_text_into_a_match():
    aligner = ScriptAligner(ScriptTracker(LONG_SENTENCE))
    assert aligner.align("火星飞船爆炸以后量子机械忽然进入超空间", None) is None


def test_long_mixed_utterance_keeps_whole_english_and_number_units():
    text = LONG_SENTENCE[:36] + " FastAPI 2026.10 sherpa-onnx 3.14% 模型验收通过。"
    tracker = ScriptTracker(text)
    result = ScriptAligner(tracker).align(text[:-1], None)
    assert result is not None
    assert result.matched_text == text[:-1]
    assert result.position == text.rindex("过")
    assert [unit.text for unit in tracker.units if len(unit.text) > 1] == [
        "FastAPI",
        "2026.10",
        "sherpa-onnx",
        "3.14%",
    ]


def test_local_readback_remains_possible_after_a_long_utterance():
    text = "开场介绍。" + LONG_SENTENCE[:36] + "。欢迎大家继续交流。"
    tracker = ScriptTracker(text)
    anchor = text.index("流")
    readback = ScriptAligner(tracker).align(LONG_SENTENCE[20:36], anchor)
    assert readback is not None
    assert readback.position == text.index(LONG_SENTENCE[:36]) + 35


def test_long_segment_cannot_use_an_unmatched_prefix_to_jump_to_a_matching_tail():
    source = LONG_SENTENCE[:85]
    tracker = ScriptTracker(source)
    transcript = "火星飞船发射以后的量子机械构造过程完全不相关" + source[-64:]
    assert ScriptAligner(tracker).align(transcript, None) is None


def test_long_exact_segment_far_ahead_keeps_the_distance_penalty():
    spoken = LONG_SENTENCE[:85]
    tracker = ScriptTracker("开场准备内容用于说明不同话题" * 4 + spoken)
    assert ScriptAligner(tracker).align(spoken, None) is None


def test_exceptionally_long_input_keeps_alignment_work_bounded():
    source = LONG_SENTENCE * 4
    tracker = ScriptTracker(source)
    assert ScriptAligner(tracker).align(source[:257], None) is None


def test_maximum_supported_long_segment_keeps_its_full_original_span():
    source = (LONG_SENTENCE * 3)[:256]
    result = ScriptAligner(ScriptTracker(source + "。后续内容。")).align(source, None)
    assert result is not None
    assert result.position == len(source) - 1
    assert result.matched_text == source
