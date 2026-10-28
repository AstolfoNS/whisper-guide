"""Public UI defaults are safe to expose; model credentials and service IDs stay private."""

import pytest
from fastapi.testclient import TestClient

from app.config import MODEL_SETTINGS_FIELDS, Settings, clone_model_settings
from app.main import create_app


@pytest.fixture(autouse=True)
def clear_app_title_environment(monkeypatch):
    monkeypatch.delenv("WHISPER_GUIDE_APP_TITLE", raising=False)


def public_defaults(title):
    return {
        "app_title": title,
        "default_silence_timeout_ms": 3000,
        "default_prompt_mode": "silence",
        "default_progress_timeout_ms": 4000,
        "default_continuous_prompt": False,
        "script_max_file_bytes": 2097152,
        "script_max_upload_files": 10,
    }


def test_default_title_is_whisper_guide(tmp_path):
    settings = Settings(environment="test", data_dir=tmp_path, _env_file=None)
    assert settings.app_title == "whisper-guide"
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/app-config").json() == public_defaults("whisper-guide")
        assert client.get("/openapi.json").json()["info"]["title"] == "whisper-guide API"


def test_environment_title_is_trimmed_and_used_by_api_documentation(tmp_path, monkeypatch):
    monkeypatch.setenv("WHISPER_GUIDE_APP_TITLE", "  自定义语音提示  ")
    settings = Settings(environment="test", data_dir=tmp_path, _env_file=None)
    expected = "自定义语音提示"
    assert settings.app_title == expected
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/app-config").json() == public_defaults(expected)
        assert client.app.title == f"{expected} API"
        assert client.get("/openapi.json").json()["info"]["title"] == f"{expected} API"
        assert client.get("/docs").status_code == 200


@pytest.mark.parametrize("value", ["", " ", "\t\n", "\u3000  "])
def test_blank_environment_title_falls_back_to_default(tmp_path, monkeypatch, value):
    monkeypatch.setenv("WHISPER_GUIDE_APP_TITLE", value)
    settings = Settings(environment="test", data_dir=tmp_path, _env_file=None)
    assert settings.app_title == "whisper-guide"
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/app-config").json() == public_defaults("whisper-guide")


def test_public_config_exposes_only_ui_defaults_and_preserves_plain_text(tmp_path):
    title = '<b>Title</b> & "quotes"'
    settings = Settings(
        environment="test",
        data_dir=tmp_path,
        app_title=title,
        cloud_stt_access_token="synthetic-speech-secret",
        llm_api_key="synthetic-llm-secret",
        _env_file=None,
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/app-config")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/json"
        assert response.json() == public_defaults(title)
        assert "synthetic-speech-secret" not in response.text
        assert "synthetic-llm-secret" not in response.text
        assert set(response.json()) == set(public_defaults(title))
        assert client.get("/api/health").json() == {
            "status": "ok",
            "service": "whisper-guide-backend",
            "version": "0.1.0",
        }


def test_public_config_reads_current_application_settings(tmp_path):
    settings = Settings(environment="test", data_dir=tmp_path, _env_file=None)
    with TestClient(create_app(settings)) as client:
        client.app.state.settings = Settings(
            environment="test", data_dir=tmp_path, app_title="Replacement title", _env_file=None
        )
        assert client.get("/api/app-config").json() == public_defaults("Replacement title")


def test_model_profile_updates_cannot_override_application_title(tmp_path):
    settings = Settings(
        environment="test", data_dir=tmp_path, app_title="Environment title", _env_file=None
    )
    updated = clone_model_settings(settings, {"app_title": "Unexpected model profile title"})
    assert updated.app_title == "Environment title"
    assert "app_title" not in MODEL_SETTINGS_FIELDS


def test_environment_ui_defaults_are_public_and_survive_model_profile_loading(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("WHISPER_GUIDE_DEFAULT_SILENCE_TIMEOUT_MS", "1500")
    monkeypatch.setenv("WHISPER_GUIDE_SCRIPT_MAX_FILE_BYTES", "4194304")
    monkeypatch.setenv("WHISPER_GUIDE_SCRIPT_MAX_UPLOAD_FILES", "15")
    settings = Settings(environment="test", data_dir=tmp_path, _env_file=None)
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/app-config")
        assert response.json() == {
            "app_title": "whisper-guide",
            "default_silence_timeout_ms": 1500,
            "default_prompt_mode": "silence",
            "default_progress_timeout_ms": 4000,
            "default_continuous_prompt": False,
            "script_max_file_bytes": 4194304,
            "script_max_upload_files": 15,
        }
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get("/api/app-config").json()["default_silence_timeout_ms"] == 1500


def test_session_without_explicit_timeout_uses_environment_default_and_honors_override(
    tmp_path, monkeypatch
):
    from app.infrastructure.speech import SpeechStream

    class AvailableSpeech:
        def status(self, **kwargs):
            return {"stt": "ready", "tts": "ready", "vad": "ready"}

        def create_stream(self, **kwargs):
            return SpeechStream(recognizer=lambda pcm: None)

    monkeypatch.setenv("WHISPER_GUIDE_DEFAULT_SILENCE_TIMEOUT_MS", "1500")
    settings = Settings(environment="test", data_dir=tmp_path, _env_file=None)
    app = create_app(settings)
    app.state.speech_services = AvailableSpeech()
    with TestClient(app) as client:
        uploaded = client.post(
            "/api/scripts/upload",
            files={"files": ("default.txt", "测试正文。".encode(), "text/plain")},
        ).json()[0]
        with client.websocket_connect("/api/session") as ws:
            ws.send_json({"type": "prepare", "script_id": uploaded["id"]})
            prepared = ws.receive_json()["session"]
            assert prepared["silence_timeout_ms"] == 1500
            ws.send_json({"type": "configure", "silence_timeout_ms": 2200})
            assert ws.receive_json()["session"]["silence_timeout_ms"] == 2200
