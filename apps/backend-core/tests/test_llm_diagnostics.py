"""Connection failures remain actionable without exposing upstream credentials."""

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.infrastructure.llm import LLMSemanticRefiner
from app.main import create_app

PRIVATE = "private-value-never-echo"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 503])
def test_llm_probe_classifies_http_without_echoing_provider_body(status):
    async def scenario():
        refiner = LLMSemanticRefiner(
            base_url="https://example.test/v1",
            model="model",
            api_key=PRIVATE,
            client_factory=lambda **kwargs: httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        status,
                        json={"error": {"message": PRIVATE}},
                        headers={"x-request-id": PRIVATE},
                    )
                ),
                **kwargs,
            ),
        )
        result = await refiner.test_connection()
        assert result["ok"] is False
        assert result["error_code"] == "HTTP_ERROR"
        assert result["http_status"] == status
        assert PRIVATE not in str(result)
        assert "example.test" not in str(result)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure, code",
    [
        (httpx.ConnectTimeout(PRIVATE), "TIMEOUT"),
        (httpx.ReadTimeout(PRIVATE), "TIMEOUT"),
        (TimeoutError(PRIVATE), "TIMEOUT"),
        (httpx.ConnectError(PRIVATE), "NETWORK_ERROR"),
        (httpx.ProxyError(PRIVATE), "NETWORK_ERROR"),
        (RuntimeError(PRIVATE), "UNKNOWN_ERROR"),
    ],
)
def test_llm_probe_classifies_transport_failure_without_exception_text(failure, code):
    async def scenario():
        def handler(request):
            raise failure

        refiner = LLMSemanticRefiner(
            base_url="https://example.test/v1",
            model="model",
            client_factory=lambda **kwargs: httpx.AsyncClient(
                transport=httpx.MockTransport(handler), **kwargs
            ),
        )
        result = await refiner.test_connection()
        assert result["ok"] is False
        assert result["error_code"] == code
        assert "http_status" not in result
        assert PRIVATE not in str(result)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "body", ["not-json", "{}", '{"choices": []}', '["private-value-never-echo"]']
)
def test_llm_probe_classifies_invalid_response(body):
    async def scenario():
        refiner = LLMSemanticRefiner(
            base_url="https://example.test/v1",
            model="model",
            client_factory=lambda **kwargs: httpx.AsyncClient(
                transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body)),
                **kwargs,
            ),
        )
        result = await refiner.test_connection()
        assert result["error_code"] == "INVALID_RESPONSE"
        assert PRIVATE not in str(result)

    asyncio.run(scenario())


def test_llm_probe_distinguishes_missing_configuration_without_network():
    def factory(**kwargs):
        raise AssertionError("Incomplete configuration must not make requests")

    result = asyncio.run(LLMSemanticRefiner(client_factory=factory).test_connection())
    assert result["error_code"] == "CONFIG_INCOMPLETE"
    assert result["latency_ms"] == 0


@pytest.mark.parametrize("path", ["/api/settings/models/test", "/api/settings/model-configs/test"])
@pytest.mark.parametrize(
    "code, status, expected",
    [
        ("CONFIG_INCOMPLETE", None, "配置不完整"),
        ("TIMEOUT", None, "超时"),
        ("NETWORK_ERROR", None, "网络"),
        ("INVALID_RESPONSE", None, "响应格式"),
        ("UNKNOWN_ERROR", None, "运行环境"),
        ("HTTP_ERROR", 400, "请求参数"),
        ("HTTP_ERROR", 401, "API Key"),
        ("HTTP_ERROR", 403, "权限"),
        ("HTTP_ERROR", 404, "模型 ID"),
        ("HTTP_ERROR", 429, "额度"),
        ("HTTP_ERROR", 503, "稍后重试"),
        ("HTTP_ERROR", 418, "接口配置"),
    ],
)
def test_llm_api_exposes_only_controlled_diagnostics(
    tmp_path, monkeypatch, path, code, status, expected
):
    async def fake_test(refiner):
        return {
            "ok": False,
            "latency_ms": 207,
            "error_code": code,
            "http_status": status,
            "message": PRIVATE,
            "headers": {"Authorization": PRIVATE},
            "url": f"https://user:{PRIVATE}@example.test",
            "body": {"error": PRIVATE},
        }

    monkeypatch.setattr(LLMSemanticRefiner, "test_connection", fake_test)
    settings = Settings(environment="test", data_dir=tmp_path, _env_file=None)
    with TestClient(create_app(settings)) as client:
        response = client.post(path, json={})
        assert response.status_code == 200
        assert PRIVATE not in response.text
        assert "example.test" not in response.text
        result = response.json()
        assert result["ok"] is False
        assert result["latency_ms"] == 207
        assert result["error_code"] == code
        assert expected in result["message"]
        if status is not None:
            assert result["http_status"] == status
        else:
            assert "http_status" not in result
        if "model-configs" in path:
            assert result["verified"] == "none"


@pytest.mark.parametrize("status", [True, "401", 99, 600, None, {"private": PRIVATE}])
def test_llm_api_rejects_untrusted_http_status_metadata(tmp_path, monkeypatch, status):
    async def fake_test(refiner):
        return {"ok": False, "error_code": "HTTP_ERROR", "http_status": status}

    monkeypatch.setattr(LLMSemanticRefiner, "test_connection", fake_test)
    with TestClient(
        create_app(Settings(environment="test", data_dir=tmp_path, _env_file=None))
    ) as client:
        response = client.post("/api/settings/model-configs/test", json={})
        assert "http_status" not in response.json()
        assert PRIVATE not in response.text


@pytest.mark.parametrize("code", [PRIVATE, None, [PRIVATE], {"secret": PRIVATE}])
def test_llm_api_rejects_unknown_error_codes(tmp_path, monkeypatch, code):
    async def fake_test(refiner):
        return {
            "ok": False,
            "error_code": code,
            "http_status": 401,
            "latency_ms": True,
            "message": PRIVATE,
        }

    monkeypatch.setattr(LLMSemanticRefiner, "test_connection", fake_test)
    with TestClient(
        create_app(Settings(environment="test", data_dir=tmp_path, _env_file=None))
    ) as client:
        response = client.post("/api/settings/model-configs/test", json={})
        assert response.json()["error_code"] == "UNKNOWN_ERROR"
        assert response.json()["latency_ms"] == 0
        assert "http_status" not in response.json()
        assert PRIVATE not in response.text
