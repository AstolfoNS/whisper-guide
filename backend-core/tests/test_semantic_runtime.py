import asyncio
from types import SimpleNamespace

from test_runtime import VOICE_PCM, FakeSpeech

from app.api.session import SessionRuntime, now_ms
from app.domain.session import SpeechSession
from app.infrastructure.llm import LLMSemanticRefiner, SemanticRefinement


def make_runtime(mode="fallback"):
    speech = FakeSpeech()
    speech.settings = SimpleNamespace(
        llm_align_mode=mode,
        llm_base_url="https://example.test/v1",
        llm_model="free-model",
        llm_timeout_ms=1500,
    )
    return SessionRuntime(SpeechSession("s", "大家好，欢迎来到演讲测试。"), "稿", speech)


def recovery_runtime(mode="fallback"):
    first = "我会坚持自律好好吃饭踏实学习"
    following = "接着继续认真练习保持专注"
    script = "开场。" + "甲" * 120 + first + "。" + following + "。结束。"
    runtime = make_runtime(mode)
    runtime.session = SpeechSession("s", script)
    return runtime, first, following


def test_semantic_result_reenters_local_aligner_and_keeps_raw_text(monkeypatch):
    async def scenario():
        runtime, quote, following = recovery_runtime()
        rough = quote[:-1] + "思"

        async def refine(self, window, transcript, **kwargs):
            assert quote in window
            assert transcript == rough
            return SemanticRefinement(quote, 0.95)

        monkeypatch.setattr(LLMSemanticRefiner, "refine", refine)
        await runtime.control({"type": "start"})
        runtime.last_segment_id = 0
        runtime.commit_transcript(rough, now_ms())
        task = runtime.refinement
        assert task is not None
        await task
        assert runtime.session.raw_transcript == rough
        # A model's correction of the same segment is not independent evidence
        # for a distant jump. A subsequent real segment confirms the position.
        assert not runtime.session.reliable
        runtime.last_segment_id = 1
        runtime.commit_transcript(following, now_ms())
        assert runtime.session.reliable
        assert runtime.session.aligned_transcript == following
        assert runtime.session.reading_position == (
            runtime.session.tracker.text.index(following) + len(following) - 1
        )
        runtime.release_stream()

    asyncio.run(scenario())


def test_semantic_result_without_transcription_evidence_is_rejected(monkeypatch):
    async def scenario():
        runtime, quote, _ = recovery_runtime()

        async def refine(self, *args, **kwargs):
            return SemanticRefinement(quote, 0.99)

        monkeypatch.setattr(LLMSemanticRefiner, "refine", refine)
        await runtime.control({"type": "start"})
        runtime.last_segment_id = 0
        runtime.commit_transcript("完全识别错误没有任何文字依据", now_ms())
        task = runtime.refinement
        assert task is not None
        await task
        assert runtime.session.reading_position is None
        assert not runtime.session.reliable
        assert not runtime.session._recovery_candidates
        runtime.release_stream()

    asyncio.run(scenario())


def test_cancelled_model_return_cannot_overwrite_reposition_or_pause(monkeypatch):
    async def scenario():
        runtime, quote, _ = recovery_runtime()
        entered = asyncio.Event()

        async def refine(self, *args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return SemanticRefinement(quote, 1)

        monkeypatch.setattr(LLMSemanticRefiner, "refine", refine)
        await runtime.control({"type": "start"})
        runtime.last_segment_id = 0
        runtime.commit_transcript(quote[:-1] + "思", now_ms())
        task = runtime.refinement
        assert task is not None
        await entered.wait()
        await runtime.control({"type": "reposition", "position": 5})
        await runtime.control({"type": "pause"})
        await task
        assert runtime.session.reading_position == 5
        assert runtime.session.state.value == "PAUSED"

    asyncio.run(scenario())


def test_partial_only_settles_at_silence_deadline_and_filler_never_mismatches(monkeypatch):
    from test_noise_guard_runtime import ControlledStream

    import app.api.session as runtime_api
    from app.infrastructure.speech import RecognitionUpdate

    async def scenario():
        clock = [100]
        monkeypatch.setattr(runtime_api, "now_ms", lambda: clock[0])
        runtime = make_runtime("off")
        await runtime.control({"type": "start"})
        runtime.stream = ControlledStream()
        await runtime.control({"type": "audio_ready"})
        runtime.stream.updates.append(RecognitionUpdate("大家好", segment_id=0))
        await runtime.audio(VOICE_PCM)
        clock[0] = 350
        await runtime.audio(bytes(8000))
        await runtime.tick(3099)
        assert runtime.session.reading_position is None
        await runtime.tick(3100)
        assert runtime.session.reading_position == 2
        assert runtime.session.prompt.text == "欢迎来到演讲测试。"
        runtime.cancel_prompt()
        clock[0] = 4000
        await runtime.control({"type": "interrupt"})
        runtime.stream = ControlledStream()
        await runtime.control({"type": "audio_ready"})
        runtime.stream.updates.append(RecognitionUpdate("嗯", segment_id=1, is_filler_only=True))
        old_feedback = runtime.session.feedback_id
        await runtime.audio(VOICE_PCM)
        clock[0] = 4250
        await runtime.audio(bytes(8000))
        await runtime.tick(7000)
        assert runtime.session.feedback_id == old_feedback
        assert runtime.session.reading_position == 2
        runtime.cancel_prompt()
        runtime.release_stream()

    asyncio.run(scenario())


def test_quiet_voice_partial_ends_and_does_not_reset_timer_on_identical_hypothesis(monkeypatch):
    import app.api.session as runtime_api
    from app.infrastructure.speech import RecognitionUpdate

    class QuietStream:
        available = True

        def is_speech(self, pcm):
            return False

        def accept_pcm(self, pcm):
            return RecognitionUpdate("大家好", segment_id=0)

        def close(self):
            pass

    async def scenario():
        runtime = make_runtime("off")
        await runtime.control({"type": "start"})
        runtime.stream = QuietStream()
        await runtime.control({"type": "audio_ready"})
        monkeypatch.setattr(runtime_api, "now_ms", lambda: 1000)
        await runtime.audio(VOICE_PCM)
        assert runtime.session.speaking
        assert runtime.last_voice_ms == 1000
        monkeypatch.setattr(runtime_api, "now_ms", lambda: 1250)
        await runtime.audio(bytes(640))
        assert not runtime.session.speaking
        assert runtime.session.silence_since_ms == 1000
        await runtime.tick(3999)
        assert runtime.session.reading_position is None
        await runtime.tick(4000)
        assert runtime.session.reading_position == 2
        runtime.cancel_prompt()

    asyncio.run(scenario())


def test_hybrid_timeout_keeps_local_alignment_and_bounds_request(monkeypatch):
    async def scenario():
        runtime = make_runtime("hybrid")

        async def refine(self, *args, **kwargs):
            return None

        monkeypatch.setattr(LLMSemanticRefiner, "refine", refine)
        await runtime.control({"type": "start"})
        runtime.commit_transcript("欢迎来到演讲测试", now_ms())
        position = runtime.session.reading_position
        task = runtime.refinement
        assert task is not None
        await task
        assert runtime.session.reading_position == position == 11
        assert runtime.session.reliable
        runtime.release_stream()

    asyncio.run(scenario())
