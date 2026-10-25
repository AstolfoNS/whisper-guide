"""Lossless bounded scheduling preserves PCM, endpoints and control priority."""

import asyncio
import threading
from array import array
from types import SimpleNamespace

import pytest

from app.api.session import AudioFrameQueue, SessionRuntime
from app.domain.session import SpeechSession
from app.infrastructure.speech import (
    FallbackSpeechStream,
    RecognitionUpdate,
    SpeechStream,
    SpeechUnavailableError,
)


class Services:
    settings = SimpleNamespace(llm_align_mode="off")

    def __init__(self, stream):
        self.stream = stream

    def status(self):
        return {"stt": "ready", "tts": "ready"}

    def create_stream(self):
        return self.stream

    async def synthesize(self, text):
        return b"in-memory-output"


async def listening(stream):
    runtime = SessionRuntime(
        SpeechSession("s", "First sentence. Second sentence."), "batch", Services(stream)
    )
    await runtime.control({"type": "start"})
    await runtime.control({"type": "audio_ready"})
    return runtime


def packet(index):
    return array("h", [-index - 1, index + 1] * 160).tobytes()


def test_queue_bounds_audio_duration_independently_of_packet_count():
    queue = AudioFrameQueue()
    queue.put_nowait((1, bytes(32000)))
    assert queue.queued_bytes == 32000
    with pytest.raises(asyncio.QueueFull):
        queue.put_nowait((1, bytes(640)))
    assert queue.get_nowait() == (1, bytes(32000))
    assert queue.queued_bytes == 0
    for _index in range(50):
        queue.put_nowait((1, bytes(640)))
    assert queue.qsize() == 50
    assert queue.queued_bytes == 32000


def test_batch_preserves_original_packet_order_and_generation_boundaries():
    queue = AudioFrameQueue()
    frames = [packet(index) for index in range(9)]
    for pcm in frames[:7]:
        queue.put_nowait((1, pcm))
    queue.put_nowait((2, frames[7]))
    queue.put_nowait((2, frames[8]))
    assert queue.take_batch(queue.get_nowait(), enabled=True) == frames[:5]
    assert queue.take_batch(queue.get_nowait(), enabled=True) == frames[5:7]
    assert queue.take_batch(queue.get_nowait(), enabled=True) == frames[7:]
    assert queue.empty()
    assert queue.queued_bytes == 0


def test_generic_stream_stays_one_packet_per_worker_call_and_batch_never_exceeds_100ms():
    queue = AudioFrameQueue()
    for _ in range(6):
        queue.put_nowait((1, bytes(640)))
    assert queue.take_batch(queue.get_nowait(), enabled=False) == [bytes(640)]
    assert queue.qsize() == 5
    assert sum(map(len, queue.take_batch(queue.get_nowait(), enabled=True))) == 3200
    queue.put_nowait((1, bytes(1920)))
    queue.put_nowait((1, bytes(1920)))
    assert queue.take_batch(queue.get_nowait(), enabled=True) == [bytes(1920)]
    assert queue.queued_bytes == 1920


def test_native_facade_batch_keeps_every_partial_final_and_pcm_packet_once():
    frames = [packet(index) for index in range(5)]
    seen = []
    expected = [
        RecognitionUpdate(str(index), is_final=index in {1, 3}, segment_id=index // 2)
        for index in range(5)
    ]

    def recognizer(pcm):
        seen.append(pcm)
        return expected[len(seen) - 1]

    stream = SpeechStream(recognizer=recognizer)
    assert stream.accept_pcm_batch(frames) == expected
    assert seen == frames
    stream.close()


def test_batch_fallback_never_replays_pcm_or_reuses_cloud_segment_ids():
    frames = [packet(index) for index in range(5)]
    local_seen, remote_seen = [], []

    def local(pcm):
        local_seen.append(pcm)
        return RecognitionUpdate("local", segment_id=len(local_seen), is_final=True)

    def remote(pcm):
        remote_seen.append(pcm)
        if len(remote_seen) == 2:
            raise SpeechUnavailableError("controlled timeout")
        return RecognitionUpdate("cloud", segment_id=7, is_final=True)

    stream = FallbackSpeechStream(SpeechStream(recognizer=remote), SpeechStream(recognizer=local))
    updates = stream.accept_pcm_batch(frames)
    assert local_seen == frames
    assert remote_seen == frames[:2]
    assert [item.segment_id for item in updates] == [7, 8, 9, 10, 11]
    stream.close()


def test_runtime_batch_uses_one_thread_roundtrip_and_vad_keeps_twenty_ms_packets(monkeypatch):
    async def scenario():
        frames = [packet(index) for index in range(5)]
        captured, vad_frames = [], []
        stream = SpeechStream(recognizer=lambda pcm: captured.append(pcm))

        class Vad:
            def is_speech(self, pcm, sample_rate):
                assert sample_rate == 16000 and len(pcm) == 640
                vad_frames.append(pcm)
                return False

        stream._vad = Vad()
        runtime = await listening(stream)
        calls = []
        original = asyncio.to_thread

        async def counted(call, *args, **kwargs):
            calls.append(call)
            return await original(call, *args, **kwargs)

        monkeypatch.setattr(asyncio, "to_thread", counted)
        await runtime.audio_batch(frames)
        assert len(calls) == 1
        assert captured == frames
        assert vad_frames == frames
        assert runtime.audio_evidence.audio_ms == 100
        assert runtime.audio_batch_frames == 5
        assert runtime.audio_processed_ms is not None
        assert runtime.audio_inference_ms >= 0
        runtime.release_stream()

    asyncio.run(scenario())


def test_generic_adapter_explicit_batch_does_not_drop_following_packets():
    async def scenario():
        frames = [packet(index) for index in range(3)]
        captured = []

        class Generic:
            available = True

            def is_speech(self, pcm):
                return False

            def accept_pcm(self, pcm):
                captured.append(pcm)

            def close(self):
                pass

        runtime = await listening(Generic())
        await runtime.audio_batch(frames)
        assert captured == frames
        runtime.release_stream()

    asyncio.run(scenario())


def test_pause_during_batch_discards_late_results_without_waiting_for_inference():
    async def scenario():
        entered, release = threading.Event(), threading.Event()
        seen = []

        def recognizer(pcm):
            seen.append(pcm)
            entered.set()
            assert release.wait(2)
            return RecognitionUpdate("First sentence", is_final=True)

        stream = SpeechStream(recognizer=recognizer)
        runtime = await listening(stream)
        task = asyncio.create_task(runtime.audio_batch([packet(index) for index in range(5)]))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            async with asyncio.timeout(0.1):
                await runtime.control({"type": "pause"})
            release.set()
            await task
            assert runtime.session.state.value == "PAUSED"
            assert runtime.session.reading_position is None
            assert len(seen) == 1
        finally:
            release.set()
            runtime.release_stream()

    asyncio.run(scenario())


def test_stable_partial_preview_runs_alignment_once_per_hypothesis(monkeypatch):
    async def scenario():
        stream = SpeechStream(recognizer=lambda pcm: RecognitionUpdate("First sentence"))
        runtime = await listening(stream)
        called = []
        original = runtime.session.preview_transcript

        def preview(text):
            called.append(text)
            original(text)

        monkeypatch.setattr(runtime.session, "preview_transcript", preview)
        for index in range(10):
            await runtime.audio(packet(index + 100))
            await runtime.tick(0)
        assert called == ["First sentence"]
        runtime.release_stream()

    asyncio.run(scenario())


def test_silent_early_batch_hypothesis_cannot_borrow_future_packet_voice(monkeypatch):
    async def scenario():
        outcomes = iter([RecognitionUpdate("First sentence", segment_id=0), None])
        stream = SpeechStream(recognizer=lambda pcm: next(outcomes))
        runtime = await listening(stream)
        observed = []
        original = runtime._accept_update

        async def accept(update, received, generation, current_stream):
            await original(update, received, generation, current_stream)
            if update is not None:
                observed.append(
                    (runtime.snapshot()["recognition_filtered"], runtime.pending_hypothesis)
                )

        monkeypatch.setattr(runtime, "_accept_update", accept)
        await runtime.audio_batch([bytes(2560), array("h", [-100, 100] * 640).tobytes()])
        assert observed == [(True, None)]
        assert runtime.pending_hypothesis is None
        assert runtime.session.reading_position is None
        assert runtime.audio_evidence.audio_ms == 160
        runtime.release_stream()

    asyncio.run(scenario())


def test_timed_batch_second_silent_segment_cannot_use_first_segments_voice():
    async def scenario():
        outcomes = iter(
            [
                RecognitionUpdate("First sentence", True, 0, False, 0, 80),
                RecognitionUpdate("Second sentence", True, 1, False, 80, 160),
            ]
        )
        stream = SpeechStream(recognizer=lambda pcm: next(outcomes))
        runtime = await listening(stream)
        await runtime.audio_batch([array("h", [-100, 100] * 640).tobytes(), bytes(2560)])
        assert runtime.snapshot()["recognition_filtered"]
        assert runtime.session.aligned_transcript == "First sentence"
        assert runtime.session.reliable
        runtime.release_stream()

    asyncio.run(scenario())


def test_bounded_batch_survives_fixed_dispatch_overhead_without_dropping_pcm():
    # Virtual 20ms producer clock plus 60ms per-worker dispatch. A single-frame
    # consumer falls behind; a <=100ms batch repays scheduling overhead.
    def simulate(batch):
        queue = AudioFrameQueue()
        produced, consumed = [], []
        next_input = clock = 0
        peak = 0
        overflow = False
        while len(consumed) < 250:
            if queue.empty() and len(produced) < 250:
                clock = max(clock, next_input)
            while len(produced) < 250 and next_input <= clock:
                pcm = packet(len(produced))
                try:
                    queue.put_nowait((1, pcm))
                except asyncio.QueueFull:
                    overflow = True
                    break
                produced.append(pcm)
                next_input += 20
                peak = max(peak, queue.qsize())
            if overflow:
                break
            if queue.empty():
                continue
            consumed.extend(queue.take_batch(queue.get_nowait(), enabled=batch))
            clock += 60
        return produced, consumed, peak, overflow

    _, _, old_peak, old_overflow = simulate(False)
    produced, consumed, peak, overflow = simulate(True)
    assert old_overflow and old_peak == 50
    assert not overflow
    assert consumed == produced == [packet(index) for index in range(250)]
    assert peak <= 3


def test_worker_batch_eligibility_keeps_cloud_packetwise_and_enables_after_fallback():
    remote = SpeechStream(recognizer=lambda pcm: None, provider_name="cloud")
    local = SpeechStream(recognizer=lambda pcm: None)
    stream = FallbackSpeechStream(remote, local)
    queue = AudioFrameQueue()
    for _ in range(6):
        queue.put_nowait((1, bytes(640)))
    assert not SessionRuntime._can_batch_audio(stream)
    assert not SessionRuntime._can_batch_audio(remote)
    assert (
        len(queue.take_batch(queue.get_nowait(), enabled=SessionRuntime._can_batch_audio(stream)))
        == 1
    )
    stream._activate_local(SpeechUnavailableError("controlled timeout"))
    assert SessionRuntime._can_batch_audio(stream)
    assert (
        len(queue.take_batch(queue.get_nowait(), enabled=SessionRuntime._can_batch_audio(stream)))
        == 5
    )
    assert queue.empty()
    assert not SessionRuntime._can_batch_audio(SimpleNamespace(effective_provider="local"))
    stream.close()


def test_explicit_cloud_batch_notifies_before_accept_and_never_reopens_completed_deadline():
    async def scenario():
        events = []

        class RemoteRecognizer:
            def __init__(self):
                self.pending = False
                self.frames = []

            def notify_voice(self):
                events.append(("notify", len(self.frames)))
                self.pending = True

            def __call__(self, pcm):
                self.frames.append(pcm)
                events.append(("accept", len(self.frames)))
                # Simulate the receive thread settling the result deadline.
                self.pending = False
                return None

        class Vad:
            def is_speech(self, pcm, sample_rate):
                return True

        recognizer = RemoteRecognizer()
        stream = SpeechStream(recognizer=recognizer, provider_name="cloud", vad=Vad())
        runtime = await listening(stream)
        frames = [packet(index + 100) for index in range(5)]
        await runtime.audio_batch(frames)
        assert recognizer.frames == frames
        assert events.index(("notify", 2)) < events.index(("accept", 3))
        assert events[-1] == ("accept", 5)
        assert not recognizer.pending
        assert runtime.audio_batch_frames == 1
        runtime.release_stream()

    asyncio.run(scenario())


def test_fallback_local_runtime_keeps_single_dispatch_batch_benefit(monkeypatch):
    async def scenario():
        local_frames = []

        def fail(_pcm):
            raise SpeechUnavailableError("controlled timeout")

        stream = FallbackSpeechStream(
            SpeechStream(recognizer=fail, provider_name="cloud"),
            SpeechStream(recognizer=lambda pcm: local_frames.append(pcm)),
        )
        runtime = await listening(stream)
        await runtime.audio(packet(100))
        assert stream.fallback_active
        calls = []
        original = asyncio.to_thread

        async def counted(call, *args, **kwargs):
            calls.append(call)
            return await original(call, *args, **kwargs)

        monkeypatch.setattr(asyncio, "to_thread", counted)
        frames = [packet(index + 110) for index in range(5)]
        await runtime.audio_batch(frames)
        assert len(calls) == 1
        assert local_frames == [packet(100), *frames]
        assert runtime.audio_batch_frames == 5
        runtime.release_stream()

    asyncio.run(scenario())
