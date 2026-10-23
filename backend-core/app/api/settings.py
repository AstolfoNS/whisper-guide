"""Runtime model settings.

GET returns public fields plus secret-presence flags; all secret hints are a
constant mask. PUT validates a clone, writes an atomic owner-only JSON file in
the SQLite data mount, then activates it. POST /test uses a transient clone and
never modifies active settings or the saved configuration.
"""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import Settings, clone_model_settings, persist_model_settings
from app.infrastructure.llm import LLMSemanticRefiner


class PrivateSettingsRoute(APIRoute):
    """Validation errors must not echo credential fields or credential URLs."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request: Request):
            try:
                return await original(request)
            except RequestValidationError as error:
                detail = [
                    {name: item[name] for name in ("loc", "msg", "type")} for item in error.errors()
                ]
                return JSONResponse(status_code=422, content={"detail": detail})

        return handle


router = APIRouter(prefix="/settings", tags=["settings"], route_class=PrivateSettingsRoute)
PresetName = Literal["custom", "deepseek", "qwen", "doubao", "openai", "ollama"]
AlignMode = Literal["off", "fallback", "hybrid"]
SpeechSource = Literal["local", "cloud"]


class LLMUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    align_mode: AlignMode | None = None
    provider_preset: PresetName | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    timeout_ms: int | None = Field(default=None, gt=0, strict=True)

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        return _validate_endpoint(value)


class STTUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: SpeechSource | None = None
    cloud_opt_in: bool | None = None
    cloud_endpoint: str | None = None
    cloud_app_id: str | None = None
    access_token: str | None = None
    cloud_stt_endpoint: str | None = None
    cloud_stt_app_id: str | None = None
    cloud_stt_access_token: str | None = None

    @field_validator("cloud_endpoint", "cloud_stt_endpoint")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        return _validate_endpoint(value)


class ModelSettingsUpdate(BaseModel):
    """Flat fields explicitly provided by the UI override nested fields."""

    model_config = ConfigDict(extra="ignore")

    llm: LLMUpdate | None = None
    stt: STTUpdate | None = None
    llm_align_mode: AlignMode | None = None
    llm_provider_preset: PresetName | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    api_key: str | None = None
    llm_timeout_ms: int | None = Field(default=None, gt=0, strict=True)
    stt_provider: SpeechSource | None = None
    cloud_opt_in: bool | None = None
    cloud_endpoint: str | None = None
    cloud_app_id: str | None = None
    cloud_access_token: str | None = None
    access_token: str | None = None
    cloud_stt_endpoint: str | None = None
    cloud_stt_app_id: str | None = None
    cloud_stt_access_token: str | None = None

    @field_validator("llm_base_url", "cloud_endpoint", "cloud_stt_endpoint")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        return _validate_endpoint(value)


def _validate_endpoint(value: str | None) -> str | None:
    if value is None or not value.strip():
        return value
    endpoint = value.strip()
    parts = urlsplit(endpoint)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise ValueError("接口地址必须为不含凭据或查询参数的 HTTP(S) URL。")
    return endpoint


def redact_secret(value: Any) -> str:
    """Never expose any part of a credential, including short credentials."""

    return "****" if value else ""


def _model_payload(settings: Settings) -> dict[str, Any]:
    token = settings.cloud_stt_access_token or settings.cloud_access_token
    endpoint = settings.cloud_stt_endpoint or settings.cloud_endpoint
    app_id = settings.cloud_stt_app_id or settings.cloud_app_id
    key = settings.llm_api_key
    key_hint = redact_secret(key)
    token_hint = redact_secret(token)
    presets = LLMSemanticRefiner.presets()
    llm = {
        "align_mode": settings.llm_align_mode,
        "provider_preset": settings.llm_provider_preset,
        "base_url": settings.llm_base_url,
        "model": settings.llm_model,
        "api_key": key_hint,
        "has_api_key": bool(key),
        "timeout_ms": settings.llm_timeout_ms,
        "enabled": LLMSemanticRefiner.from_settings(settings).enabled,
    }
    stt = {
        "provider": settings.stt_provider,
        "cloud_opt_in": settings.cloud_opt_in,
        "cloud_endpoint": endpoint,
        "cloud_stt_endpoint": endpoint,
        "cloud_app_id": app_id,
        "cloud_stt_app_id": app_id,
        "access_token": token_hint,
        "has_access_token": bool(token),
    }
    return {
        "stt": stt,
        "llm": llm,
        "stt_provider": settings.stt_provider,
        "cloud_opt_in": settings.cloud_opt_in,
        "cloud_stt_endpoint": endpoint,
        "cloud_stt_app_id": app_id,
        "cloud_stt_token_configured": bool(token),
        "cloud_stt_token_hint": token_hint,
        "llm_align_mode": settings.llm_align_mode,
        "llm_provider_preset": settings.llm_provider_preset,
        "llm_base_url": settings.llm_base_url,
        "llm_model": settings.llm_model,
        "llm_api_key": key_hint,
        "has_api_key": bool(key),
        "llm_api_key_configured": bool(key),
        "llm_api_key_hint": key_hint,
        "llm_timeout_ms": settings.llm_timeout_ms,
        "has_access_token": bool(token),
        "presets": presets,
        "llm_presets": presets,
        "configuration_source": "runtime",
    }


def _updates(payload: ModelSettingsUpdate) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if payload.llm:
        values.update(
            {
                f"llm_{name}": value
                for name, value in payload.llm.model_dump(exclude_none=True).items()
            }
        )
    if payload.stt:
        mapping = {
            "provider": "stt_provider",
            "cloud_opt_in": "cloud_opt_in",
            "cloud_endpoint": "cloud_stt_endpoint",
            "cloud_stt_endpoint": "cloud_stt_endpoint",
            "cloud_app_id": "cloud_stt_app_id",
            "cloud_stt_app_id": "cloud_stt_app_id",
            "access_token": "cloud_stt_access_token",
            "cloud_stt_access_token": "cloud_stt_access_token",
        }
        for name, value in payload.stt.model_dump(exclude_none=True).items():
            values[mapping[name]] = value
    aliases = {
        "api_key": "llm_api_key",
        "cloud_endpoint": "cloud_stt_endpoint",
        "cloud_app_id": "cloud_stt_app_id",
        "cloud_access_token": "cloud_stt_access_token",
        "access_token": "cloud_stt_access_token",
    }
    flat = payload.model_dump(exclude_none=True, exclude={"llm", "stt"})
    for name, value in flat.items():
        values[aliases.get(name, name)] = value
    # Empty or masked inputs mean "keep the stored credential".
    for name in ("llm_api_key", "cloud_stt_access_token"):
        if name in values:
            value = str(values[name]).strip()
            if not value or "****" in value:
                del values[name]
            else:
                values[name] = value
    return values


def _apply_update(payload: ModelSettingsUpdate, settings: Settings) -> Settings:
    return clone_model_settings(settings, _updates(payload))


def _activate_settings(application: Any, settings: Settings) -> None:
    application.state.settings = settings
    from app.infrastructure.speech import ProviderRegistry, ProviderSpeechServices

    current = getattr(application.state, "speech_services", None)
    if isinstance(current, ProviderSpeechServices):
        # Retain the already loaded local model and active session references.
        current.settings = settings
        current.provider_name = settings.stt_provider
        current.registry = ProviderRegistry(current, settings)


@router.get("/models")
def get_model_settings(request: Request) -> dict[str, Any]:
    return _model_payload(request.app.state.settings)


@router.put("/models")
def update_model_settings(request: Request, payload: ModelSettingsUpdate) -> dict[str, Any]:
    settings = _apply_update(payload, request.app.state.settings)
    try:
        persist_model_settings(settings)
    except OSError as error:
        raise HTTPException(
            status_code=503, detail="模型配置保存失败，请检查数据目录权限。"
        ) from error
    _activate_settings(request.app, settings)
    return _model_payload(settings)


@router.post("/models/test")
async def test_model_settings(
    request: Request, payload: ModelSettingsUpdate | None = None
) -> dict[str, Any]:
    settings = request.app.state.settings
    candidate = _apply_update(payload, settings) if payload is not None else settings
    refiner = LLMSemanticRefiner.from_settings(candidate)
    try:
        result = await refiner.test_connection()
    except Exception:
        result = {"ok": False, "latency_ms": 0}
    ok = bool(result.get("ok", False))
    latency = result.get("latency_ms", 0)
    # Provider error messages may contain URLs, request headers or echoed secrets.
    return {
        "ok": ok,
        "latency_ms": latency if type(latency) is int and latency >= 0 else 0,
        "message": "连接成功。" if ok else "连接失败，请检查接口、模型名称、凭据或超时设置。",
    }


__all__ = ["ModelSettingsUpdate", "get_model_settings", "redact_secret", "router"]
