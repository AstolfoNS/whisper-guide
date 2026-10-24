"""Settled recovery evidence survives partials and rejects stale model responses."""

import asyncio

from test_auto_recovery import distant_script
from test_noise_guard_runtime import ControlledStream, ac_pcm
from test_runtime import VOICE_PCM
from test_semantic_runtime import make_runtime, recovery_runtime

import app.api.session as runtime_api
from app.domain.session import SpeechSession
from app.infrastructure.llm import LLMSemanticRefiner, SemanticRefinement
from app.infrastructure.speech import RecognitionUpdate


def test_slow_recovery_survives_partial_and_catches_up_final(monkeypatch):
    async def scenario():
        clock = [100]
        monkeypatch.setattr(runtime_api, "now_ms", lambda: clock[0])
        runtime, quote, following = recovery_runtime()
        entered, finish = asyncio.Event(), asyncio.Event()

        async def refine(self, *args, **kwargs):
            entered.set()
            await finish.wait()
            return SemanticRefinement(quote, 0.98)

        monkeypatch.setattr(LLMSemanticRefiner, "refine", refine)
        await runtime.control({"type": "start"})
        runtime.stream = ControlledStream()
        runtime.stream.vad = True
        await runtime.control({"type": "audio_ready"})
        runtime.last_segment_id = 0
        runtime.commit_transcript(quote[:-1] + "思", clock[0])
        original = runtime.refinement
        assert original is not None
        await entered.wait()
        revision = runtime.committed_revision
        clock[0] = 200
        runtime.stream.updates.append(RecognitionUpdate(following, segment_id=1))
        await runtime.audio(VOICE_PCM)
        assert runtime.committed_revision == revision
        assert runtime.pending_hypothesis == (1, following)
        assert runtime.refinement is original
        clock[0] = 300
        runtime.stream.updates.append(RecognitionUpdate(following, segment_id=1, is_final=True))
        await runtime.audio(VOICE_PCM)
        assert runtime.committed_revision == revision + 1
        assert runtime.refinement is original
        finish.set()
        await original
        assert runtime.session.reliable
        assert runtime.session.reading_position == (
            runtime.session.tracker.text.index(following) + len(following) - 1
        )
        assert runtime.session.raw_transcript == following
        runtime.release_stream()

    asyncio.run(scenario())


def test_same_segment_revision_rejects_cancel_ignoring_model(monkeypatch):
    async def scenario():
        runtime, quote, _ = recovery_runtime()
        entered = asyncio.Event()
        calls = []

        async def refine(self, window, rough, **kwargs):
            calls.append(rough)
            if len(calls) > 1:
                return None
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return SemanticRefinement(quote, 0.99)

        monkeypatch.setattr(LLMSemanticRefiner, "refine", refine)
        await runtime.control({"type": "start"})
        runtime.stream = ControlledStream()
        runtime.stream.vad = True
        await runtime.control({"type": "audio_ready"})
        runtime.last_segment_id = 0
        runtime.commit_transcript(quote[:-1] + "思", 1)
        original = runtime.refinement
        assert original is not None
        await entered.wait()
        revision = "这段识别已经修订为无关内容"
        runtime.stream.updates.append(RecognitionUpdate(revision, segment_id=0, is_final=True))
        await runtime.audio(VOICE_PCM)
        await original
        assert runtime.session.reading_position is None
        assert not runtime.session.reliable
        assert runtime.session.raw_transcript == revision
        runtime.release_stream()

    asyncio.run(scenario())


def test_history_bounds_catchup_and_replaces_old_request(monkeypatch):
    async def scenario():
        runtime, quote, _ = recovery_runtime()
        entered = asyncio.Event()
        requests = []

        async def refine(self, window, rough, **kwargs):
            requests.append(rough)
            if len(requests) > 1:
                return None
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return SemanticRefinement(quote, 0.99)

        monkeypatch.setattr(LLMSemanticRefiner, "refine", refine)
        await runtime.control({"type": "start"})
        runtime.last_segment_id = 0
        runtime.commit_transcript(quote[:-1] + "思", 1)
        original = runtime.refinement
        assert original is not None
        await entered.wait()
        for index in range(1, 6):
            runtime.last_segment_id = index
            runtime.commit_transcript("这段背景文字无法匹配讲稿" + str(index), index + 1)
            if index <= 4:
                assert runtime.refinement is original
        replacement = runtime.refinement
        assert replacement is not None and replacement is not original
        await original
        await replacement
        assert requests == [quote[:-1] + "思", "这段背景文字无法匹配讲稿5"]
        assert runtime.session.reading_position is None
        assert len(runtime.committed_history) == 5
        runtime.release_stream()

    asyncio.run(scenario())


def test_stream_release_rejects_late_model_in_same_generation(monkeypatch):
    async def scenario():
        runtime, quote, _ = recovery_runtime()
        entered = asyncio.Event()

        async def refine(self, *args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return SemanticRefinement(quote, 0.99)

        monkeypatch.setattr(LLMSemanticRefiner, "refine", refine)
        await runtime.control({"type": "start"})
        runtime.last_segment_id = 0
        runtime.commit_transcript(quote[:-1] + "思", 1)
        original = runtime.refinement
        assert original is not None
        await entered.wait()
        generation = runtime.session.generation
        runtime.release_stream()
        await original
        assert runtime.session.generation == generation
        assert runtime.session.reading_position is None
        assert not runtime.committed_history

    asyncio.run(scenario())


def test_stable_partial_preview_is_retracted_by_empty_final(monkeypatch):
    async def scenario():
        clock = [100]
        monkeypatch.setattr(runtime_api, "now_ms", lambda: clock[0])
        runtime = make_runtime("off")
        await runtime.control({"type": "start"})
        runtime.stream = ControlledStream()
        runtime.stream.vad = True
        await runtime.control({"type": "audio_ready"})
        for timestamp in (100, 200):
            clock[0] = timestamp
            runtime.stream.updates.append(RecognitionUpdate("欢迎来到演讲测试", segment_id=0))
            await runtime.audio(VOICE_PCM)
        assert runtime.session.provisional_position == 11
        assert runtime.session.reading_position is None
        assert runtime.session.prompt_position == 0
        assert not runtime.session.reliable
        runtime.stream.updates.append(RecognitionUpdate("", segment_id=0, is_final=True))
        await runtime.audio(VOICE_PCM)
        assert runtime.session.provisional_position is None
        assert runtime.pending_hypothesis is None
        assert runtime.session.reading_position is None
        runtime.release_stream()

    asyncio.run(scenario())


def test_partial_age_preview_does_not_commit_before_final(monkeypatch):
    async def scenario():
        clock = [100]
        monkeypatch.setattr(runtime_api, "now_ms", lambda: clock[0])
        runtime = make_runtime("off")
        await runtime.control({"type": "start"})
        runtime.stream = ControlledStream()
        runtime.stream.vad = True
        await runtime.control({"type": "audio_ready"})
        runtime.stream.updates.append(RecognitionUpdate("欢迎来到演讲测试", segment_id=0))
        await runtime.audio(VOICE_PCM)
        assert runtime.session.provisional_position is None
        clock[0] = 400
        await runtime.tick(clock[0])
        assert runtime.session.provisional_position == 11
        assert runtime.session.reading_position is None
        assert runtime.session.prompt_position == 0
        runtime.stream.updates.append(
            RecognitionUpdate("欢迎来到演讲测试", segment_id=0, is_final=True)
        )
        await runtime.audio(VOICE_PCM)
        assert runtime.session.provisional_position is None
        assert runtime.session.reading_position == 11
        assert runtime.session.reliable
        runtime.release_stream()

    asyncio.run(scenario())


def test_hybrid_short_noise_never_requests_model_or_clears_mismatch(monkeypatch):
    async def scenario():
        runtime = make_runtime("hybrid")
        requests = []

        async def refine(self, window, rough, **kwargs):
            requests.append(rough)
            return SemanticRefinement("欢迎来到演讲测试", 0.99)

        monkeypatch.setattr(LLMSemanticRefiner, "refine", refine)
        await runtime.control({"type": "start"})
        runtime.session.handle("transcript", 1, text="这是完全无关的背景杂音")
        assert not runtime.session.reliable
        runtime.last_segment_id = 1
        runtime.commit_transcript("来", 2)
        assert runtime.refinement is None
        assert not requests
        assert runtime.session.reading_position is None
        assert not runtime.session.reliable
        runtime.release_stream()

    asyncio.run(scenario())


def test_stationary_noise_withdraws_early_partial_before_silence_settlement(monkeypatch):
    async def scenario():
        clock = [100]
        monkeypatch.setattr(runtime_api, "now_ms", lambda: clock[0])
        runtime = make_runtime("off")
        await runtime.control({"type": "start"})
        runtime.stream = ControlledStream()
        await runtime.control({"type": "audio_ready"})
        runtime.stream.updates.append(RecognitionUpdate("欢迎来到演讲测试", segment_id=0))
        await runtime.audio(VOICE_PCM)
        assert runtime.pending_hypothesis is not None
        assert runtime.audio_evidence.is_supported(0)
        for index in range(7):
            clock[0] = 180 + index * 80
            await runtime.audio(VOICE_PCM)
        assert not runtime.audio_evidence.is_supported(0)
        assert runtime.pending_hypothesis is None
        assert runtime.session.provisional_position is None
        assert runtime.snapshot()["recognition_filtered"]
        clock[0] = 1000
        await runtime.audio(bytes(8000))
        await runtime.tick(5000)
        assert runtime.session.reading_position is None
        assert runtime.session.prompt is None
        assert not runtime.session.reliable
        runtime.release_stream()

    asyncio.run(scenario())


def test_same_generation_new_stream_cannot_confirm_old_tentative_jump():
    async def scenario():
        runtime, quote, following = recovery_runtime("off")
        await runtime.control({"type": "start"})
        runtime.last_segment_id = 0
        runtime.commit_transcript(quote, 1)
        assert not runtime.session.reliable
        assert runtime.session._recovery_candidates
        generation = runtime.session.generation
        runtime.release_stream()
        assert runtime.session.generation == generation
        assert not runtime.session._recovery_candidates
        runtime.last_segment_id = 1
        runtime.commit_transcript(following, 2)
        assert not runtime.session.reliable
        assert runtime.session.reading_position is None
        runtime.release_stream()

    asyncio.run(scenario())


def test_retracted_catchup_segment_cannot_be_replayed_by_older_model(monkeypatch):
    async def scenario():
        runtime, quote, following = recovery_runtime()
        entered = asyncio.Event()

        async def refine(self, *args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return SemanticRefinement(quote, 0.99)

        monkeypatch.setattr(LLMSemanticRefiner, "refine", refine)
        await runtime.control({"type": "start"})
        runtime.stream = ControlledStream()
        runtime.stream.vad = True
        await runtime.control({"type": "audio_ready"})
        runtime.last_segment_id = 0
        runtime.commit_transcript(quote[:-1] + "思", 1)
        original = runtime.refinement
        assert original is not None
        await entered.wait()
        runtime.last_segment_id = 1
        runtime.commit_transcript(following, 2)
        assert runtime.refinement is original
        runtime.stream.updates.append(RecognitionUpdate("", segment_id=1, is_final=True))
        await runtime.audio(VOICE_PCM)
        await original
        assert not runtime.session.reliable
        assert runtime.session.reading_position is None
        assert not runtime.session._recovery_candidates
        assert all(item.evidence_id[1] != 1 for item in runtime.committed_history)
        runtime.release_stream()

    asyncio.run(scenario())


def test_withdrawn_stationary_noise_finals_invalidate_confirmed_jump(monkeypatch):
    async def scenario():
        clock = [80]
        monkeypatch.setattr(runtime_api, "now_ms", lambda: clock[0])
        script, first, second = distant_script()
        runtime = make_runtime("off")
        runtime.session = SpeechSession("s", script, silence_timeout_ms=1000)
        await runtime.control({"type": "start"})
        runtime.stream = ControlledStream()
        await runtime.control({"type": "audio_ready"})
        for index, text in enumerate((first, second)):
            clock[0] = (index + 1) * 80
            runtime.stream.updates.append(RecognitionUpdate(text, segment_id=index, is_final=True))
            await runtime.audio(ac_pcm(80, 300))
        assert runtime.session.reliable
        position = script.index(second) + len(second) - 1
        assert runtime.session.reading_position == position
        for index in range(6):
            clock[0] = 240 + index * 80
            await runtime.audio(ac_pcm(80, 300))
        assert not runtime.audio_evidence.is_supported(0)
        assert not runtime.audio_evidence.is_supported(1)
        assert not runtime.committed_history
        assert runtime.session.reading_position == position
        assert not runtime.session.reliable
        assert not runtime.session._last_reliable
        assert runtime.session.snapshot()["alignment_status"] == "mismatch"
        clock[0] = 1000
        await runtime.audio(bytes(8000))
        await runtime.tick(3000)
        assert runtime.session.state.value == "LISTENING"
        assert runtime.session.prompt is None
        runtime.release_stream()

    asyncio.run(scenario())


def test_old_model_cannot_settle_new_vad_activity_without_text(monkeypatch):
    async def scenario():
        clock = [100]
        monkeypatch.setattr(runtime_api, "now_ms", lambda: clock[0])
        quote = "欢迎来到演讲测试"
        following = "接着继续认真练习保持专注"
        runtime = make_runtime("hybrid")
        runtime.session = SpeechSession("s", quote + "。" + following + "。结束。")
        entered, finish = asyncio.Event(), asyncio.Event()

        async def refine(self, *args, **kwargs):
            entered.set()
            await finish.wait()
            return SemanticRefinement(quote, 0.99)

        monkeypatch.setattr(LLMSemanticRefiner, "refine", refine)
        await runtime.control({"type": "start"})
        runtime.stream = ControlledStream()
        runtime.stream.vad = True
        await runtime.control({"type": "audio_ready"})
        runtime.last_segment_id = 0
        runtime.commit_transcript(quote, clock[0])
        original = runtime.refinement
        assert original is not None
        await entered.wait()
        clock[0] = 200
        await runtime.audio(VOICE_PCM)
        assert runtime.session.speaking
        finish.set()
        await original
        assert runtime.session.reading_position == len(quote) - 1
        assert runtime.session._last_reliable
        assert not runtime.session.reliable
        assert runtime.session.snapshot()["alignment_status"] == "pending"
        runtime.stream.vad = False
        clock[0] = 500
        await runtime.audio(bytes(8000))
        await runtime.tick(3500)
        assert not runtime.session.reliable
        assert runtime.session.prompt is None
        assert runtime.session.state.value == "LISTENING"
        runtime.release_stream()

    asyncio.run(scenario())


def test_old_model_keeps_new_partial_pending_until_actual_settlement(monkeypatch):
    async def scenario():
        clock = [100]
        monkeypatch.setattr(runtime_api, "now_ms", lambda: clock[0])
        quote = "欢迎来到演讲测试"
        following = "接着继续认真练习保持专注"
        runtime = make_runtime("hybrid")
        runtime.session = SpeechSession("s", quote + "。" + following + "。结束。")
        entered, finish = asyncio.Event(), asyncio.Event()

        async def refine(self, window, rough, **kwargs):
            if rough != quote:
                return None
            entered.set()
            await finish.wait()
            return SemanticRefinement(quote, 0.99)

        monkeypatch.setattr(LLMSemanticRefiner, "refine", refine)
        await runtime.control({"type": "start"})
        runtime.stream = ControlledStream()
        runtime.stream.vad = True
        await runtime.control({"type": "audio_ready"})
        runtime.last_segment_id = 0
        runtime.commit_transcript(quote, clock[0])
        original = runtime.refinement
        assert original is not None
        await entered.wait()
        clock[0] = 200
        runtime.stream.updates.append(RecognitionUpdate(following, segment_id=1))
        await runtime.audio(VOICE_PCM)
        assert runtime.refinement is original
        finish.set()
        await original
        assert runtime.pending_hypothesis == (1, following)
        assert not runtime.session.reliable
        assert runtime.session.reading_position == len(quote) - 1
        runtime.stream.vad = False
        clock[0] = 500
        await runtime.audio(bytes(8000))
        await runtime.tick(3199)
        assert runtime.session.prompt is None
        assert not runtime.session.reliable
        await runtime.tick(3200)
        assert runtime.session.reading_position == (
            runtime.session.tracker.text.index(following) + len(following) - 1
        )
        assert runtime.session.state.value == "PROMPTING"
        assert runtime.session.prompt.text == "结束。"
        runtime.cancel_prompt()
        runtime.release_stream()

    asyncio.run(scenario())
