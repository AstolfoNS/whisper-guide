"""Progress-based assistance remains independent of detector noise and ASR churn."""

import asyncio
from array import array
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.api.session as runtime_api
from app.api.session import SessionRuntime
from app.config import Settings
from app.domain.session import SessionOptions, SpeechSession
from app.infrastructure.speech import SpeechStream
from app.main import create_app

SCRIPT = "我将继续立足岗位，用心传递。最后谢谢大家。"


def session(**options):
    current = SpeechSession("script", SCRIPT, prompt_mode="progress", **options)
    current.handle("start", 0)
    return current


@pytest.mark.parametrize("value", [0, 1999, 10001, 4000.0, "4000", True, None])
def test_progress_options_reject_noninteger_or_out_of_range(value):
    with pytest.raises(ValidationError):
        SessionOptions(progress_timeout_ms=value)


def test_progress_options_are_opt_in_and_boolean_is_strict():
    assert SessionOptions().prompt_mode == "silence"
    assert SessionOptions().continuous_prompt is False
    with pytest.raises(ValidationError):
        SessionOptions(prompt_mode="unknown")
    with pytest.raises(ValidationError):
        SessionOptions(continuous_prompt="true")


def test_noise_activity_cannot_postpone_progress_timeout_and_does_not_fake_reading():
    current = session()
    for timestamp in (100, 1000, 3999):
        current.handle("speech_start", timestamp)
        assert current.handle("tick", timestamp)["state"] == "LISTENING"
    before = current.reading_position
    result = current.handle("tick", 4000)
    assert result["state"] == "PROMPTING"
    assert result["transition_trigger"] == "progress_timeout"
    assert result["prompt"]["start"] == 0
    assert result["prompt_start_reason"] == "saved_position"
    assert current.reading_position == before
    assert not result["reliable"]


def test_progress_prompt_starts_at_next_complete_unit_and_keeps_original_read_cursor():
    current = session()
    current.handle("transcript", 100, text="我将继续立足", evidence_id=0)
    current.handle("speech_start", 200)
    result = current.handle("tick", 4100)
    assert result["prompt"]["text"] == "岗位，"
    assert result["prompt"]["start"] == SCRIPT.index("岗")
    assert result["prompt_start_reason"] == "next_unit"
    assert result["reading_position"] == SCRIPT.index("足")
    assert not result["reliable"]  # Unsettled newer activity remains unsettled.


def test_next_unit_never_splits_an_english_word_or_decimal():
    current = SpeechSession("s", "请使用OpenAI和3.14版本。", prompt_mode="progress")
    current.handle("start", 0)
    current.handle("transcript", 1, text="请使用")
    current.handle("speech_start", 2)
    result = current.handle("tick", 4001)
    assert result["prompt"]["text"] == "OpenAI和3.14版本。"


def test_unrelated_transcripts_and_repeated_confirmations_cannot_extend_budget():
    current = session()
    current.handle("transcript", 100, text="我将继续", evidence_id=0)
    assert current.progress_since_ms == 100
    current.handle("transcript", 1000, text="我将继续", evidence_id=0)
    assert current.progress_since_ms == 100
    current.handle("transcript", 3000, text="完全无关的背景声音和内容", evidence_id=1)
    assert current.progress_since_ms == 100
    result = current.handle("tick", 4100)
    assert result["state"] == "PROMPTING"
    assert result["prompt_start_reason"] == "saved_position"
    assert result["alignment_status"] == "mismatch"


def test_same_segment_revisions_cannot_oscillate_progress_clock_backwards():
    current = session()
    current.handle("transcript", 100, text="我将继续立足", evidence_id=0)
    current.handle("transcript", 1000, text="我将继续", evidence_id=0)
    current.handle("transcript", 2000, text="我将继续立足", evidence_id=0)
    assert current.progress_since_ms == 100


def test_one_prompt_then_wait_for_real_progress_instead_of_reading_entire_script():
    current = session()
    first = current.handle("tick", 4000)["prompt"]
    result = current.handle("playback_finished", 4500, prompt_id=first["id"])
    assert result["prompt_position"] == SCRIPT.index("用")
    assert result["reading_position"] is None
    assert not result["progress_prompt_armed"]
    assert current.handle("tick", 50000)["state"] == "LISTENING"
    current.handle("transcript", 51000, text="用心传递", evidence_id=1)
    assert current.progress_prompt_armed
    assert current.handle("tick", 55000)["state"] == "PROMPTING"


def test_continuous_mode_waits_a_full_new_period_after_playback():
    current = session(continuous_prompt=True, silence_timeout_ms=10000)
    first = current.handle("tick", 4000)["prompt"]
    current.handle("playback_finished", 4500, prompt_id=first["id"])
    assert current.handle("tick", 8499)["state"] == "LISTENING"
    assert current.handle("tick", 8500)["prompt"]["start"] == SCRIPT.index("用")


def test_postpone_and_manual_position_rearm_without_claiming_new_reading():
    current = session()
    current.handle("speech_start", 100)
    current.handle("postpone_prompt", 3999)
    assert current.progress_since_ms == 3999
    assert current.reading_position is None
    assert current.handle("tick", 7998)["state"] == "LISTENING"
    assert current.handle("tick", 7999)["state"] == "PROMPTING"
    current.handle("interrupt", 8000)
    assert not current.progress_prompt_armed
    current.handle("reposition", 8001, position=SCRIPT.index("岗"))
    assert current.progress_prompt_armed
    result = current.handle("tick", 12001)
    assert result["prompt"]["start"] == SCRIPT.index("岗")
    assert result["prompt_start_reason"] == "manual_position"


@pytest.mark.parametrize("control", ["pause", "end", "service_fault"])
def test_priority_controls_cancel_progress_deadline(control):
    current = session()
    result = current.handle_batch([("tick", {}), (control, {})], 4000)
    assert result["state"] != "PROMPTING"
    assert result["prompt"] is None


def test_real_progress_at_deadline_cancels_old_timeout():
    current = session()
    result = current.handle_batch(
        [("tick", {}), ("transcript", {"text": "我将继续", "evidence_id": 0})], 4000
    )
    assert result["state"] == "LISTENING"
    assert current.progress_since_ms == 4000


def test_retracted_alignment_never_claims_the_next_unit():
    current = session()
    current.handle("transcript", 100, text="我将继续", evidence_id=0)
    current.retract_evidence(0)
    result = current.handle("tick", 4100)
    assert result["prompt_start_reason"] == "saved_position"
    assert not result["reliable"]


class ReadySpeech:
    def status(self, **kwargs):
        return {"stt": "ready", "vad": "ready", "tts": "ready"}

    def create_stream(self, **kwargs):
        return SpeechStream(recognizer=lambda pcm: None)

    async def synthesize(self, text):
        return b"synthetic-audio"


def make_runtime(timestamp=0):
    current = SessionRuntime(session(silence_timeout_ms=10000), "test", ReadySpeech())
    current.stt_ready = True
    current.audio_ready = True
    current.audio_received_ms = timestamp
    current.socket = SimpleNamespace(send_json=AsyncMock())
    return current


@pytest.mark.parametrize(
    "gate", ["stt", "microphone", "missing", "stale", "queue", "inflight", "pending"]
)
def test_progress_deadline_cannot_override_capture_or_work_gates(gate):
    async def scenario():
        current = make_runtime(4000)
        if gate == "stt":
            current.stt_ready = False
        elif gate == "microphone":
            current.audio_ready = False
        elif gate == "missing":
            current.audio_received_ms = None
        elif gate == "stale":
            current.audio_received_ms = -1001
        elif gate == "queue":
            current.audio_queue.put_nowait((current.session.generation, bytes(640)))
        elif gate == "inflight":
            current.audio_inflight = True
        else:
            current.audio_pending = True
        await current.tick(4000)
        assert current.session.state.value == "LISTENING"
        current.cancel_prompt()

    asyncio.run(scenario())


def test_first_pcm_starts_progress_budget_after_slow_capture_start(monkeypatch):
    async def scenario():
        clock = [0]
        monkeypatch.setattr(runtime_api, "now_ms", lambda: clock[0])
        current = SessionRuntime(session(silence_timeout_ms=10000), "test", ReadySpeech())
        await current.control({"type": "pause"})
        clock[0] = 1000
        await current.control({"type": "resume"})
        clock[0] = 5000
        await current.control({"type": "audio_ready"})
        assert current.session.progress_since_ms is None
        clock[0] = 8000
        await current.audio(bytes(640))
        assert current.session.progress_since_ms == 8000
        await current.tick(11999)
        assert current.session.state.value == "LISTENING"
        current.release_stream()

    asyncio.run(scenario())


def support(current, segment=0):
    for _ in range(4):
        current.audio_evidence.observe(array("h", [-100, 100] * 160).tobytes(), True)
    assert current.audio_evidence.allows(segment)


def test_stable_partial_advances_budget_without_committing_a_cursor():
    current = make_runtime(0)
    support(current)
    current.pending_hypothesis = (0, "我将继续")
    current.preview_hypothesis = current.pending_hypothesis
    current.preview_observations = 2
    current.preview_partial(1000)
    assert current.session.progress_since_ms == 1000
    assert current.session.last_progress_source == "partial"
    assert current.session.reading_position is None
    current.preview_applied = None
    current.preview_partial(3000)
    assert current.session.progress_since_ms == 1000


def test_deadline_settles_supported_partial_before_deciding_takeover():
    async def scenario():
        current = make_runtime(4000)
        support(current)
        current.pending_hypothesis = (0, "我将继续")
        current.session.handle("speech_start", 10)
        await current.tick(4000)
        assert current.session.state.value == "LISTENING"
        assert current.session.reading_position == SCRIPT.index("续")
        assert current.session.progress_since_ms == 4000
        current.audio_received_ms = 8000
        await current.tick(8000)
        assert current.session.state.value == "PROMPTING"
        current.cancel_prompt()

    asyncio.run(scenario())


def test_pending_noise_onsets_do_not_block_due_progress_timer():
    async def scenario():
        current = make_runtime(4000)
        current.session.handle("speech_start", 10)
        current.audio_evidence.observe(array("h", [-100, 100] * 160).tobytes(), True)
        assert current.audio_evidence.pending_voice
        await current.tick(4000)
        assert current.session.state.value == "PROMPTING"
        current.cancel_prompt()

    asyncio.run(scenario())


def test_progress_diagnostics_distinguish_countdown_and_single_prompt_latch():
    current = make_runtime(1000)
    current.session.handle("speech_start", 10)
    result = current.prompt_diagnostics(1000)
    assert result["prompt_wait_reason"] == "progress_wait"
    assert result["progress_elapsed_ms"] == 1000
    assert result["progress_remaining_ms"] == 3000
    current.session.progress_prompt_armed = False
    assert current.prompt_diagnostics(1000)["prompt_wait_reason"] == "awaiting_progress"


def test_environment_defaults_prepare_and_partial_configure_are_preserved(tmp_path, monkeypatch):
    monkeypatch.setenv("WHISPER_GUIDE_DEFAULT_PROMPT_MODE", "progress")
    monkeypatch.setenv("WHISPER_GUIDE_DEFAULT_PROGRESS_TIMEOUT_MS", "2500")
    monkeypatch.setenv("WHISPER_GUIDE_DEFAULT_CONTINUOUS_PROMPT", "true")
    app = create_app(Settings(data_dir=tmp_path, _env_file=None))
    app.state.speech_services = ReadySpeech()
    with TestClient(app) as client:
        config = client.get("/api/app-config").json()
        assert config["default_prompt_mode"] == "progress"
        assert config["default_progress_timeout_ms"] == 2500
        assert config["default_continuous_prompt"] is True
        script = client.post(
            "/api/scripts/upload",
            files={"files": ("p.txt", SCRIPT.encode(), "text/plain")},
        ).json()[0]
        with client.websocket_connect("/api/session") as socket:
            socket.send_json({"type": "prepare", "script_id": script["id"]})
            prepared = socket.receive_json()["session"]
            assert prepared["prompt_mode"] == "progress"
            assert prepared["progress_timeout_ms"] == 2500
            assert prepared["continuous_prompt"] is True
            socket.send_json({"type": "configure", "silence_timeout_ms": 1800})
            configured = socket.receive_json()["session"]
            assert configured["silence_timeout_ms"] == 1800
            assert configured["progress_timeout_ms"] == 2500
            assert configured["continuous_prompt"] is True
            socket.send_json(
                {"type": "configure", "prompt_mode": "silence", "continuous_prompt": False}
            )
            configured = socket.receive_json()["session"]
            assert configured["prompt_mode"] == "silence"
            assert configured["continuous_prompt"] is False


def test_full_websocket_noise_stream_prompts_once_and_postpone_rearms(tmp_path, monkeypatch):
    from test_silence_prompting import audio_is_ready, drained, wait_state

    clock = [10000]
    monkeypatch.setattr(runtime_api, "now_ms", lambda: clock[0])

    class NoiseStream:
        available = True

        def is_speech(self, pcm):
            return True

        def accept_pcm(self, pcm):
            return None

        def close(self):
            pass

    class NoiseSpeech(ReadySpeech):
        def create_stream(self, **kwargs):
            return NoiseStream()

    app = create_app(Settings(data_dir=tmp_path, _env_file=None))
    app.state.speech_services = NoiseSpeech()
    with TestClient(app) as client:
        script = client.post(
            "/api/scripts/upload",
            files={"files": ("noise.txt", SCRIPT.encode(), "text/plain")},
        ).json()[0]
        with client.websocket_connect("/api/session") as socket:
            socket.send_json(
                {
                    "type": "prepare",
                    "script_id": script["id"],
                    "prompt_mode": "progress",
                    "progress_timeout_ms": 2000,
                    "silence_timeout_ms": 1000,
                }
            )
            prepared = socket.receive_json()["session"]
            current = app.state.sessions[prepared["id"]]
            socket.send_json({"type": "start"})
            socket.receive_json()
            socket.send_json({"type": "audio_ready"})
            client.portal.call(audio_is_ready, current)
            packet = array("h", [-100, 100] * 160).tobytes()
            for elapsed in range(0, 2000, 20):
                clock[0] = 10000 + elapsed
                socket.send_bytes(packet)
                client.portal.call(drained, current, clock[0])
            assert current.session.speaking
            clock[0] = 12000
            socket.send_bytes(packet)
            client.portal.call(wait_state, current, "PROMPTING")
            assert current.session.transition_trigger == "progress_timeout"
            assert current.session.reading_position is None
            first = current.session.prompt
            socket.send_json({"type": "playback_finished", "prompt_id": first.id})
            client.portal.call(wait_state, current, "LISTENING")
            socket.send_json({"type": "audio_ready"})
            client.portal.call(audio_is_ready, current)
            for elapsed in range(0, 2020, 20):
                clock[0] = 12000 + elapsed
                socket.send_bytes(packet)
                client.portal.call(drained, current, clock[0])
            assert current.session.state.value == "LISTENING"
            assert not current.session.progress_prompt_armed
            socket.send_json({"type": "postpone_prompt"})
            client.portal.call(asyncio.sleep, 0.02)
            restarted = current.session.progress_since_ms
            assert current.session.progress_prompt_armed
            for elapsed in range(20, 2000, 20):
                clock[0] = restarted + elapsed
                socket.send_bytes(packet)
                client.portal.call(drained, current, clock[0])
            clock[0] = restarted + 2000
            socket.send_bytes(packet)
            client.portal.call(wait_state, current, "PROMPTING")
            assert current.session.prompt.start == first.next_position
            assert current.session.reading_position is None


def test_completed_final_at_partial_highwater_rearms_a_new_listening_round():
    current = session()
    current.note_progress(SCRIPT.index("足"), 1000, "partial")
    first = current.handle("tick", 5000)["prompt"]
    current.handle("playback_finished", 6000, prompt_id=first["id"])
    assert not current.progress_prompt_armed
    current.handle("transcript", 7000, text="我将继续立足", evidence_id=1)
    assert current.progress_prompt_armed
    assert current.last_progress_source == "confirmed"


def test_first_pcm_after_delayed_audio_ready_gets_full_budget(monkeypatch):
    async def scenario():
        clock = [0]
        monkeypatch.setattr(runtime_api, "now_ms", lambda: clock[0])
        current = SessionRuntime(session(silence_timeout_ms=10000), "test", ReadySpeech())
        await current.control({"type": "pause"})
        await current.control({"type": "resume"})
        clock[0] = 6000
        await current.control({"type": "audio_ready"})
        current.session.handle("postpone_prompt", 6500)
        clock[0] = 9000
        await current.audio(bytes(640))
        assert current.session.progress_since_ms == 9000
        await current.tick(12999)
        assert current.session.state.value == "LISTENING"
        current.release_stream()

    asyncio.run(scenario())


def test_independent_confirmed_backread_progress_starts_a_new_reading_budget():
    current = session(silence_timeout_ms=10000)
    current.handle("transcript", 100, text="用心传递", evidence_id=0)
    old_highwater = current.progress_highwater
    current.handle("transcript", 500, text="我将继续", evidence_id=1)
    assert current.reading_position == SCRIPT.index("续")
    assert current.progress_highwater == old_highwater
    assert current.progress_since_ms == 100
    current.handle("transcript", 1000, text="我将继续立足", evidence_id=2)
    assert current.progress_highwater == SCRIPT.index("足")
    assert current.progress_since_ms == 1000
    assert current.last_progress_source == "confirmed"
    current.handle("transcript", 1500, text="我将继续立足岗", evidence_id=3)
    assert current.progress_highwater == SCRIPT.index("岗")
    assert current.progress_since_ms == 1500
    assert current.handle("tick", 5499)["state"] == "LISTENING"
    assert current.handle("tick", 5500)["prompt"]["text"] == "位，"


def test_backread_revisions_of_one_segment_do_not_rebase_or_renew_budget():
    current = session()
    current.handle("transcript", 100, text="用心传递", evidence_id=0)
    highwater = current.progress_highwater
    for timestamp, text in ((500, "我将继续"), (1000, "我将继续立足"), (1500, "我将继续")):
        current.handle("transcript", timestamp, text=text, evidence_id=1)
    assert current.progress_highwater == highwater
    assert current.progress_since_ms == 100


def test_repeated_backread_text_in_new_segments_is_not_forward_progress():
    current = session()
    current.handle("transcript", 100, text="用心传递", evidence_id=0)
    highwater = current.progress_highwater
    for timestamp in (500, 1000, 1500):
        current.handle("transcript", timestamp, text="我将继续", evidence_id=timestamp)
    assert current.progress_highwater == highwater
    assert current.progress_since_ms == 100


def test_backread_partial_does_not_supply_independent_rebase_confirmation():
    current = session()
    current.handle("transcript", 100, text="用心传递", evidence_id=0)
    highwater = current.progress_highwater
    current.handle("transcript", 500, text="我将继续", evidence_id=1)
    current.preview_transcript("我将继续立足")
    current.note_preview_progress(1000)
    assert current.progress_highwater == highwater
    assert current.progress_since_ms == 100
    current.handle("transcript", 1500, text="我将继续立足", evidence_id=1)
    assert current.progress_highwater == highwater
    assert current.progress_since_ms == 100


@pytest.mark.parametrize("reset", ["stream", "retract"])
def test_backread_confirmation_cannot_cross_rebuilt_or_retracted_evidence(reset):
    current = session()
    current.handle("transcript", 100, text="用心传递", evidence_id=0)
    highwater = current.progress_highwater
    current.handle("transcript", 500, text="我将继续", evidence_id=1)
    if reset == "stream":
        current.reset_recovery_evidence()
    else:
        current.retract_evidence(1)
    current.handle("transcript", 1000, text="我将继续立足", evidence_id=2)
    assert current.progress_highwater == highwater
    assert current.progress_since_ms == 100
