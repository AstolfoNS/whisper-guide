import asyncio
import json

import httpx
import pytest

from app.infrastructure.llm import LLMSemanticRefiner, SemanticRefinement


@pytest.mark.parametrize(
    "base, endpoint",
    [
        ("https://example.test/v1", "https://example.test/v1/chat/completions"),
        ("https://example.test/api/v3", "https://example.test/api/v3/chat/completions"),
        ("http://localhost:11434", "http://localhost:11434/v1/chat/completions"),
        ("https://example.test/chat/completions", "https://example.test/chat/completions"),
    ],
)
def test_chat_protocol_uses_free_model_and_versioned_base_url(base, endpoint):
    async def scenario():
        async def handler(request):
            assert str(request.url) == endpoint
            assert request.headers["Authorization"] == "Bearer private"
            payload = json.loads(request.content)
            assert payload["model"] == "any-custom-model"
            context = json.loads(payload["messages"][1]["content"])
            assert context == {"script_window": "大家好，欢迎。", "rough_transcript": "大甲好"}
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {"matched_quote": "大家好", "confidence": 0.9}
                                )
                            }
                        }
                    ]
                },
            )

        refiner = LLMSemanticRefiner(
            align_mode="hybrid",
            base_url=base,
            model="any-custom-model",
            api_key="private",
            client_factory=lambda **kwargs: httpx.AsyncClient(
                transport=httpx.MockTransport(handler), **kwargs
            ),
        )
        assert await refiner.refine("大家好，欢迎。", "大甲好") == SemanticRefinement("大家好", 0.9)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "payload",
    [
        {"matched_quote": "不在讲稿中", "confidence": 1},
        {"matched_quote": "大家好", "confidence": True},
        {"matched_quote": "大家好", "confidence": float("nan")},
        {"matched_quote": "大家好", "confidence": 9},
        {"choices": [{"message": {"content": "大家好"}}]},
        {"choices": []},
    ],
)
def test_untrusted_model_output_is_rejected(payload):
    async def scenario():
        refiner = LLMSemanticRefiner(
            align_mode="fallback",
            base_url="https://example.test/v1",
            model="model",
            client_factory=lambda **kwargs: httpx.AsyncClient(
                transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
                **kwargs,
            ),
        )
        assert await refiner.refine("大家好", "错误识别") is None

    asyncio.run(scenario())


def test_total_timeout_returns_local_fallback_and_never_leaks_request_secret():
    async def scenario():
        async def handler(request):
            await asyncio.sleep(1)
            return httpx.Response(200, json={"matched_quote": "大家好", "confidence": 1})

        refiner = LLMSemanticRefiner(
            align_mode="hybrid",
            base_url="https://example.test/api/v3",
            model="model",
            api_key="sensitive",
            timeout_ms=10,
            client_factory=lambda **kwargs: httpx.AsyncClient(
                transport=httpx.MockTransport(handler), **kwargs
            ),
        )
        assert await refiner.refine("大家好", "错误识别") is None
        result = await refiner.test_connection()
        assert result["ok"] is False
        assert "sensitive" not in result["message"]
        assert result["latency_ms"] < 300

    asyncio.run(scenario())
