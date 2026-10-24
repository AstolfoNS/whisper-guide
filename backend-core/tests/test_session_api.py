"""Protocol integration tests use explicit test speech services, never production fakes."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.infrastructure.speech import SpeechStream
from app.main import create_app


class TestSpeech:
    def status(self):
        return {"stt": "ready", "tts": "ready", "vad": "ready"}

    def create_stream(self):
        return SpeechStream(recognizer=lambda pcm: None)

    async def synthesize(self, text):
        return b"test-audio"


def setup(tmp_path: Path):
    app = create_app(Settings(environment="test", data_dir=tmp_path, _env_file=None))
    app.state.speech_services = TestSpeech()
    return app


def upload(client):
    result = client.post(
        "/api/scripts/upload",
        files={"files": ("test.txt", "欢迎大家。谢谢。".encode(), "text/plain")},
    )
    assert result.status_code == 201
    return result.json()[0]["id"]


def receive_state(ws, expected):
    message = ws.receive_json()
    assert message["type"] == "snapshot"
    assert message["session"]["state"] == expected
    return message["session"]


def test_prepare_start_pause_configure_resume_end(tmp_path):
    with TestClient(setup(tmp_path)) as client:
        script_id = upload(client)
        with client.websocket_connect("/api/session") as ws:
            ws.send_json({"type": "prepare", "script_id": script_id, "silence_timeout_ms": 1000})
            ready = receive_state(ws, "READY")
            ws.send_json({"type": "start"})
            receive_state(ws, "LISTENING")
            ws.send_json({"type": "audio_ready"})
            ws.send_bytes(b"\x00\x00" * 320)
            prompting = receive_state(ws, "PROMPTING")
            audio = ws.receive_json()
            assert audio["type"] == "audio"
            assert audio["prompt_id"] == prompting["prompt"]["id"]
            ws.send_json({"type": "pause"})
            paused = receive_state(ws, "PAUSED")
            ws.send_json({"type": "playback_finished", "prompt_id": audio["prompt_id"]})
            assert receive_state(ws, "PAUSED")["prompt_position"] == paused["prompt_position"]
            ws.send_json({"type": "configure", "silence_timeout_ms": 1000})
            assert receive_state(ws, "PAUSED")["silence_timeout_ms"] == 1000
            ws.send_json({"type": "resume"})
            receive_state(ws, "LISTENING")
            ws.send_json({"type": "terminate"})
            receive_state(ws, "TERMINATED")
            assert ready["script_id"] == script_id


def test_disconnect_retains_text_state_and_attach_does_not_auto_resume(tmp_path):
    with TestClient(setup(tmp_path)) as client:
        script_id = upload(client)
        with client.websocket_connect("/api/session") as ws:
            ws.send_json({"type": "prepare", "script_id": script_id})
            ready = receive_state(ws, "READY")
            ws.send_json({"type": "start"})
            receive_state(ws, "LISTENING")
        with client.websocket_connect("/api/session") as ws:
            ws.send_json({"type": "attach", "session_id": ready["id"]})
            assert receive_state(ws, "PAUSED")["script_id"] == script_id


def test_invalid_controls_and_script_binding(tmp_path):
    with TestClient(setup(tmp_path)) as client:
        script_id = upload(client)
        with client.websocket_connect("/api/session") as ws:
            ws.send_json({"type": "prepare", "script_id": script_id, "silence_timeout_ms": True})
            assert ws.receive_json()["type"] == "error"
            ws.send_json({"type": "prepare", "script_id": script_id})
            receive_state(ws, "READY")
            ws.send_json({"type": "prepare", "script_id": script_id})
            assert ws.receive_json()["type"] == "error"
            ws.send_json({"type": "reposition", "unit_index": 1})
            assert receive_state(ws, "READY")["reading_position"] == 1


def test_playback_advances_next_segment_and_interrupt_replays(tmp_path):
    with TestClient(setup(tmp_path)) as client:
        script_id = upload(client)
        with client.websocket_connect("/api/session") as ws:
            ws.send_json({"type": "prepare", "script_id": script_id})
            receive_state(ws, "READY")
            ws.send_json({"type": "start"})
            receive_state(ws, "LISTENING")
            ws.send_json({"type": "manual_prompt"})
            first = receive_state(ws, "PROMPTING")["prompt"]
            assert ws.receive_json()["type"] == "audio"
            ws.send_json({"type": "interrupt"})
            assert receive_state(ws, "LISTENING")["prompt_position"] == first["start"]
            ws.send_json({"type": "manual_prompt"})
            replay = receive_state(ws, "PROMPTING")["prompt"]
            assert replay["text"] == first["text"]
            assert ws.receive_json()["prompt_id"] == replay["id"]
            ws.send_json({"type": "playback_finished", "prompt_id": replay["id"]})
            next_state = receive_state(ws, "LISTENING")
            assert next_state["reading_position"] is None
            assert next_state["prompt_position"] == first["next_position"]
            ws.send_json({"type": "manual_prompt"})
            assert receive_state(ws, "PROMPTING")["prompt"]["text"] == "谢谢。"
            ws.receive_json()


def test_model_unavailable_does_not_start_and_does_not_break_connection(tmp_path):
    app = setup(tmp_path)

    class Unavailable(TestSpeech):
        def status(self):
            return {"stt": "unavailable", "tts": "ready"}

    app.state.speech_services = Unavailable()
    with TestClient(app) as client:
        script_id = upload(client)
        with client.websocket_connect("/api/session") as ws:
            ws.send_json({"type": "prepare", "script_id": script_id})
            receive_state(ws, "READY")
            ws.send_json({"type": "start"})
            assert ws.receive_json()["type"] == "error"
            ws.send_json({"type": "terminate"})
            receive_state(ws, "TERMINATED")


def test_tts_failure_enters_paused_and_retains_prompt_start(tmp_path):
    app = setup(tmp_path)

    class Failing(TestSpeech):
        async def synthesize(self, text):
            raise RuntimeError("network failed")

    app.state.speech_services = Failing()
    with TestClient(app) as client:
        script_id = upload(client)
        with client.websocket_connect("/api/session") as ws:
            ws.send_json({"type": "prepare", "script_id": script_id})
            ready = receive_state(ws, "READY")
            ws.send_json({"type": "start"})
            receive_state(ws, "LISTENING")
            ws.send_json({"type": "manual_prompt"})
            receive_state(ws, "PROMPTING")
            paused = receive_state(ws, "PAUSED")
            assert paused["prompt_position"] == ready["prompt_position"]


def test_invalid_audio_ready_before_start_does_not_trigger_prompt(tmp_path):
    with TestClient(setup(tmp_path)) as client:
        script_id = upload(client)
        with client.websocket_connect("/api/session") as ws:
            ws.send_json({"type": "prepare", "script_id": script_id})
            receive_state(ws, "READY")
            ws.send_json({"type": "audio_ready"})
            ws.send_json({"type": "configure", "silence_timeout_ms": 1000})
            assert receive_state(ws, "READY")["silence_timeout_ms"] == 1000


def test_new_session_after_termination_uses_new_script_queue(tmp_path):
    with TestClient(setup(tmp_path)) as client:
        first = upload(client)
        second = upload(client)
        with client.websocket_connect("/api/session") as ws:
            ws.send_json({"type": "prepare", "script_id": first})
            old = receive_state(ws, "READY")
            ws.send_json({"type": "terminate"})
            receive_state(ws, "TERMINATED")
            ws.send_json({"type": "prepare", "script_id": second})
            new = receive_state(ws, "READY")
            assert old["id"] != new["id"]
            ws.send_json({"type": "start"})
            receive_state(ws, "LISTENING")
            ws.send_json({"type": "manual_prompt"})
            receive_state(ws, "PROMPTING")
            assert ws.receive_json()["type"] == "audio"


def test_prepare_and_configure_reject_below_minimum_and_preserve_session(tmp_path):
    with TestClient(setup(tmp_path)) as client:
        script_id = upload(client)
        with client.websocket_connect("/api/session") as ws:
            ws.send_json({"type": "prepare", "script_id": script_id, "silence_timeout_ms": 999})
            assert ws.receive_json()["type"] == "error"
            ws.send_json({"type": "prepare", "script_id": script_id, "silence_timeout_ms": 1000})
            ready = receive_state(ws, "READY")
            assert ready["silence_timeout_ms"] == 1000
            ws.send_json({"type": "configure", "silence_timeout_ms": 999})
            assert ws.receive_json()["type"] == "error"
            ws.send_json({"type": "reposition", "unit_index": 0})
            unchanged = receive_state(ws, "READY")
            assert unchanged["id"] == ready["id"]
            assert unchanged["silence_timeout_ms"] == 1000
