"""Native streaming ASR settings, credential separation and safe protocol probes."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import MODEL_SETTINGS_FIELDS, Settings
from app.main import create_app

NEW_FIELDS = {
    "cloud_stt_protocol": "volcengine-ws",
    "cloud_stt_auth_mode": "api-key",
    "cloud_stt_resource_id": "volc.seedasr.sauc.duration",
    "cloud_stt_timeout_ms": 7000,
    "cloud_stt_enable_nonstream": False,
}


def make_app(tmp_path, **values):
    return create_app(Settings(environment="test", data_dir=tmp_path, _env_file=None, **values))


def cloud_fields(**updates):
    return {
        **NEW_FIELDS,
        "stt_provider": "cloud",
        "cloud_opt_in": True,
        "cloud_stt_endpoint": "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel",
        "cloud_stt_access_token": "asr-private-key",
        **updates,
    }


def test_native_asr_profile_fields_survive_save_activation_restart_and_copy(tmp_path):
    app = make_app(tmp_path, llm_api_key="unrelated-active-llm-key")
    with TestClient(app) as client:
        created = client.post(
            "/api/settings/model-configs",
            json={"name": "双引擎", "settings": cloud_fields(llm_api_key="llm-private-key")},
        )
        assert created.status_code == 201, created.text
        profile = created.json()
        identifier = profile["id"]
        public = profile["settings"]
        for field, value in NEW_FIELDS.items():
            assert field in MODEL_SETTINGS_FIELDS
            assert public[field] == value
            assert public["stt"][field.removeprefix("cloud_stt_")] == value
        assert public["stt"]["access_token"] == "****"
        assert public["stt"]["cloud_endpoint"].startswith("wss://")
        assert "asr-private-key" not in created.text
        assert "llm-private-key" not in created.text
        assert app.state.settings.cloud_stt_protocol == "http-json"
        copied = client.post(f"/api/settings/model-configs/{identifier}/duplicate", json={}).json()
        assert copied["settings"]["cloud_stt_protocol"] == "volcengine-ws"
        assert copied["settings"]["has_access_token"] is False
        assert copied["settings"]["has_api_key"] is False
        assert client.post(f"/api/settings/model-configs/{identifier}/activate").status_code == 200
        for field, value in NEW_FIELDS.items():
            assert getattr(app.state.settings, field) == value
        assert app.state.settings.cloud_stt_access_token == "asr-private-key"
        assert app.state.settings.llm_api_key == "llm-private-key"
    restarted = make_app(tmp_path)
    with TestClient(restarted):
        for field, value in NEW_FIELDS.items():
            assert getattr(restarted.state.settings, field) == value
        assert restarted.state.settings.cloud_stt_access_token == "asr-private-key"


def test_nested_asr_settings_and_explicit_flat_override(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        response = client.put(
            "/api/settings/models",
            json={
                "stt": {
                    "provider": "cloud",
                    "cloud_opt_in": True,
                    "protocol": "volcengine-ws",
                    "auth_mode": "app-token",
                    "resource_id": "volc.seedasr.sauc.concurrent",
                    "timeout_ms": 3000,
                    "enable_nonstream": True,
                    "cloud_endpoint": "wss://speech.example/stream",
                    "cloud_app_id": "app-id",
                    "access_token": "asr-only-key",
                },
                "cloud_stt_timeout_ms": 6000,
                "cloud_stt_enable_nonstream": False,
            },
        )
        assert response.status_code == 200, response.text
        settings = response.json()
        assert settings["cloud_stt_protocol"] == "volcengine-ws"
        assert settings["cloud_stt_auth_mode"] == "app-token"
        assert settings["stt"]["resource_id"] == "volc.seedasr.sauc.concurrent"
        assert settings["stt"]["timeout_ms"] == 6000
        assert settings["stt"]["enable_nonstream"] is False
        assert settings["has_api_key"] is False
        assert settings["has_access_token"] is True


@pytest.mark.parametrize("value", [199, 30001, True, 1000.5, "5000"])
def test_asr_timeout_requires_bounded_strict_integer(tmp_path, value):
    with TestClient(make_app(tmp_path)) as client:
        for settings in ({"cloud_stt_timeout_ms": value}, {"stt": {"timeout_ms": value}}):
            response = client.post(
                "/api/settings/model-configs", json={"name": "invalid", "settings": settings}
            )
            assert response.status_code == 422
        assert not (tmp_path / ".model-configs.json").exists()


@pytest.mark.parametrize(
    "settings",
    [
        {"cloud_stt_protocol": "vendor-sdk"},
        {"cloud_stt_auth_mode": "borrow-llm-key"},
        {"cloud_stt_enable_nonstream": "true"},
        {"stt": {"enable_nonstream": 1}},
        {"cloud_stt_resource_id": "x" * 201},
    ],
)
def test_asr_schema_rejects_invalid_values(tmp_path, settings):
    with TestClient(make_app(tmp_path)) as client:
        response = client.post(
            "/api/settings/model-configs", json={"name": "invalid", "settings": settings}
        )
        assert response.status_code == 422
        assert not (tmp_path / ".model-configs.json").exists()


@pytest.mark.parametrize(
    "endpoint",
    [
        "wss://speech.example/asr",
        "https://speech.example/asr",
        "ws://localhost:9010/asr",
        "ws://127.0.0.2:9010/asr",
        "ws://[::1]:9010/asr",
        "http://127.0.0.1:9010/asr",
    ],
)
def test_asr_endpoint_accepts_tls_and_loopback_development(tmp_path, endpoint):
    with TestClient(make_app(tmp_path)) as client:
        response = client.post(
            "/api/settings/model-configs",
            json={"name": "valid", "settings": {"cloud_stt_endpoint": endpoint}},
        )
        assert response.status_code == 201, response.text
        assert response.json()["settings"]["cloud_stt_endpoint"] == endpoint


@pytest.mark.parametrize(
    "endpoint",
    [
        "ws://speech.example/asr",
        "http://speech.example/asr",
        "wss://user:private-value@speech.example/asr",
        "wss://speech.example/asr?token=private-value",
        "wss://speech.example/asr#private-value",
        "wss://speech.example:70000/asr",
        "file:///private-value",
        "wss:///private-value",
    ],
)
def test_asr_endpoint_rejects_insecure_or_secret_urls_without_echo(tmp_path, endpoint):
    with TestClient(make_app(tmp_path)) as client:
        for settings in ({"cloud_stt_endpoint": endpoint}, {"stt": {"cloud_endpoint": endpoint}}):
            response = client.post(
                "/api/settings/model-configs", json={"name": "invalid", "settings": settings}
            )
            assert response.status_code == 422
            assert "private-value" not in response.text
        assert not (tmp_path / ".model-configs.json").exists()


def test_llm_endpoint_still_rejects_websocket_urls(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        response = client.post(
            "/api/settings/model-configs",
            json={"name": "invalid", "settings": {"llm_base_url": "wss://speech.example/asr"}},
        )
        assert response.status_code == 422


def test_older_profile_store_loads_defaults_for_new_fields(tmp_path):
    app = make_app(tmp_path)
    with TestClient(app) as client:
        assert client.post("/api/settings/model-configs", json={"name": "old"}).status_code == 201
    path = tmp_path / ".model-configs.json"
    previous = json.loads(path.read_text(encoding="utf-8"))
    for item in [*previous["configs"], previous["active"]]:
        for field in NEW_FIELDS:
            item["settings"].pop(field, None)
    path.write_text(json.dumps(previous), encoding="utf-8")
    restarted = make_app(tmp_path)
    with TestClient(restarted) as client:
        listing = client.get("/api/settings/model-configs").json()
        assert listing["store_available"] is True
        for profile in listing["configs"]:
            settings = profile["settings"]
            assert settings["cloud_stt_protocol"] == "http-json"
            assert settings["cloud_stt_auth_mode"] == "api-key"
            assert settings["cloud_stt_timeout_ms"] == 5000
            assert settings["cloud_stt_enable_nonstream"] is True


def fake_probe(monkeypatch, callback):
    # Probe tests remain independent of transport internals and never contact a service.
    import sys

    monkeypatch.setitem(
        sys.modules,
        "app.infrastructure.speech_websocket",
        SimpleNamespace(probe_websocket=callback),
    )


def test_asr_probe_uses_saved_asr_key_independent_timeout_and_does_not_save(tmp_path, monkeypatch):
    captured = []

    async def probe(config):
        captured.append(config)
        return {"ok": True, "latency_ms": 23, "message": "private-upstream-data"}

    fake_probe(monkeypatch, probe)
    app = make_app(tmp_path)
    with TestClient(app) as client:
        saved = client.post(
            "/api/settings/model-configs",
            json={"name": "ASR", "settings": cloud_fields(llm_api_key="different-llm-key")},
        ).json()
        before = (tmp_path / ".model-configs.json").read_bytes()
        active = app.state.model_configs.active_snapshot()
        response = client.post(
            "/api/settings/model-configs/test",
            json={
                "target": "stt",
                "id": saved["id"],
                "settings": {"cloud_stt_access_token": "", "llm_timeout_ms": 100},
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["ok"] is True
        assert response.json()["verified"] == "network"
        assert response.json()["latency_ms"] == 23
        assert "private-upstream-data" not in response.text
        assert "asr-private-key" not in response.text
        assert len(captured) == 1
        config = captured[0]
        assert config.protocol == "volcengine-ws"
        assert config.auth_mode == "api-key"
        assert config.timeout_seconds == 7
        assert config.access_token == "asr-private-key"
        assert config.resource_id == "volc.seedasr.sauc.duration"
        assert config.enable_nonstream is False
        assert (tmp_path / ".model-configs.json").read_bytes() == before
        assert app.state.model_configs.active_snapshot() == active


@pytest.mark.parametrize(
    ("fields", "code"),
    [
        ({"cloud_opt_in": False}, "CLOUD_OPT_IN_REQUIRED"),
        ({"cloud_stt_access_token": "", "llm_api_key": "llm-key-only"}, "CONFIG_INCOMPLETE"),
        ({"cloud_stt_resource_id": ""}, "CONFIG_INCOMPLETE"),
        ({"cloud_stt_auth_mode": "app-token", "cloud_stt_app_id": ""}, "CONFIG_INCOMPLETE"),
    ],
)
def test_asr_probe_requires_opt_in_and_independent_credentials(tmp_path, monkeypatch, fields, code):
    async def must_not_probe(config):
        raise AssertionError("Incomplete/unapproved configuration must not send cloud audio")

    fake_probe(monkeypatch, must_not_probe)
    app = make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/settings/model-configs/test",
            json={"target": "stt", "settings": cloud_fields(**fields)},
        )
        assert response.status_code == 200, response.text
        assert response.json()["ok"] is False
        assert response.json()["verified"] == "none"
        assert response.json()["error_code"] == code
        assert not (tmp_path / ".model-configs.json").exists()


@pytest.mark.parametrize(
    "result, expected",
    [
        ({"error_code": "TIMEOUT"}, {"error_code": "TIMEOUT"}),
        (
            {"error_code": "HTTP_ERROR", "http_status": 401},
            {"error_code": "HTTP_ERROR", "http_status": 401},
        ),
        (
            {"error_code": "PROVIDER_ERROR", "provider_error_code": 45000081},
            {"error_code": "PROVIDER_ERROR", "provider_error_code": 45000081},
        ),
        ({"error_code": "private-untrusted-error"}, {"error_code": "UNKNOWN_ERROR"}),
        (
            {"error_code": "PROVIDER_ERROR", "provider_error_code": "private-untrusted-error"},
            {"error_code": "PROVIDER_ERROR"},
        ),
    ],
)
def test_asr_probe_exposes_only_controlled_diagnostics(tmp_path, monkeypatch, result, expected):
    async def probe(config):
        return {"ok": False, "message": "private-upstream-secret", "latency_ms": 17, **result}

    fake_probe(monkeypatch, probe)
    with TestClient(make_app(tmp_path)) as client:
        response = client.post(
            "/api/settings/model-configs/test", json={"target": "stt", "settings": cloud_fields()}
        )
        public = response.json()
        assert public["ok"] is False
        assert public["verified"] == "none"
        assert public["latency_ms"] == 17
        assert "private-" not in response.text
        for field, value in expected.items():
            assert public[field] == value
        for field in {"http_status", "provider_error_code"} - expected.keys():
            assert field not in public


def test_asr_probe_disabling_saved_cloud_opt_in_does_not_verify_local_fallback(
    tmp_path, monkeypatch
):
    async def must_not_probe(config):
        raise AssertionError("Privacy-disabled draft must not reach cloud")

    fake_probe(monkeypatch, must_not_probe)
    with TestClient(make_app(tmp_path)) as client:
        saved = client.post(
            "/api/settings/model-configs",
            json={"name": "ASR", "settings": cloud_fields()},
        ).json()
        before = (tmp_path / ".model-configs.json").read_bytes()
        response = client.post(
            "/api/settings/model-configs/test",
            json={"id": saved["id"], "target": "stt", "settings": {"cloud_opt_in": False}},
        )
        assert response.json()["ok"] is False
        assert response.json()["verified"] == "none"
        assert response.json()["error_code"] == "CLOUD_OPT_IN_REQUIRED"
        assert (tmp_path / ".model-configs.json").read_bytes() == before


@pytest.mark.parametrize("empty_audio", [True, False, 1, "true", None])
def test_asr_probe_empty_audio_hint_accepts_only_true_bool(tmp_path, monkeypatch, empty_audio):
    async def probe(config):
        return {
            "ok": True,
            "latency_ms": 12,
            "probe_empty_audio": empty_audio,
            "message": "private-upstream-secret",
            "error_code": "private-error",
            "audio": "private-audio",
        }

    fake_probe(monkeypatch, probe)
    with TestClient(make_app(tmp_path)) as client:
        response = client.post(
            "/api/settings/model-configs/test", json={"target": "stt", "settings": cloud_fields()}
        )
        public = response.json()
        assert public["ok"] is True
        assert public["verified"] == "network"
        assert "private-" not in response.text
        assert "audio" not in public
        assert "error_code" not in public
        if empty_audio is True:
            assert public["probe_empty_audio"] is True
            assert public["message"] == (
                "语音服务鉴权与协议可达；静音探测返回空音频，实际识别效果请在工作台朗读验证。"
            )
        else:
            assert "probe_empty_audio" not in public
            assert public["message"] == "流式语音握手与协议响应验证成功；未验证识别准确率。"
