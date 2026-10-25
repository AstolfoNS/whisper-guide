"""Prompt waits expose bounded scalar reasons without serializing audio or scripts."""

import asyncio
import json
import threading
from array import array
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.api.session as runtime_api
from app.api.session import SessionRuntime
from app.domain.session import SessionState, SpeechSession


def runtime(timestamp=10000):
    session = SpeechSession("script", "大家好。欢迎参加本次测试。", silence_timeout_ms=3000)
    session.handle("start", timestamp - 1000)
    current = SessionRuntime(session, "diagnostics", SimpleNamespace(), "cloud")
    current.stt_ready = True
    current.audio_ready = True
    current.audio_received_ms = timestamp - 20
    current.last_frame_ms = timestamp - 5
    return current


@pytest.mark.parametrize("state", ["READY", "PROMPTING", "PAUSED", "TERMINATED"])
def test_non_listening_state_is_inactive_and_has_no_silence_countdown(state):
    current = runtime()
    current.session.state = SessionState(state)
    result = current.prompt_diagnostics(10000)
    assert result["prompt_wait_reason"] == "inactive"
    assert result["silence_elapsed_ms"] is None
    assert result["silence_remaining_ms"] is None


def test_readiness_reasons_precede_audio_and_clock_gates():
    current = runtime()
    current.audio_received_ms = None
    current.stt_ready = False
    current.audio_ready = False
    assert current.prompt_diagnostics(10000)["prompt_wait_reason"] == "stt_connecting"
    current.stt_ready = True
    assert current.prompt_diagnostics(10000)["prompt_wait_reason"] == "audio_not_ready"
    current.audio_ready = True
    current.last_frame_ms = 10000
    result = current.prompt_diagnostics(10000)
    assert result["prompt_wait_reason"] == "audio_missing"
    assert result["last_audio_received_age_ms"] is None
    assert result["silence_elapsed_ms"] is None
    assert result["silence_remaining_ms"] is None


def test_audio_receipt_not_processing_clock_drives_missing_audio_reason():
    current = runtime()
    current.audio_received_ms = 4999
    current.last_frame_ms = 10000
    current.audio_processed_ms = 10000
    current.audio_inflight = True
    result = current.prompt_diagnostics(10000)
    assert result["prompt_wait_reason"] == "audio_missing"
    assert result["last_audio_received_age_ms"] == 5001
    assert result["silence_elapsed_ms"] is None
    current.audio_received_ms = 5000
    assert current.prompt_diagnostics(10000)["prompt_wait_reason"] == "audio_processing"


@pytest.mark.parametrize("gate", ["queued", "inflight", "pending"])
def test_accepted_audio_work_explains_why_prompt_waits_before_voice_checks(gate):
    current = runtime()
    current.session.speaking = True
    if gate == "queued":
        current.audio_queue.put_nowait((current.session.generation, bytes(640)))
    elif gate == "inflight":
        current.audio_inflight = True
    else:
        current.audio_pending = True
    result = current.prompt_diagnostics(10000)
    assert result["prompt_wait_reason"] == (
        "audio_backlog" if gate == "queued" else "audio_processing"
    )
    assert result["audio_queued_ms"] == (20 if gate == "queued" else 0)
    assert result["audio_inflight"] is (gate == "inflight")


def test_actual_queued_pcm_takes_priority_over_normal_inflight_processing():
    current = runtime()
    current.audio_inflight = True
    assert current.prompt_diagnostics(10000)["prompt_wait_reason"] == "audio_processing"
    current.audio_queue.put_nowait((current.session.generation, bytes(640)))
    assert current.prompt_diagnostics(10000)["prompt_wait_reason"] == "audio_backlog"
    current.audio_queue.get_nowait()
    assert current.prompt_diagnostics(10000)["prompt_wait_reason"] == "audio_processing"


def test_confirmed_voice_has_no_silence_countdown():
    current = runtime()
    current.session.handle("speech_start", 9950)
    result = current.prompt_diagnostics(10000)
    assert result["prompt_wait_reason"] == "voice_active"
    assert result["silence_elapsed_ms"] is None
    assert result["silence_remaining_ms"] is None


def test_unconfirmed_short_voice_onset_is_distinct_from_confirmed_speech():
    current = runtime()
    current.audio_evidence.observe(array("h", [-100, 100] * 160).tobytes(), True)
    result = current.prompt_diagnostics(10000)
    assert result["prompt_wait_reason"] == "voice_pending"
    assert result["pending_voice"]
    assert not current.session.speaking


def test_end_of_script_has_no_remaining_prompt_even_with_old_pending_text():
    current = runtime()
    current.session.prompt_position = len(current.session.tracker.text)
    current.pending_hypothesis = (0, "待结算文字")
    assert current.prompt_diagnostics(10000)["prompt_wait_reason"] == "at_end"


def test_silence_countdown_uses_monotonic_deadline_and_saturates_at_zero():
    current = runtime()
    result = current.prompt_diagnostics(10000)
    assert result["prompt_wait_reason"] == "silence_wait"
    assert result["silence_elapsed_ms"] == 1000
    assert result["silence_remaining_ms"] == 2000
    current.audio_received_ms = 12000
    due = current.prompt_diagnostics(12000)
    assert due["prompt_wait_reason"] == "ready"
    assert due["silence_elapsed_ms"] == 3000
    assert due["silence_remaining_ms"] == 0
    assert current.prompt_diagnostics(12500)["silence_remaining_ms"] == 0


def test_due_pending_hypothesis_is_transiently_settling_not_an_infinite_gate():
    current = runtime(10000)
    current.session.silence_since_ms = 7000
    current.pending_hypothesis = (0, "大家好")
    assert current.prompt_diagnostics(10000)["prompt_wait_reason"] == "recognition_settling"
    current.settled_hypothesis = current.pending_hypothesis
    assert current.prompt_diagnostics(10000)["prompt_wait_reason"] == "ready"


@pytest.mark.parametrize("alignment", ["pending", "mismatch"])
def test_uncertain_alignment_can_still_be_ready_for_saved_anchor_prompt(alignment):
    current = runtime()
    current.session.handle("speech_start", 6000)
    if alignment == "mismatch":
        current.session.handle("transcript", 6100, text="这是完全不相关的背景文字")
    current.session.handle("speech_end", 7000)
    result = current.prompt_diagnostics(10000)
    assert current.session.snapshot()["alignment_status"] == alignment
    assert result["prompt_wait_reason"] == "ready"
    assert not current.session.reliable


def test_snapshot_includes_current_diagnostics_without_device_or_audio_identifiers(monkeypatch):
    current = runtime()
    current.audio_inference_ms = 53.7
    current.audio_batch_frames = 3
    monkeypatch.setattr(runtime_api, "now_ms", lambda: 10000)
    snapshot = current.snapshot()
    assert snapshot["prompt_wait_reason"] == "silence_wait"
    assert snapshot["last_audio_received_age_ms"] == 20
    assert snapshot["audio_inference_ms"] == 53
    assert snapshot["audio_batch_frames"] == 3
    assert snapshot["audio_ready"]
    assert not snapshot["pending_voice"]
    assert not snapshot["audio_inflight"]
    assert all(
        key not in current.prompt_diagnostics(10000)
        for key in ("pcm", "audio", "script_text", "units", "raw_transcript", "deviceId", "groupId")
    )


def test_default_timestamp_uses_backend_monotonic_clock(monkeypatch):
    current = runtime()
    monkeypatch.setattr(runtime_api, "now_ms", lambda: 10025)
    assert current.prompt_diagnostics()["last_audio_received_age_ms"] == 45


def test_heartbeat_is_small_generation_bound_and_throttled_to_one_second(monkeypatch):
    async def scenario():
        current = runtime()
        current.socket = SimpleNamespace(send_json=AsyncMock())
        current.last_prompt_diagnostics_ms = 10000
        monkeypatch.setattr(current, "snapshot", lambda: pytest.fail("full snapshot heartbeat"))
        await current.publish_prompt_diagnostics(10999)
        current.socket.send_json.assert_not_awaited()
        await current.publish_prompt_diagnostics(11000)
        event = current.socket.send_json.call_args.args[0]
        assert event["type"] == "audio_diagnostics"
        assert event["session_id"] == current.session.id
        assert event["generation"] == current.session.generation
        assert "script_text" not in json.dumps(event)
        assert "units" not in event["diagnostics"]
        assert "session" not in event
        for timestamp in (11000, 11500, 11999):
            await current.publish_prompt_diagnostics(timestamp)
        assert current.socket.send_json.await_count == 1
        await current.publish_prompt_diagnostics(12000)
        assert current.socket.send_json.await_count == 2
        current.session.state = SessionState.PAUSED
        await current.publish_prompt_diagnostics(14000)
        assert current.socket.send_json.await_count == 2
        current.session.state = SessionState.LISTENING
        current.socket = None
        await current.publish_prompt_diagnostics(15000)

    asyncio.run(scenario())


@pytest.mark.parametrize("queued", [False, True])
def test_websocket_heartbeat_explains_missing_or_queued_audio_without_full_snapshot(
    tmp_path, monkeypatch, queued
):
    from fastapi.testclient import TestClient
    from test_silence_prompting import make_app, prepare

    clock = [10000]
    monkeypatch.setattr(runtime_api, "now_ms", lambda: clock[0])
    with TestClient(make_app(tmp_path)) as client, client.websocket_connect("/api/session") as ws:
        current = prepare(client, ws, 6000)
        ws.send_json({"type": "audio_ready"})

        async def acknowledged():
            async with asyncio.timeout(2):
                while not current.audio_ready:
                    await asyncio.sleep(0.001)

        client.portal.call(acknowledged)

        if queued:
            entered = threading.Event()
            release = threading.Event()

            class HeldInference:
                def __call__(self, pcm):
                    entered.set()
                    assert release.wait(2)
                    return None

                def close(self):
                    release.set()

            current.stream._recognizer = HeldInference()
            ws.send_bytes(bytes(640))
            assert entered.wait(1)
            ws.send_bytes(bytes(640))

            async def queued_frame():
                async with asyncio.timeout(2):
                    while current.audio_queued_ms != 20:
                        await asyncio.sleep(0.001)

            client.portal.call(queued_frame)
        clock[0] = 11000
        event = ws.receive_json()
        assert event["type"] == "audio_diagnostics"
        assert event["session_id"] == current.session.id
        assert event["generation"] == current.session.generation
        assert event["diagnostics"]["prompt_wait_reason"] == (
            "audio_backlog" if queued else "audio_missing"
        )
        assert event["diagnostics"]["audio_queued_ms"] == (20 if queued else 0)
        assert "units" not in json.dumps(event)
        assert "script_text" not in json.dumps(event)
        assert current.session.state == SessionState.LISTENING
        assert current.session.prompt is None
        ws.send_json({"type": "terminate"})
        ended = ws.receive_json()
        assert ended["type"] == "snapshot"
        assert ended["session"]["state"] == "TERMINATED"


def test_audio_ready_without_any_pcm_never_prompts_and_eventually_pauses(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_silence_prompting import make_app, prepare

    clock = [10000]
    monkeypatch.setattr(runtime_api, "now_ms", lambda: clock[0])
    with TestClient(make_app(tmp_path)) as client, client.websocket_connect("/api/session") as ws:
        current = prepare(client, ws, 1000)
        ws.send_json({"type": "audio_ready"})

        async def acknowledged():
            async with asyncio.timeout(2):
                while not current.audio_ready:
                    await asyncio.sleep(0.001)

        client.portal.call(acknowledged)
        clock[0] = 11000
        event = ws.receive_json()
        assert event["type"] == "audio_diagnostics"
        assert event["diagnostics"]["prompt_wait_reason"] == "audio_missing"
        assert current.session.state == SessionState.LISTENING
        assert current.session.prompt is None
        clock[0] = 15001
        paused = ws.receive_json()
        assert paused["type"] == "snapshot"
        assert paused["session"]["state"] == "PAUSED"
        assert "麦克风数据中断" in paused["session"]["pause_reason"]
        assert paused["session"]["prompt"] is None
