"""Continuous quiet PCM must reach the configured automatic-prompt deadline."""

import asyncio
import base64
from array import array

import pytest
from fastapi.testclient import TestClient

import app.api.session as session_api
from app.config import Settings
from app.infrastructure.speech import SpeechStream
from app.main import create_app


class BusyPollClock:
    """Keep periodic polling asleep so completed-frame scheduling is isolated."""

    def __getattr__(self, name):
        return getattr(asyncio, name)

    async def sleep(self, delay, *args):
        if delay == 0.05:
            await asyncio.Event().wait()
        else:
            await asyncio.sleep(delay, *args)


class QuietSpeech:
    def __init__(self):
        self.spoken = []

    def status(self):
        return {"stt": "ready", "vad": "ready", "tts": "ready"}

    def create_stream(self):
        return SpeechStream(recognizer=lambda pcm: None)

    async def synthesize(self, text):
        self.spoken.append(text)
        return b"in-memory-quiet-prompt"


def make_app(tmp_path):
    app = create_app(Settings(environment="test", data_dir=tmp_path, _env_file=None))
    app.state.speech_services = QuietSpeech()
    return app


def state(ws, expected):
    event = ws.receive_json()
    while event["type"] == "audio_diagnostics":
        event = ws.receive_json()
    assert event["type"] == "snapshot"
    assert event["session"]["state"] == expected
    return event["session"]


def prepare(client, ws, timeout):
    response = client.post(
        "/api/scripts/upload",
        files={"files": ("quiet.txt", "欢迎大家。谢谢。".encode(), "text/plain")},
    )
    assert response.status_code == 201
    ws.send_json(
        {
            "type": "prepare",
            "script_id": response.json()[0]["id"],
            "silence_timeout_ms": timeout,
        }
    )
    ready = state(ws, "READY")
    ws.send_json({"type": "start"})
    state(ws, "LISTENING")
    return client.app.state.sessions[ready["id"]]


async def drained(runtime, timestamp):
    async with asyncio.timeout(2):
        while runtime.session.state.value == "LISTENING":
            if (
                runtime.last_frame_ms == timestamp
                and not runtime.audio_inflight
                and not runtime.audio_pending
                and runtime.audio_queue.empty()
            ):
                return
            await asyncio.sleep(0.001)


async def wait_state(runtime, expected):
    async with asyncio.timeout(2):
        while runtime.session.state.value != expected:
            await asyncio.sleep(0.01)


def quiet_until_deadline(client, ws, runtime, clock, timeout):
    started = clock[0]
    ws.send_json({"type": "audio_ready"})
    for elapsed in range(0, timeout, 20):
        clock[0] = started + elapsed
        ws.send_bytes(bytes(640))
        client.portal.call(drained, runtime, clock[0])
        assert runtime.session.state.value == "LISTENING"
        assert runtime.session.silence_since_ms == started
        if elapsed == timeout // 2:
            ws.send_json({"type": "audio_ready"})
    # Let the real periodic task observe a just-before-deadline time as well.
    clock[0] = started + timeout - 1
    client.portal.call(asyncio.sleep, 0.06)
    assert runtime.session.state.value == "LISTENING"
    clock[0] = started + timeout
    ws.send_bytes(bytes(640))
    client.portal.call(wait_state, runtime, "PROMPTING")
    prompted = state(ws, "PROMPTING")
    audio = ws.receive_json()
    assert audio["type"] == "audio"
    assert audio["prompt_id"] == prompted["prompt"]["id"]
    assert base64.b64decode(audio["data"]) == b"in-memory-quiet-prompt"
    assert prompted["transition_trigger"] == "silence_threshold_reached"
    assert prompted["reading_position"] is None
    return prompted


@pytest.mark.parametrize("timeout", [1000, 3000, 6000])
def test_continuous_quiet_frames_auto_prompt_at_configured_deadline(tmp_path, monkeypatch, timeout):
    clock = [10000]
    monkeypatch.setattr(session_api, "now_ms", lambda: clock[0])
    app = make_app(tmp_path)
    with TestClient(app) as client, client.websocket_connect("/api/session") as ws:
        runtime = prepare(client, ws, timeout)
        prompted = quiet_until_deadline(client, ws, runtime, clock, timeout)
        assert prompted["prompt"]["text"] == "欢迎大家。"
        assert app.state.speech_services.spoken == ["欢迎大家。"]


def test_playback_and_resume_require_fresh_audio_ready_then_restart_quiet_wait(
    tmp_path, monkeypatch
):
    clock = [10000]
    monkeypatch.setattr(session_api, "now_ms", lambda: clock[0])
    app = make_app(tmp_path)
    with TestClient(app) as client, client.websocket_connect("/api/session") as ws:
        runtime = prepare(client, ws, 1000)
        first = quiet_until_deadline(client, ws, runtime, clock, 1000)
        clock[0] += 10
        ws.send_json({"type": "playback_finished", "prompt_id": first["prompt"]["id"]})
        listening = state(ws, "LISTENING")
        assert listening["prompt_position"] == first["prompt"]["next_position"]
        clock[0] += 10000
        client.portal.call(asyncio.sleep, 0.06)
        assert runtime.session.state.value == "LISTENING"  # No audio_ready yet.
        ws.send_json({"type": "pause"})
        state(ws, "PAUSED")
        clock[0] += 10000
        client.portal.call(asyncio.sleep, 0.06)
        assert runtime.session.state.value == "PAUSED"
        ws.send_json({"type": "resume"})
        state(ws, "LISTENING")
        second = quiet_until_deadline(client, ws, runtime, clock, 1000)
        assert second["prompt"]["text"] == "谢谢。"
        assert second["prompt_position"] == first["prompt"]["next_position"]
        assert app.state.speech_services.spoken == ["欢迎大家。", "谢谢。"]


@pytest.mark.parametrize("pending_ms", [0, 20, 40])
def test_microphone_data_loss_is_paused_instead_of_treated_as_live_silence(
    tmp_path, monkeypatch, pending_ms
):
    clock = [10000]
    monkeypatch.setattr(session_api, "now_ms", lambda: clock[0])
    app = make_app(tmp_path)
    with TestClient(app) as client, client.websocket_connect("/api/session") as ws:
        runtime = prepare(client, ws, 6000)
        ws.send_json({"type": "audio_ready"})
        pcm = array("h", [-1000, 1000] * (pending_ms * 8)).tobytes()
        ws.send_bytes(pcm if pending_ms else bytes(640))
        client.portal.call(drained, runtime, clock[0])
        assert runtime.audio_evidence.pending_voice is bool(pending_ms)
        clock[0] += 5001
        client.portal.call(wait_state, runtime, "PAUSED")
        paused = state(ws, "PAUSED")
        assert "麦克风数据中断" in paused["pause_reason"]
        assert app.state.speech_services.spoken == []


def test_finishing_quiet_audio_checks_deadline_when_periodic_poll_has_no_idle_slot(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(session_api, "asyncio", BusyPollClock())
    clock = [10000]
    monkeypatch.setattr(session_api, "now_ms", lambda: clock[0])
    app = make_app(tmp_path)
    with TestClient(app) as client, client.websocket_connect("/api/session") as ws:
        runtime = prepare(client, ws, 1000)
        prompted = quiet_until_deadline(client, ws, runtime, clock, 1000)
        assert prompted["prompt"]["text"] == "欢迎大家。"


async def signaled(event):
    async with asyncio.timeout(2):
        await event.wait()


async def audio_is_ready(runtime):
    async with asyncio.timeout(2):
        while not runtime.audio_ready:
            await asyncio.sleep(0.001)


def test_old_audio_completion_cannot_prompt_a_resumed_generation(tmp_path, monkeypatch):
    monkeypatch.setattr(session_api, "asyncio", BusyPollClock())
    clock = [10000]
    monkeypatch.setattr(session_api, "now_ms", lambda: clock[0])
    entered = asyncio.Event()
    release = asyncio.Event()
    completed = asyncio.Event()
    original_audio = session_api.SessionRuntime.audio
    first_frame = True

    async def held_audio(self, pcm, generation=None):
        nonlocal first_frame
        if first_frame:
            first_frame = False
            entered.set()
            await release.wait()
            await original_audio(self, pcm, generation)
            completed.set()
        else:
            await original_audio(self, pcm, generation)

    monkeypatch.setattr(session_api.SessionRuntime, "audio", held_audio)
    app = make_app(tmp_path)
    with TestClient(app) as client, client.websocket_connect("/api/session") as ws:
        runtime = prepare(client, ws, 1000)
        ws.send_json({"type": "audio_ready"})
        ws.send_bytes(bytes(640))
        client.portal.call(signaled, entered)
        old_generation = runtime.session.generation
        ws.send_json({"type": "pause"})
        state(ws, "PAUSED")
        clock[0] = 11000
        ws.send_json({"type": "resume"})
        resumed = state(ws, "LISTENING")
        assert resumed["generation"] > old_generation
        ws.send_json({"type": "audio_ready"})
        client.portal.call(audio_is_ready, runtime)
        clock[0] = 12000
        client.portal.call(release.set)
        client.portal.call(signaled, completed)
        client.portal.call(asyncio.sleep, 0.02)
        assert runtime.session.state.value == "LISTENING"
        assert app.state.speech_services.spoken == []
        # A frame belonging to the current turn can settle its own due clock.
        ws.send_bytes(bytes(640))
        client.portal.call(wait_state, runtime, "PROMPTING")
        prompted = state(ws, "PROMPTING")
        assert prompted["prompt"]["text"] == "欢迎大家。"
        assert ws.receive_json()["type"] == "audio"
        assert app.state.speech_services.spoken == ["欢迎大家。"]


def test_queued_voice_at_deadline_precedes_completed_quiet_frame(tmp_path, monkeypatch):
    monkeypatch.setattr(session_api, "asyncio", BusyPollClock())
    clock = [10000]
    monkeypatch.setattr(session_api, "now_ms", lambda: clock[0])
    entered = asyncio.Event()
    release = asyncio.Event()
    original_audio = session_api.SessionRuntime.audio
    first_frame = True

    async def held_audio(self, pcm, generation=None):
        nonlocal first_frame
        if first_frame:
            first_frame = False
            entered.set()
            await release.wait()
        await original_audio(self, pcm, generation)

    async def queued_voice(runtime):
        async with asyncio.timeout(2):
            while runtime.audio_queue.empty():
                await asyncio.sleep(0.001)

    monkeypatch.setattr(session_api.SessionRuntime, "audio", held_audio)
    app = make_app(tmp_path)
    with TestClient(app) as client, client.websocket_connect("/api/session") as ws:
        runtime = prepare(client, ws, 1000)
        ws.send_json({"type": "audio_ready"})
        clock[0] = 10999
        ws.send_bytes(bytes(640))
        client.portal.call(signaled, entered)
        clock[0] = 11000
        ws.send_bytes(array("h", [-10000, 10000] * 480).tobytes())
        client.portal.call(queued_voice, runtime)
        client.portal.call(release.set)
        speaking = state(ws, "LISTENING")
        assert speaking["speaking"]
        client.portal.call(drained, runtime, clock[0])
        assert runtime.session.state.value == "LISTENING"
        assert runtime.session.silence_since_ms is None
        assert app.state.speech_services.spoken == []
