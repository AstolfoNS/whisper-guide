from fastapi.testclient import TestClient
from test_session_api import TestSpeech, receive_state, upload

from app.config import Settings
from app.main import create_app


def test_session_uses_activated_revision_and_draft_cannot_change_it(tmp_path):
    app = create_app(Settings(environment="test", data_dir=tmp_path, _env_file=None))
    app.state.speech_services = TestSpeech()
    with TestClient(app) as client:
        created = client.post(
            "/api/settings/model-configs",
            json={
                "name": "配置快照",
                "settings": {"llm_model": "captured-model"},
            },
        ).json()
        assert (
            client.post(f"/api/settings/model-configs/{created['id']}/activate").status_code == 200
        )
        script_id = upload(client)
        with client.websocket_connect("/api/session") as ws:
            ws.send_json({"type": "prepare", "script_id": script_id})
            ready = receive_state(ws, "READY")
            assert ready["model_config_id"] == created["id"]
            assert ready["model_config_revision"] == created["revision"]
            saved = client.put(
                f"/api/settings/model-configs/{created['id']}",
                json={
                    "revision": created["revision"],
                    "settings": {"llm_model": "pending-model"},
                },
            )
            assert saved.status_code == 200
            assert saved.json()["pending_activation"]
            assert (
                client.post(f"/api/settings/model-configs/{created['id']}/activate").status_code
                == 409
            )
            assert (
                client.put("/api/settings/models", json={"llm_model": "legacy-change"}).status_code
                == 409
            )
            runtime = app.state.sessions[ready["id"]]
            assert runtime.model_settings.llm_model == "captured-model"
            ws.send_json({"type": "start"})
            listening = receive_state(ws, "LISTENING")
            assert listening["model_config_revision"] == created["revision"]
            ws.send_json({"type": "terminate"})
            receive_state(ws, "TERMINATED")
        assert (
            client.post(f"/api/settings/model-configs/{created['id']}/activate").status_code == 200
        )
