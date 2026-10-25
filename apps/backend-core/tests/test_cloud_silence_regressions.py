"""Late cloud revisions must settle locally without replaying finalized segments."""

import asyncio
import threading
from array import array
from collections import deque
from types import SimpleNamespace

import pytest

import app.api.session as runtime_api
from app.api.session import SessionRuntime
from app.domain.session import SpeechSession
from app.infrastructure.speech import RecognitionUpdate


class QueuedCloudStream:
    available = True
    effective_provider = "cloud"

    def __init__(self, updates=(), voiced=()):
        self.updates = deque(updates)
        self.voiced = deque(voiced)

    def is_speech(self, pcm):
        return self.voiced.popleft() if self.voiced else False

    def accept_pcm(self, pcm):
        return self.updates.popleft() if self.updates else None

    def close(self):
        pass


class CloudSpeech:
    settings = SimpleNamespace(llm_align_mode="off")

    def __init__(self, stream):
        self.stream = stream

    def status(self):
        return {"stt": "ready", "tts": "ready"}

    def create_stream(self):
        return self.stream

    async def synthesize(self, text):
        return b"in-memory-prompt"


async def listening(stream, script="大家好。欢迎测试。继续下一句。"):
    runtime = SessionRuntime(
        SpeechSession("script", script, silence_timeout_ms=1000),
        "稿",
        CloudSpeech(stream),
        provider_name="cloud",
    )
    await runtime.control({"type": "start"})
    await runtime.control({"type": "audio_ready"})
    return runtime


async def frame(runtime, monkeypatch, timestamp):
    monkeypatch.setattr(runtime_api, "now_ms", lambda: timestamp)
    # Distinct logical reads carry a real 80 ms weak voice, separated by
    # 200 ms silence on the PCM clock rather than only jumping wall time.
    await runtime.audio(bytes(6400) + array("h", [-100, 100] * 640).tobytes())


def test_revised_cloud_partial_in_same_segment_can_settle_after_failed_alignment(monkeypatch):
    async def scenario():
        stream = QueuedCloudStream(
            [
                RecognitionUpdate("火星飞船发射", segment_id=0),
                None,
                RecognitionUpdate("大家好", segment_id=0),
                None,
            ]
        )
        runtime = await listening(stream)
        await frame(runtime, monkeypatch, 1000)
        await frame(runtime, monkeypatch, 1250)
        runtime.commit_transcript("火星飞船发射", 1900)
        assert runtime.session.state.value == "LISTENING"
        assert not runtime.session.reliable
        assert runtime.session.reading_position is None
        feedback = runtime.session.feedback_id
        await frame(runtime, monkeypatch, 2100)
        await frame(runtime, monkeypatch, 2350)
        await runtime.tick(3099)
        assert runtime.session.reading_position is None
        await runtime.tick(3100)
        assert runtime.session.state.value == "PROMPTING"
        assert runtime.session.reading_position == 2
        assert runtime.session.aligned_transcript == "大家好"
        assert runtime.session.feedback_id == feedback
        runtime.cancel_prompt()
        runtime.release_stream()

    asyncio.run(scenario())


def test_identical_partial_is_not_resettled_after_vad_clears_transient_text(monkeypatch):
    async def scenario():
        update = RecognitionUpdate("火星飞船发射", segment_id=0)
        stream = QueuedCloudStream([update, None, update, None], [False, False, True, False])
        runtime = await listening(stream)
        commits = []
        original = runtime.commit_transcript

        def commit(text, timestamp):
            commits.append(text)
            original(text, timestamp)

        monkeypatch.setattr(runtime, "commit_transcript", commit)
        await frame(runtime, monkeypatch, 1000)
        await frame(runtime, monkeypatch, 1250)
        runtime.commit_transcript("火星飞船发射", 1900)
        await runtime.tick(1950)
        await frame(runtime, monkeypatch, 2100)
        await frame(runtime, monkeypatch, 2350)
        await runtime.tick(3100)
        assert commits == ["火星飞船发射"]
        assert runtime.session.reading_position is None
        assert runtime.session.state.value == "PROMPTING"
        assert runtime.session.transition_trigger == "recognition_wait_timeout"
        assert runtime.session.prompt.start == 0
        assert runtime.session.aligned_transcript == ""
        assert not runtime.session.reliable
        runtime.release_stream()

    asyncio.run(scenario())


def test_revised_but_unmatched_partial_preserves_reliable_cursor_and_single_warning(monkeypatch):
    async def scenario():
        stream = QueuedCloudStream(
            [
                RecognitionUpdate("火星飞船发射", segment_id=1),
                None,
                RecognitionUpdate("量子机械构造", segment_id=1),
                None,
            ]
        )
        runtime = await listening(stream)
        runtime.session.handle("transcript", 0, text="大家好")
        position = runtime.session.reading_position
        await frame(runtime, monkeypatch, 1000)
        await frame(runtime, monkeypatch, 1250)
        runtime.commit_transcript("火星飞船发射", 1900)
        feedback = runtime.session.feedback_id
        await frame(runtime, monkeypatch, 2100)
        await frame(runtime, monkeypatch, 2350)
        await runtime.tick(3100)
        assert runtime.session.reading_position == position == 2
        assert runtime.session.feedback_id == feedback + 1
        assert not runtime.session.reliable
        assert runtime.session.state.value == "PROMPTING"
        assert runtime.session.transition_trigger == "recognition_wait_timeout"
        assert runtime.session.prompt.text == "欢迎测试。"
        assert runtime.session.aligned_transcript == "大家好"
        runtime.release_stream()

    asyncio.run(scenario())


@pytest.mark.parametrize(("duplicate_segment", "duplicate_text"), [(0, "大家好"), (1, "欢迎测试")])
def test_final_segments_commit_normally_but_cannot_replay_after_new_vad_voice(
    monkeypatch, duplicate_segment, duplicate_text
):
    async def scenario():
        stream = QueuedCloudStream(
            [
                RecognitionUpdate("大家好", segment_id=0),
                RecognitionUpdate("大家好", is_final=True, segment_id=0),
                None,
                RecognitionUpdate("欢迎测试", is_final=True, segment_id=1),
                None,
                RecognitionUpdate(duplicate_text, is_final=True, segment_id=duplicate_segment),
            ],
            [False, False, False, False, False, True],
        )
        runtime = await listening(stream)
        commits = []
        original = runtime.commit_transcript

        def commit(text, timestamp):
            commits.append(text)
            original(text, timestamp)

        monkeypatch.setattr(runtime, "commit_transcript", commit)
        await frame(runtime, monkeypatch, 1000)
        assert runtime.session.reading_position is None
        await frame(runtime, monkeypatch, 1100)
        assert runtime.session.reading_position == 2
        await frame(runtime, monkeypatch, 1350)
        await frame(runtime, monkeypatch, 1400)
        position = runtime.session.reading_position
        assert position == 7
        await frame(runtime, monkeypatch, 1650)
        await frame(runtime, monkeypatch, 1700)
        assert runtime.session.reading_position == position
        assert commits == ["大家好", "欢迎测试"]
        runtime.release_stream()

    asyncio.run(scenario())


def test_late_revised_partial_from_old_turn_cannot_restore_location(monkeypatch):
    class DelayedStream(QueuedCloudStream):
        def __init__(self):
            super().__init__()
            self.entered = threading.Event()
            self.release = threading.Event()

        def accept_pcm(self, pcm):
            self.entered.set()
            assert self.release.wait(timeout=2)
            return RecognitionUpdate("大家好", segment_id=0)

    async def scenario():
        stream = DelayedStream()
        runtime = await listening(stream)
        old_generation = runtime.session.generation
        inference = asyncio.create_task(runtime.audio(bytes(640), old_generation))
        assert await asyncio.to_thread(stream.entered.wait, 1)
        await runtime.control({"type": "pause"})
        runtime.speech.stream = QueuedCloudStream()
        await runtime.control({"type": "resume"})
        await runtime.control({"type": "audio_ready"})
        stream.release.set()
        await inference
        assert runtime.session.generation != old_generation
        assert runtime.session.state.value == "LISTENING"
        assert runtime.session.reading_position is None
        assert runtime.session.aligned_transcript == ""
        assert runtime.last_text == ""
        runtime.release_stream()

    asyncio.run(scenario())


def test_new_segment_can_still_represent_an_intentional_local_readback(monkeypatch):
    async def scenario():
        stream = QueuedCloudStream(
            [
                RecognitionUpdate("大家好", is_final=True, segment_id=0),
                None,
                RecognitionUpdate("欢迎测试", is_final=True, segment_id=1),
                None,
                RecognitionUpdate("大家好", is_final=True, segment_id=2),
            ]
        )
        runtime = await listening(stream)
        await frame(runtime, monkeypatch, 1000)
        assert runtime.session.reading_position == 2
        await frame(runtime, monkeypatch, 1250)
        await frame(runtime, monkeypatch, 1300)
        assert runtime.session.reading_position == 7
        await frame(runtime, monkeypatch, 1550)
        await frame(runtime, monkeypatch, 1600)
        assert runtime.session.reading_position == 2
        assert runtime.session.aligned_transcript == "大家好"
        runtime.release_stream()

    asyncio.run(scenario())
