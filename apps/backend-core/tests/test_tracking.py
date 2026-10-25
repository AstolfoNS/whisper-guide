import pytest

from app.domain.tracking import ScriptAligner, ScriptTracker, content_units


def test_units_preserve_original_offsets_and_mixed_language() -> None:
    text = "欢迎参加 AI 2026 大会，增长 3.14%。"
    units = content_units(text)
    assert [unit.text for unit in units] == [
        "欢",
        "迎",
        "参",
        "加",
        "AI",
        "2026",
        "大",
        "会",
        "增",
        "长",
        "3.14%",
    ]
    assert all(text[unit.start : unit.end] == unit.text for unit in units)


@pytest.mark.parametrize("boundary", ["，", ",", "。", ".", "？", "?", "！", "!", "；", ";"])
def test_every_supported_boundary_ends_a_segment(boundary: str) -> None:
    tracker = ScriptTracker(f"第一段{boundary}下一段。")
    segment = tracker.segment(0)
    assert segment is not None
    assert segment.text == f"第一段{boundary}"
    assert tracker.text[segment.next_position :] == "下一段。"


def test_decimal_and_consecutive_punctuation_do_not_create_empty_segments() -> None:
    tracker = ScriptTracker("增长3.14%，？！  “继续努力。”")
    first = tracker.segment(0)
    assert first is not None
    assert first.text == "增长3.14%，？！"
    second = tracker.segment(first.next_position)
    assert second is not None
    assert second.text == "继续努力。”"
    assert tracker.segment(second.next_position) is None


def test_segment_snaps_inside_words_and_numbers_to_their_beginning() -> None:
    tracker = ScriptTracker("欢迎 AI 2026 大会")
    assert tracker.segment(4).text == "AI 2026 大会"
    assert tracker.segment(8).text == "2026 大会"


def test_exact_match_can_restart_from_last_spoken_unit() -> None:
    tracker = ScriptTracker("今天我们讨论人工智能，接着介绍应用场景。")
    result = tracker.match("今天我们讨论人工", None)
    assert result is not None
    assert tracker.segment(result.position).text == "工智能，"


def test_fuzzy_matching_tolerates_one_wrong_character_and_small_interjection() -> None:
    tracker = ScriptTracker("今天我们讨论人工智能，接着介绍应用场景。")
    for transcript in ["今天我们讨论人工智呢", "今天我们嗯讨论人工智能"]:
        result = tracker.match(transcript, None)
        assert result is not None
        assert result.confidence >= 0.65
        assert result.position >= tracker.text.index("智")


def test_repeated_sentence_uses_nearby_context_and_supports_readback() -> None:
    tracker = ScriptTracker("今天我们讨论人工智能。接着介绍应用场景。今天我们讨论人工智能。")
    later = tracker.text.rindex("今天")
    result = tracker.match("今天我们讨论人工智能", later)
    assert result is not None
    assert result.position == tracker.text.rindex("能")
    readback = tracker.match("接着介绍应用场景", result.position)
    assert readback is not None
    assert readback.position == tracker.text.index("景")


def test_short_recognition_cannot_jump_to_far_away_occurrence() -> None:
    tracker = ScriptTracker("开场介绍与大家见面欢迎来到我们的年度大会。")
    assert tracker.match("大会", None) is None
    assert tracker.match("完全无关的识别文本", None) is None


def test_script_must_have_content_units() -> None:
    with pytest.raises(ValueError):
        ScriptTracker("，？！   ")


def test_repeated_streaming_prefix_is_collapsed_only_when_script_matches() -> None:
    tracker = ScriptTracker("大家好。")
    assert tracker.normalize_transcript("大大大大家好好好好", None) == "大家好"

    other = ScriptTracker("大大大好。")
    assert other.normalize_transcript("大大大好", None) == "大大大好"


def test_script_aligner_corrects_homophone_to_script_text() -> None:
    pytest.importorskip("pypinyin")
    tracker = ScriptTracker("大家好，今天开始测试。")
    result = ScriptAligner(tracker).align("大甲好", None)
    assert result is not None
    assert result.matched_text == "大家好"
    assert result.confidence >= 0.68


def test_script_aligner_prefers_nearby_duplicate_sentence() -> None:
    pytest.importorskip("pypinyin")
    text = "大家好。继续努力。大家好。"
    tracker = ScriptTracker(text)
    result = ScriptAligner(tracker).align("大家好", text.index("大家好", 1))
    assert result is not None
    assert result.position == text.rindex("好")
