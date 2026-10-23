"""Vendor-neutral semantic refinement for speech/script alignment."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, ClassVar, Mapping

import httpx


@dataclass(frozen=True, slots=True)
class SemanticRefinement:
    """A validated semantic alignment suggestion from an LLM."""

    matched_quote: str
    confidence: float


class LLMSemanticRefiner:
    """Refine speech text with any OpenAI-compatible chat completion model."""

    PRESETS: ClassVar[dict[str, dict[str, str]]] = {
        "custom": {"label": "自定义（OpenAI 兼容）", "base_url": "", "model": ""},
        "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
        "qwen": {"label": "通义千问", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-turbo"},
        "doubao": {"label": "豆包 / 火山方舟", "base_url": "https://ark.cn-beijing.volces.com/api/v3", "model": "doubao-1-5-lite-32k"},
        "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
        "ollama": {"label": "Ollama（局域网）", "base_url": "http://127.0.0.1:11434/v1", "model": "qwen2.5:7b"},
    }

    def __init__(
        self,
        *,
        align_mode: str = "off",
        provider_preset: str = "custom",
        base_url: str = "",
        model: str = "",
        api_key: str = "",
        timeout_ms: int = 1500,
        client_factory: Any | None = None,
    ) -> None:
        self.align_mode = align_mode
        self.provider_preset = provider_preset
        self.base_url = base_url.strip()
        self.model = model.strip()
        self.api_key = api_key
        self.timeout_ms = max(1, int(timeout_ms))
        self._client_factory = client_factory or httpx.AsyncClient

    @classmethod
    def from_settings(cls, settings: Any) -> "LLMSemanticRefiner":
        return cls(
            align_mode=str(getattr(settings, "llm_align_mode", "off")),
            provider_preset=str(getattr(settings, "llm_provider_preset", "custom")),
            base_url=str(getattr(settings, "llm_base_url", "")),
            model=str(getattr(settings, "llm_model", "")),
            api_key=str(getattr(settings, "llm_api_key", "")),
            timeout_ms=int(getattr(settings, "llm_timeout_ms", 1500)),
        )

    @classmethod
    def presets(cls) -> list[dict[str, str]]:
        return [{"id": key, **value} for key, value in cls.PRESETS.items()]

    @classmethod
    def preset_defaults(cls, name: str) -> dict[str, str]:
        return dict(cls.PRESETS.get(name, cls.PRESETS["custom"]))

    @property
    def enabled(self) -> bool:
        return self.align_mode in {"fallback", "hybrid"} and bool(
            self.base_url and self.model and self.api_key
        )

    @property
    def endpoint(self) -> str:
        value = self.base_url.rstrip("/")
        if not value:
            return ""
        if value.endswith("/chat/completions"):
            return value
        if value.endswith("/v1"):
            return f"{value}/chat/completions"
        return f"{value}/v1/chat/completions"

    async def refine(
        self,
        script_window: str,
        rough_transcript: str,
        *,
        turn_token: int | str | None = None,
        current_turn_token: int | str | None = None,
    ) -> SemanticRefinement | None:
        window = script_window.strip()
        rough = rough_transcript.strip()
        if not window or not rough or not self.enabled:
            return None
        if (
            turn_token is not None
            and current_turn_token is not None
            and str(turn_token) != str(current_turn_token)
        ):
            return None
        payload = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": max(32, min(256, len(window) * 2)),
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是讲稿对齐器。只能从用户提供的讲稿窗口中复制连续原文，"
                        "不要改写、补充或解释。仅输出 JSON："
                        '{"matched_quote":"讲稿连续子串","confidence":0.0}'
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"script_window": window, "rough_transcript": rough},
                        ensure_ascii=False,
                    ),
                },
            ],
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            async with self._client_factory(timeout=self.timeout_ms / 1000) as client:
                response = await client.post(self.endpoint, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, TimeoutError, OSError, ValueError, TypeError):
            return None
        result = self._parse_response(data)
        if result is None:
            return None
        quote, confidence = result
        if quote not in window or confidence <= 0:
            return None
        if (
            turn_token is not None
            and current_turn_token is not None
            and str(turn_token) != str(current_turn_token)
        ):
            return None
        return SemanticRefinement(quote, confidence)

    async def align(self, *args: Any, **kwargs: Any) -> SemanticRefinement | None:
        return await self.refine(*args, **kwargs)

    async def test_connection(self) -> dict[str, Any]:
        if not self.base_url or not self.model or not self.api_key:
            return {"ok": False, "latency_ms": 0, "message": "LLM 配置不完整。"}
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        payload = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 4,
            "messages": [{"role": "user", "content": "回复 OK"}],
        }
        started = time.perf_counter()
        try:
            async with self._client_factory(timeout=self.timeout_ms / 1000) as client:
                response = await client.post(self.endpoint, headers=headers, json=payload)
                response.raise_for_status()
        except (httpx.HTTPError, TimeoutError, OSError, ValueError, TypeError) as error:
            elapsed = int((time.perf_counter() - started) * 1000)
            return {"ok": False, "latency_ms": elapsed, "message": _safe_error(error)}
        elapsed = int((time.perf_counter() - started) * 1000)
        return {"ok": True, "latency_ms": elapsed, "message": "连接成功。"}

    @staticmethod
    def _parse_response(data: Any) -> tuple[str, float] | None:
        if not isinstance(data, Mapping):
            return None
        content: Any = data.get("matched_quote")
        confidence: Any = data.get("confidence")
        if content is None:
            choices = data.get("choices")
            if isinstance(choices, list) and choices:
                first = choices[0]
                if isinstance(first, Mapping):
                    message = first.get("message", first)
                    content = message.get("content") if isinstance(message, Mapping) else None
        if isinstance(content, str):
            parsed = _parse_json_object(content)
            if parsed is not None:
                content = parsed.get("matched_quote")
                confidence = parsed.get("confidence", confidence)
        if not isinstance(content, str):
            return None
        try:
            score = min(1.0, max(0.0, float(confidence)))
        except (TypeError, ValueError):
            return None
        quote = content.strip()
        return (quote, score) if quote else None


def _parse_json_object(content: str) -> Mapping[str, Any] | None:
    value = content.strip()
    fence = chr(96) * 3
    if value.startswith(fence):
        pattern = r"^" + fence + r"(?:json)?\s*|\s*" + fence + "$"
        value = re.sub(pattern, "", value, flags=re.IGNORECASE)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, Mapping) else None


def _safe_error(error: Exception) -> str:
    message = str(error).strip().replace("\n", " ")
    return message[:200] or "连接失败。"


__all__ = ["LLMSemanticRefiner", "SemanticRefinement"]
