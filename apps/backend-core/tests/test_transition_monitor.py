from datetime import datetime

from fastapi.testclient import TestClient
from test_session_api import receive_state, setup, upload

from app.domain.session import SpeechSession


def test_transition_history_is_bounded_and_only_records_real_transitions():
    session = SpeechSession("s", "大家好，欢迎。")
    initial = session.snapshot()
    assert initial["current_state"] == "READY"
    assert initial["previous_state"] is None
    datetime.fromisoformat(initial["transition_at"])
    session.handle("start", 0)
    session.handle("speech_start", 1)
    assert len(session.transition_history) == 2
    for i in range(12):
        session.handle("pause", 2 * i + 2)
        session.handle("resume", 2 * i + 3)
    snapshot = session.snapshot()
    assert len(snapshot["transition_history"]) == 10
    assert snapshot["previous_state"] == "PAUSED"
    assert snapshot["transition_trigger"] == "user_resumed"
    assert snapshot["allowed_transitions"] == ["PROMPTING", "PAUSED", "TERMINATED"]
    ended = session.handle("end", 30)
    assert ended["allowed_transitions"] == []
    assert session.handle("resume", 99) == ended


def test_websocket_broadcasts_prompt_pause_and_disconnect_context(tmp_path):
    with TestClient(setup(tmp_path)) as client:
        script = upload(client)
        with client.websocket_connect("/api/session") as ws:
            ws.send_json({"type": "prepare", "script_id": script})
            initial = receive_state(ws, "READY")
            assert initial["transition_trigger"] == "session_initialized"
            ws.send_json({"type": "start"})
            started = receive_state(ws, "LISTENING")
            assert started["previous_state"] == "READY"
            assert started["transition_trigger"] == "session_started"
            ws.send_json({"type": "manual_prompt"})
            prompting = receive_state(ws, "PROMPTING")
            assert prompting["transition_trigger"] == "manual_prompt"
            ws.receive_json()
        with client.websocket_connect("/api/session") as ws:
            ws.send_json({"type": "attach", "session_id": initial["id"]})
            attached = receive_state(ws, "PAUSED")
            assert attached["previous_state"] == "PROMPTING"
            assert attached["transition_trigger"] == "ws_disconnected"
            assert attached["transition_history"][-1]["to_state"] == "PAUSED"
