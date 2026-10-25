"""Read-only catalog discovery normalizes providers without exposing credentials."""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import app.infrastructure.model_catalog as catalog
from app.config import Settings
from app.main import create_app

PRIVATE = "private-discovery-token"
ENDPOINT = "/api/settings/model-configs/discover-models"


def install_transport(monkeypatch, handler, captured=None):
    original = httpx.AsyncClient

    def factory(**kwargs):
        if captured is not None:
            captured.append(kwargs)
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(catalog.httpx, "AsyncClient", factory)


def make_app(tmp_path, **values):
    return create_app(Settings(environment="test", data_dir=tmp_path, _env_file=None, **values))


@pytest.mark.parametrize(
    "base, expected",
    [
        ("https://provider.test", "https://provider.test/v1/models"),
        ("https://provider.test/", "https://provider.test/v1/models"),
        ("https://provider.test/api/v3/", "https://provider.test/api/v3/models"),
        (
            "https://provider.test/compatible-mode/v1",
            "https://provider.test/compatible-mode/v1/models",
        ),
        ("https://provider.test/api/v3/chat/completions", "https://provider.test/api/v3/models"),
        ("https://provider.test/chat/completions", "https://provider.test/models"),
        ("http://localhost:11434/v1", "http://localhost:11434/v1/models"),
    ],
)
def test_catalog_request_uses_same_api_root_and_only_get(tmp_path, monkeypatch, base, expected):
    captured = []

    def handler(request):
        assert request.method == "GET"
        assert str(request.url) == expected
        assert request.headers["Authorization"] == f"Bearer {PRIVATE}"
        assert request.content == b""
        return httpx.Response(200, json={"data": [{"id": "model-1", "owned_by": "private"}]})

    install_transport(monkeypatch, handler, captured)
    with TestClient(make_app(tmp_path)) as client:
        response = client.post(
            ENDPOINT,
            json={"settings": {"llm_base_url": base, "llm_api_key": PRIVATE}},
        )
        assert response.status_code == 200
        assert response.json()["ok"] is True
        assert response.json()["models"] == [{"id": "model-1"}]
        assert PRIVATE not in response.text
    assert captured == [{"timeout": 1.5, "follow_redirects": False}]


@pytest.mark.parametrize(
    "body, expected",
    [
        ({"data": []}, []),
        ([], []),
        ({"models": []}, []),
        ({"data": [{"id": "a", "name": "Alpha"}, {"id": "a"}]}, [{"id": "a", "name": "Alpha"}]),
        (["a", "b", "a"], [{"id": "a"}, {"id": "b"}]),
        ({"models": [{"model": "qwen:7b", "name": "Qwen"}]}, [{"id": "qwen:7b", "name": "Qwen"}]),
        ({"models": [{"name": "qwen:7b"}]}, [{"id": "qwen:7b"}]),
        ({"data": [{"id": "m", "display_name": "Model"}]}, [{"id": "m", "name": "Model"}]),
        ({"data": [{"name": "missing-id"}, {"id": ""}, {"id": None}, 3, True]}, []),
    ],
)
def test_catalog_formats_deduplication_and_empty_list(tmp_path, monkeypatch, body, expected):
    install_transport(monkeypatch, lambda request: httpx.Response(200, json=body))
    with TestClient(make_app(tmp_path)) as client:
        response = client.post(ENDPOINT, json={"settings": {"llm_base_url": "http://local.test"}})
        result = response.json()
        assert result["ok"] is True
        assert result["models"] == expected
        assert result["truncated"] is False
        assert "为空" in result["message"] if not expected else "仍需测试" in result["message"]


def test_catalog_keeps_stored_key_without_activating_or_saving_draft(tmp_path, monkeypatch):
    seen = []

    def handler(request):
        seen.append((str(request.url), request.headers.get("Authorization")))
        return httpx.Response(200, json={"data": [{"id": "discovered"}]})

    install_transport(monkeypatch, handler)
    app = make_app(tmp_path, llm_api_key="active-credential")
    with TestClient(app) as client:
        profile = client.post(
            "/api/settings/model-configs",
            json={"name": "Saved", "settings": {"llm_api_key": PRIVATE}},
        ).json()
        before = app.state.settings.model_configs_path.read_bytes()
        for fields, expected_key in (
            ({"llm_api_key": ""}, f"Bearer {PRIVATE}"),
            ({"llm_api_key": "****"}, f"Bearer {PRIVATE}"),
            ({"clear_llm_api_key": True}, None),
            ({"llm_api_key": "replacement"}, "Bearer replacement"),
        ):
            response = client.post(
                ENDPOINT,
                json={
                    "id": profile["id"],
                    "settings": {"llm_base_url": "https://draft.test/api/v3", **fields},
                },
            )
            assert response.json()["ok"] is True
            assert seen[-1] == ("https://draft.test/api/v3/models", expected_key)
        assert app.state.settings.model_configs_path.read_bytes() == before
        assert app.state.settings.llm_api_key == "active-credential"
        assert app.state.model_configs.get(profile["id"])["settings"]["llm_api_key"] == PRIVATE
        client.post(ENDPOINT, json={"settings": {"llm_base_url": "https://new.test"}})
        assert seen[-1] == ("https://new.test/v1/models", None)


def test_catalog_existing_profile_can_discover_without_new_fields(tmp_path, monkeypatch):
    install_transport(monkeypatch, lambda request: httpx.Response(200, json={"data": ["m"]}))
    with TestClient(make_app(tmp_path)) as client:
        profile = client.post(
            "/api/settings/model-configs",
            json={"name": "Saved", "settings": {"llm_base_url": "https://saved.test/v1"}},
        ).json()
        assert client.post(ENDPOINT, json={"id": profile["id"]}).json()["ok"] is True


@pytest.mark.parametrize(
    "url",
    ["https://user:secret@provider.test", "https://provider.test?token=secret", "file:///secret"],
)
def test_catalog_rejects_credential_urls_without_echo(tmp_path, url):
    with TestClient(make_app(tmp_path)) as client:
        response = client.post(ENDPOINT, json={"settings": {"llm_base_url": url}})
        assert response.status_code == 422
        assert "secret" not in response.text


def test_catalog_missing_base_url_and_profile_do_not_request(tmp_path, monkeypatch):
    def factory(**kwargs):
        raise AssertionError("No request expected")

    monkeypatch.setattr(catalog.httpx, "AsyncClient", factory)
    with TestClient(make_app(tmp_path)) as client:
        response = client.post(ENDPOINT, json={})
        assert response.json()["error_code"] == "CONFIG_INCOMPLETE"
        assert "Base URL" in response.json()["message"]
        assert client.post(ENDPOINT, json={"id": "missing"}).status_code == 404


def test_catalog_removes_secret_echoes_invalid_values_and_upstream_metadata(tmp_path, monkeypatch):
    body = {
        "data": [
            {"id": PRIVATE, "name": "key"},
            {"id": f"prefix-{PRIVATE}-suffix"},
            {"id": "safe", "name": PRIVATE, "headers": PRIVATE},
            {"id": "normal", "name": "Normal", "api_key": PRIVATE},
            {"id": "bad\u0000id"},
            {"id": "x" * 257},
            {"id": "unpaired\ud800"},
        ],
        "token": PRIVATE,
    }
    install_transport(monkeypatch, lambda request: httpx.Response(200, content=json.dumps(body)))
    with TestClient(make_app(tmp_path)) as client:
        response = client.post(
            ENDPOINT,
            json={"settings": {"llm_base_url": "https://provider.test", "llm_api_key": PRIVATE}},
        )
        assert response.json()["models"] == [{"id": "safe"}, {"id": "normal", "name": "Normal"}]
        assert PRIVATE not in response.text
        assert "headers" not in response.text


@pytest.mark.parametrize("status", [301, 302, 400, 401, 403, 404, 405, 429, 503])
def test_catalog_http_errors_have_safe_diagnostics_and_do_not_redirect(
    tmp_path, monkeypatch, status
):
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(
            status,
            headers={"Location": "https://other.test/models", "X-Request-ID": PRIVATE},
            json={"error": {"message": PRIVATE}},
        )

    install_transport(monkeypatch, handler)
    with TestClient(make_app(tmp_path)) as client:
        response = client.post(ENDPOINT, json={"settings": {"llm_base_url": "https://p.test"}})
        result = response.json()
        assert result["ok"] is False
        assert result["models"] == []
        assert result["error_code"] == "HTTP_ERROR"
        assert result["http_status"] == status
        assert PRIVATE not in response.text
        assert "other.test" not in response.text
        if status in {404, 405}:
            assert "手动填写" in result["message"]
    assert requests == ["https://p.test/v1/models"]


@pytest.mark.parametrize(
    "failure, code",
    [
        (httpx.ReadTimeout(PRIVATE), "TIMEOUT"),
        (httpx.ConnectError(PRIVATE), "NETWORK_ERROR"),
        (RuntimeError(PRIVATE), "UNKNOWN_ERROR"),
    ],
)
def test_catalog_transport_errors_do_not_echo_exception(tmp_path, monkeypatch, failure, code):
    def handler(request):
        raise failure

    install_transport(monkeypatch, handler)
    with TestClient(make_app(tmp_path)) as client:
        response = client.post(ENDPOINT, json={"settings": {"llm_base_url": "https://p.test"}})
        assert response.json()["error_code"] == code
        assert PRIVATE not in response.text


@pytest.mark.parametrize("body", ["not-json", "{}", '{"data": {}}', "null"])
def test_catalog_invalid_bodies_allow_manual_model_entry(tmp_path, monkeypatch, body):
    install_transport(monkeypatch, lambda request: httpx.Response(200, text=body))
    with TestClient(make_app(tmp_path)) as client:
        response = client.post(ENDPOINT, json={"settings": {"llm_base_url": "https://p.test"}})
        assert response.json()["error_code"] == "INVALID_RESPONSE"
        assert "手动填写" in response.json()["message"]


@pytest.mark.parametrize("announced_size", [True, False])
def test_catalog_bounds_response_size_with_and_without_content_length(
    tmp_path, monkeypatch, announced_size
):
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(3):
                yield b"x" * 64

    monkeypatch.setattr(catalog, "MAX_RESPONSE_BYTES", 100)
    install_transport(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            headers={"Content-Length": "192"} if announced_size else {},
            stream=Stream(),
        ),
    )
    with TestClient(make_app(tmp_path)) as client:
        response = client.post(ENDPOINT, json={"settings": {"llm_base_url": "https://p.test"}})
        assert response.json()["error_code"] == "RESPONSE_TOO_LARGE"
        assert response.json()["models"] == []
        assert "大小限制" in response.json()["message"]


def test_catalog_caps_unique_entries_and_marks_truncation(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "MAX_MODELS", 2)
    install_transport(
        monkeypatch,
        lambda request: httpx.Response(200, json={"data": ["a", "a", "b", "c"]}),
    )
    with TestClient(make_app(tmp_path)) as client:
        result = client.post(ENDPOINT, json={"settings": {"llm_base_url": "https://p.test"}}).json()
        assert result["models"] == [{"id": "a"}, {"id": "b"}]
        assert result["truncated"] is True


def test_catalog_total_timeout_bounds_slow_responses_and_setting(tmp_path):
    async def handler(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json={"data": []})

    captured = []

    def factory(**kwargs):
        captured.append(kwargs)
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    settings = Settings(
        environment="test",
        data_dir=tmp_path,
        _env_file=None,
        llm_base_url="https://p.test",
        llm_timeout_ms=10,
    )
    result = asyncio.run(catalog.discover_models(settings, client_factory=factory))
    assert result["error_code"] == "TIMEOUT"
    assert result["latency_ms"] < 300
    settings.llm_timeout_ms = 999999
    original = catalog.MAX_DISCOVERY_SECONDS
    try:
        catalog.MAX_DISCOVERY_SECONDS = 0.01
        result = asyncio.run(catalog.discover_models(settings, client_factory=factory))
        assert result["error_code"] == "TIMEOUT"
        assert captured[-1]["timeout"] == 0.01
    finally:
        catalog.MAX_DISCOVERY_SECONDS = original
