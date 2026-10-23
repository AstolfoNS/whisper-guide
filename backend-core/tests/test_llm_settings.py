import asyncio
import json
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.api.settings as settings_api
from app.config import Settings
from app.infrastructure.llm import LLMSemanticRefiner, SemanticRefinement
from app.main import create_app


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, endpoint, **kwargs):
        self.calls.append((endpoint, kwargs))
        return FakeResponse(self.payload)


def test_refiner_accepts_only_script_substrings():
    fake = FakeClient(
        {"choices": [{"message": {"content": '{"matched_quote":"大家好","confidence":0.9}'}}]}
    )
    refiner = LLMSemanticRefiner(
        align_mode="fallback",
        base_url="https://example.test/v1",
        model="any-model",
        api_key="secret",
        client_factory=lambda **kwargs: fake,
    )
    result = asyncio.run(
        refiner.refine("大家好，欢迎。", "大甲好", turn_token=3, current_turn_token=3)
    )
    assert result == SemanticRefinement("大家好", 0.9)
    assert fake.calls[0][0] == "https://example.test/v1/chat/completions"


def test_refiner_rejects_stale_turn_and_fabricated_quote():
    fake = FakeClient({"matched_quote": "不存在", "confidence": 1})
    refiner = LLMSemanticRefiner(
        align_mode="fallback",
        base_url="https://example.test/v1",
        model="any-model",
        api_key="secret",
        client_factory=lambda **kwargs: fake,
    )
    assert (
        asyncio.run(refiner.refine("大家好", "大甲好", turn_token=1, current_turn_token=2)) is None
    )
    assert (
        asyncio.run(refiner.refine("大家好", "大甲好", turn_token=2, current_turn_token=2)) is None
    )


def make_settings(tmp_path, **kwargs):
    return Settings(environment="test", data_dir=tmp_path, _env_file=None, **kwargs)


def test_model_settings_mask_secrets_and_save_validated_clone(tmp_path: Path):
    initial = make_settings(
        tmp_path,
        llm_align_mode="fallback",
        llm_base_url="https://old.example/v1",
        llm_model="old",
        llm_api_key="sk-secret-1234",
        cloud_stt_access_token="abcd",
    )
    app = create_app(initial)
    with TestClient(app) as client:
        response = client.get("/api/settings/models")
        assert response.status_code == 200
        assert "sk-secret-1234" not in response.text
        assert "abcd" not in response.text
        assert response.json()["llm"]["api_key"] == "****"
        assert response.json()["llm_api_key_configured"] is True
        assert response.json()["cloud_stt_token_configured"] is True
        assert {item["id"] for item in response.json()["llm_presets"]} == {
            "custom",
            "deepseek",
            "qwen",
            "doubao",
            "openai",
            "ollama",
        }

        response = client.put(
            "/api/settings/models",
            json={
                "llm_base_url": "https://new.example/v1",
                "llm_model": "new-model",
                "llm_api_key": response.json()["llm"]["api_key"],
                "cloud_stt_access_token": "",
            },
        )
        assert response.status_code == 200
        assert initial.llm_base_url == "https://old.example/v1"
        active = app.state.settings
        assert active.llm_base_url == "https://new.example/v1"
        assert active.llm_model == "new-model"
        assert active.llm_api_key == "sk-secret-1234"
        assert active.cloud_stt_access_token == "abcd"
        assert active.model_settings_path.is_file()
        assert stat.S_IMODE(active.model_settings_path.stat().st_mode) == 0o600
        assert list(tmp_path.glob(".model-settings-*.tmp")) == []


def test_model_settings_restart_loads_saved_configuration(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as client:
        assert (
            client.put(
                "/api/settings/models",
                json={
                    "llm_model": "user-model",
                    "llm_base_url": "https://custom.example/v1",
                    "llm_api_key": "private-restart-token",
                },
            ).status_code
            == 200
        )
    restarted = create_app(make_settings(tmp_path))
    with TestClient(restarted) as client:
        assert restarted.state.settings.llm_api_key == "private-restart-token"
        payload = client.get("/api/settings/models").json()
        assert payload["llm_model"] == "user-model"
        assert payload["llm_base_url"] == "https://custom.example/v1"
        assert payload["llm_api_key_hint"] == "****"


def test_nested_update_and_explicit_flat_override(tmp_path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        response = client.put(
            "/api/settings/models",
            json={
                "llm": {
                    "align_mode": "hybrid",
                    "provider_preset": "qwen",
                    "base_url": "https://nested.example/v1",
                    "model": "nested-model",
                    "timeout_ms": 1200,
                },
                "llm_model": "flat-model",
                "stt": {
                    "provider": "cloud",
                    "cloud_opt_in": True,
                    "cloud_stt_endpoint": "https://speech.example/stt",
                    "cloud_stt_app_id": "app-1",
                    "access_token": "private-cloud-token",
                },
            },
        )
        assert response.status_code == 200
        active = app.state.settings
        assert active.llm_align_mode == "hybrid"
        assert active.llm_provider_preset == "qwen"
        assert active.llm_base_url == "https://nested.example/v1"
        assert active.llm_model == "flat-model"
        assert active.llm_timeout_ms == 1200
        assert active.stt_provider == "cloud"
        assert active.cloud_stt_endpoint == active.cloud_endpoint == "https://speech.example/stt"
        assert active.cloud_stt_access_token == active.cloud_access_token == "private-cloud-token"
        assert "private-cloud-token" not in response.text


def test_opt_in_false_forces_local_and_empty_endpoint_can_clear(tmp_path):
    app = create_app(
        make_settings(
            tmp_path,
            cloud_opt_in=True,
            stt_provider="cloud",
            cloud_stt_endpoint="https://speech.example/stt",
        )
    )
    with TestClient(app) as client:
        response = client.put(
            "/api/settings/models",
            json={"cloud_opt_in": False, "stt_provider": "cloud", "cloud_stt_endpoint": ""},
        )
        assert response.status_code == 200
        assert response.json()["stt_provider"] == "local"
        assert app.state.settings.cloud_endpoint == app.state.settings.cloud_stt_endpoint == ""


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "1500"])
def test_model_settings_require_strict_positive_integer_timeout(tmp_path, value):
    with TestClient(create_app(make_settings(tmp_path))) as client:
        assert client.put("/api/settings/models", json={"llm_timeout_ms": value}).status_code == 422
        assert (
            client.put("/api/settings/models", json={"llm": {"timeout_ms": value}}).status_code
            == 422
        )
        assert not (tmp_path / ".model-settings.json").exists()


def test_model_settings_invalid_mode_or_url_does_not_mutate(tmp_path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        for payload in (
            {"llm_align_mode": "bad"},
            {"llm_base_url": "file:///private"},
            {"llm_base_url": "https://user:secret@example.test/v1"},
        ):
            assert client.put("/api/settings/models", json=payload).status_code == 422
        assert app.state.settings.llm_align_mode == "off"
        assert app.state.settings.llm_base_url == ""


def test_connection_uses_transient_clone_and_sanitizes_upstream_error(tmp_path, monkeypatch):
    captures = []

    async def fake_test(refiner):
        captures.append((refiner.base_url, refiner.model, refiner.api_key))
        return {"ok": False, "latency_ms": 17, "message": "echo private-test-key"}

    monkeypatch.setattr(LLMSemanticRefiner, "test_connection", fake_test)
    app = create_app(make_settings(tmp_path, llm_model="active-model"))
    with TestClient(app) as client:
        response = client.post(
            "/api/settings/models/test",
            json={
                "llm_base_url": "https://test.example/v1",
                "llm_model": "test-model",
                "llm_api_key": "private-test-key",
            },
        )
        assert response.status_code == 200
        assert response.json()["ok"] is False
        assert response.json()["latency_ms"] == 17
        assert "private-test-key" not in response.text
        assert captures == [("https://test.example/v1", "test-model", "private-test-key")]
        assert app.state.settings.llm_model == "active-model"
        assert app.state.settings.llm_api_key == ""
        assert not app.state.settings.model_settings_path.exists()


def test_connection_returns_success_and_never_echoes_provider_metadata(tmp_path, monkeypatch):
    async def fake_test(refiner):
        return {
            "ok": True,
            "latency_ms": 4,
            "message": "secret-value",
            "headers": {"Authorization": "secret-value"},
        }

    monkeypatch.setattr(LLMSemanticRefiner, "test_connection", fake_test)
    with TestClient(create_app(make_settings(tmp_path))) as client:
        response = client.post("/api/settings/models/test", json={})
        assert response.json() == {"ok": True, "latency_ms": 4, "message": "连接成功。"}


def test_failed_persistence_leaves_active_settings_and_response_secret_free(tmp_path, monkeypatch):
    def fail_save(settings):
        raise OSError("private-key-must-not-be-returned")

    monkeypatch.setattr(settings_api, "persist_model_settings", fail_save)
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        response = client.put(
            "/api/settings/models", json={"llm_model": "uncommitted", "llm_api_key": "secret"}
        )
        assert response.status_code == 503
        assert "private-key" not in response.text
        assert app.state.settings.llm_model == ""


def test_corrupt_saved_settings_leave_environment_defaults(tmp_path):
    (tmp_path / ".model-settings.json").write_text("{broken", encoding="utf-8")
    app = create_app(make_settings(tmp_path, llm_model="environment-model"))
    assert app.state.settings.llm_model == "environment-model"
    (tmp_path / ".model-settings.json").write_text(
        json.dumps({"llm_timeout_ms": -1}), encoding="utf-8"
    )
    app = create_app(make_settings(tmp_path, llm_model="environment-model"))
    assert app.state.settings.llm_timeout_ms == 1500


def test_settings_validation_errors_never_echo_credentials(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as client:
        response = client.put(
            "/api/settings/models",
            json={"llm_base_url": "https://user:embedded-private-key@example.test/v1"},
        )
        assert response.status_code == 422
        assert "embedded-private-key" not in response.text
        assert "input" not in response.json()["detail"][0]
        response = client.post(
            "/api/settings/models/test",
            json={"llm_api_key": {"wrong_type": "private-nested-value"}},
        )
        assert response.status_code == 422
        assert "private-nested-value" not in response.text
