"""Bounded, read-only discovery for OpenAI-compatible model catalogs."""

from __future__ import annotations

import asyncio
import json
import time
import unicodedata
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from app.config import Settings
from app.infrastructure.llm import LLMSemanticRefiner, connection_error_code

MAX_RESPONSE_BYTES = 1024 * 1024
MAX_MODELS = 500
MAX_SCANNED_ITEMS = 5000
MAX_DISCOVERY_SECONDS = 30


class CatalogTooLargeError(ValueError):
    """The upstream body exceeds the in-memory discovery limit."""


def _catalog_endpoint(base_url: str) -> str:
    value = base_url.strip().rstrip("/")
    parts = urlsplit(value)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise ValueError("Invalid catalog endpoint")
    # Use the same API root as chat completions, including explicitly supplied
    # completion URLs and non-v1 service roots such as Ark's /api/v3.
    completion = LLMSemanticRefiner(base_url=value).endpoint
    return completion.removesuffix("/chat/completions") + "/models"


def _safe_text(value: Any, secrets: tuple[str, ...], limit: int = 256) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > limit:
        return None
    if any(unicodedata.category(char).startswith("C") for char in text):
        return None
    if any(secret in text or quote(secret, safe="") in text for secret in secrets):
        return None
    return text


def _normalize_models(data: Any, secrets: tuple[str, ...]) -> tuple[list[dict[str, str]], bool]:
    ollama = False
    if isinstance(data, list):
        entries = data
    elif isinstance(data, Mapping) and isinstance(data.get("data"), list):
        entries = data["data"]
    elif isinstance(data, Mapping) and isinstance(data.get("models"), list):
        entries, ollama = data["models"], True
    else:
        raise ValueError("Invalid catalog response")
    models: list[dict[str, str]] = []
    seen: set[str] = set()
    truncated = len(entries) > MAX_SCANNED_ITEMS
    for entry in entries[:MAX_SCANNED_ITEMS]:
        identifier: Any = entry
        name: Any = None
        if isinstance(entry, Mapping):
            identifier = entry.get("id")
            if ollama and identifier is None:
                identifier = entry.get("model", entry.get("name"))
            name = entry.get("display_name", entry.get("name"))
        identifier = _safe_text(identifier, secrets)
        if identifier is None or identifier in seen:
            continue
        if len(models) == MAX_MODELS:
            truncated = True
            break
        seen.add(identifier)
        item = {"id": identifier}
        name = _safe_text(name, secrets)
        if name is not None and name != identifier:
            item["name"] = name
        models.append(item)
    return models, truncated


async def discover_models(
    settings: Settings, *, client_factory: Callable[..., Any] | None = None
) -> dict[str, Any]:
    """Never save settings, follow redirects, or return untrusted error text."""

    if not settings.llm_base_url.strip():
        return {"ok": False, "latency_ms": 0, "error_code": "CONFIG_INCOMPLETE"}
    started = time.perf_counter()
    timeout = min(settings.llm_timeout_ms / 1000, MAX_DISCOVERY_SECONDS)
    headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"
    secrets = tuple(
        value
        for value in (
            settings.llm_api_key,
            settings.cloud_stt_access_token,
            settings.cloud_access_token,
        )
        if value
    )
    try:
        endpoint = _catalog_endpoint(settings.llm_base_url)
        async with asyncio.timeout(timeout):
            async with (client_factory or httpx.AsyncClient)(
                timeout=timeout, follow_redirects=False
            ) as client:
                async with client.stream("GET", endpoint, headers=headers) as response:
                    response.raise_for_status()
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise ValueError("Compressed catalogs are not supported")
                    content_length = response.headers.get("content-length", "")
                    if content_length.isdigit() and int(content_length) > MAX_RESPONSE_BYTES:
                        raise CatalogTooLargeError
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                        if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise CatalogTooLargeError
                        body.extend(chunk)
        data = json.loads(body)
        models, truncated = _normalize_models(data, secrets)
    except Exception as error:
        result = {
            "ok": False,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "error_code": (
                "RESPONSE_TOO_LARGE"
                if isinstance(error, CatalogTooLargeError)
                else connection_error_code(error)
            ),
        }
        if isinstance(error, httpx.HTTPStatusError):
            result["http_status"] = error.response.status_code
        return result
    return {
        "ok": True,
        "models": models,
        "truncated": truncated,
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }
