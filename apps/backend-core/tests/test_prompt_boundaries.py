"""Prompt boundaries follow reliable reading without replaying a completed fragment."""

import pytest

from app.domain.session import SpeechSession
from app.domain.tracking import ScriptTracker


@pytest.mark.parametrize("boundary", ["，", ",", "。", ".", "？", "?", "！", "!", "；", ";"])
def test_completed_fragment_prompts_the_next_fragment(boundary):
    text = f"已经完成{boundary}接着讲下一段。"
    session = SpeechSession("boundary", text)
    session.handle("start", 0)
    snapshot = session.handle("transcript", 1, text="已经完成")

    assert snapshot["reading_position"] == text.index("成")
    assert snapshot["prompt_position"] == text.index("接")
    assert session.handle("tick", 2999)["state"] == "LISTENING"
    prompt = session.handle("tick", 3000)["prompt"]
    assert prompt["text"] == "接着讲下一段。"
    assert session.reading_position == text.index("成")


def test_completed_fragment_skips_consecutive_punctuation_spaces_and_quotes():
    text = "已经完成，？！  ”’）  接着讲下一段。"
    tracker = ScriptTracker(text)
    assert tracker.prompt_position_after_reading(text.index("成")) == text.index("接")


@pytest.mark.parametrize("tail", ["", "。", "？！", "  ", "。” \n\n"])
def test_user_reading_to_document_end_does_not_replay_last_unit(tail):
    text = f"全文已经结束{tail}"
    session = SpeechSession("end", text)
    session.handle("start", 0)
    snapshot = session.handle("transcript", 1, text="全文已经结束")

    assert snapshot["reading_position"] == text.index("束")
    assert snapshot["prompt_position"] == len(text)
    assert snapshot["at_end"]
    assert session.handle("tick", 99999)["state"] == "LISTENING"
    assert session.prompt is None


@pytest.mark.parametrize(
    "text,transcript,next_text",
    [
        ("欢迎 OpenAI, keep going.", "欢迎 OpenAI", "keep going."),
        ("今年 2026，继续介绍。", "今年 2026", "继续介绍。"),
        ("数值 3.14%，继续介绍。", "数值 3.14%", "继续介绍。"),
    ],
)
def test_whole_word_and_number_at_fragment_end_move_to_next_unit(text, transcript, next_text):
    session = SpeechSession("units", text)
    session.handle("start", 0)
    session.handle("transcript", 1, text=transcript)
    assert session.handle("tick", 3000)["prompt"]["text"] == next_text


@pytest.mark.parametrize(
    "text,transcript,prompt_text",
    [
        ("欢迎 OpenAI 模型。", "欢迎 OpenAI", "OpenAI 模型。"),
        ("今年 2026 继续介绍。", "今年 2026", "2026 继续介绍。"),
        ("数值 3.14% 继续增加。", "数值 3.14%", "3.14% 继续增加。"),
        ("遇到困难不轻易放弃。", "遇到困难", "难不轻易放弃。"),
    ],
)
def test_mid_fragment_pause_keeps_last_complete_content_unit(text, transcript, prompt_text):
    session = SpeechSession("mid-fragment", text)
    session.handle("start", 0)
    session.handle("transcript", 1, text=transcript)
    assert session.handle("tick", 3000)["prompt"]["text"] == prompt_text


@pytest.mark.parametrize("separator", ["\n\n", "\r\n\r\n", "\n \t\n", "\r\n \t\r\n"])
def test_blank_line_is_a_shared_prompt_and_reading_boundary(separator):
    text = f"第一段结束{separator}第二段开始。"
    tracker = ScriptTracker(text)
    first = tracker.segment(0)
    assert first.text == "第一段结束"
    assert first.next_position == text.index("第二")
    assert tracker.prompt_position_after_reading(text.index("束")) == first.next_position

    session = SpeechSession("paragraph", text)
    session.handle("start", 0)
    session.handle("transcript", 1, text="第一段结束")
    assert session.handle("tick", 3000)["prompt"]["text"] == "第二段开始。"


@pytest.mark.parametrize("separator", ["\n", "\r\n", "\n \t"])
def test_single_line_break_is_a_soft_wrap(separator):
    text = f"内容继续{separator}不切分。"
    tracker = ScriptTracker(text)
    assert tracker.segment(0).text == text
    assert tracker.prompt_position_after_reading(text.index("续")) == text.index("续")


def test_readback_to_an_earlier_fragment_end_rebuilds_local_prompt_position():
    text = "第一段开始。第二段继续。第三段收尾。"
    session = SpeechSession("readback", text)
    session.handle("start", 0)
    session.handle("reposition", 1, position=text.index("第三"))
    snapshot = session.handle("transcript", 2, text="第一段开始")
    assert snapshot["reading_position"] == text.index("始")
    assert snapshot["prompt_position"] == text.index("第二")
    assert session.handle("tick", 3001)["prompt"]["text"] == "第二段继续。"


def test_manual_reposition_keeps_explicit_unit_even_when_it_ends_a_fragment():
    text = "第一段结束。第二段开始。"
    session = SpeechSession("manual", text)
    session.handle("start", 0)
    session.handle("reposition", 1, position=text.index("束"))
    assert session.handle("manual_prompt", 2)["prompt"]["text"] == "束。"


def test_pause_and_interrupt_keep_the_selected_next_fragment_for_replay():
    text = "第一段结束。第二段开始。"
    session = SpeechSession("replay", text)
    session.handle("start", 0)
    session.handle("transcript", 1, text="第一段结束")
    first = session.handle("tick", 3000)["prompt"]
    assert first["text"] == "第二段开始。"

    session.handle("interrupt", 3100)
    session.handle("playback_finished", 3200, prompt_id=first["id"])
    replay = session.handle("tick", 6100)["prompt"]
    assert replay["start"] == first["start"]
    session.handle("pause", 6200)
    session.handle("resume", 10000)
    third = session.handle("tick", 13000)["prompt"]
    assert third["start"] == first["start"]
    assert session.reading_position == text.index("束")
