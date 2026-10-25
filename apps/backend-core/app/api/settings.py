"""Runtime model settings.

GET returns public fields plus secret-presence flags; all secret hints are a
constant mask. PUT validates a clone, writes an atomic owner-only JSON file in
the SQLite data mount, then activates it. POST /test uses a transient clone and
never modifies active settings or the saved configuration.
"""

from __future__ import annotations

import asyncio
import base64
import time
from contextlib import contextmanager
from ipaddress import ip_address
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import Settings, clone_model_settings, persist_model_settings, persist_private_bytes
from app.infrastructure.llm import LLMSemanticRefiner
from app.infrastructure.model_catalog import discover_models
from app.repositories.model_configs import ModelConfigError, ModelConfigRepository


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
SpeechProtocol = Literal["http-json", "volcengine-ws"]
SpeechAuthMode = Literal["api-key", "app-token"]


class LLMUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    align_mode: AlignMode | None = None
    provider_preset: PresetName | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    clear_api_key: bool = False
    timeout_ms: int | None = Field(default=None, gt=0, strict=True)

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        return _validate_endpoint(value)


class STTUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: SpeechSource | None = None
    protocol: SpeechProtocol | None = None
    auth_mode: SpeechAuthMode | None = None
    resource_id: str | None = Field(default=None, max_length=200)
    timeout_ms: int | None = Field(default=None, ge=200, le=30000, strict=True)
    enable_nonstream: bool | None = Field(default=None, strict=True)
    cloud_opt_in: bool | None = None
    cloud_endpoint: str | None = None
    cloud_app_id: str | None = None
    access_token: str | None = None
    clear_access_token: bool = False
    cloud_stt_endpoint: str | None = None
    cloud_stt_protocol: SpeechProtocol | None = None
    cloud_stt_auth_mode: SpeechAuthMode | None = None
    cloud_stt_resource_id: str | None = Field(default=None, max_length=200)
    cloud_stt_timeout_ms: int | None = Field(default=None, ge=200, le=30000, strict=True)
    cloud_stt_enable_nonstream: bool | None = Field(default=None, strict=True)
    cloud_stt_app_id: str | None = None
    cloud_stt_access_token: str | None = None

    @field_validator("cloud_endpoint", "cloud_stt_endpoint")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        return _validate_stt_endpoint(value)


class ModelSettingsUpdate(BaseModel):
    """Flat fields explicitly provided by the UI override nested fields."""

    model_config = ConfigDict(extra="ignore")

    clear_llm_api_key: bool = False
    clear_cloud_stt_access_token: bool = False
    llm: LLMUpdate | None = None
    stt: STTUpdate | None = None
    llm_align_mode: AlignMode | None = None
    llm_provider_preset: PresetName | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    api_key: str | None = None
    clear_api_key: bool = False
    llm_timeout_ms: int | None = Field(default=None, gt=0, strict=True)
    stt_provider: SpeechSource | None = None
    cloud_opt_in: bool | None = None
    cloud_endpoint: str | None = None
    cloud_app_id: str | None = None
    cloud_access_token: str | None = None
    access_token: str | None = None
    clear_access_token: bool = False
    cloud_stt_endpoint: str | None = None
    cloud_stt_protocol: SpeechProtocol | None = None
    cloud_stt_auth_mode: SpeechAuthMode | None = None
    cloud_stt_resource_id: str | None = Field(default=None, max_length=200)
    cloud_stt_timeout_ms: int | None = Field(default=None, ge=200, le=30000, strict=True)
    cloud_stt_enable_nonstream: bool | None = Field(default=None, strict=True)
    cloud_stt_app_id: str | None = None
    cloud_stt_access_token: str | None = None

    @field_validator("llm_base_url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        return _validate_endpoint(value)

    @field_validator("cloud_endpoint", "cloud_stt_endpoint")
    @classmethod
    def validate_speech_url(cls, value: str | None) -> str | None:
        return _validate_stt_endpoint(value)


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


def _validate_stt_endpoint(value: str | None) -> str | None:
    """Speech accepts encrypted endpoints and loopback-only development URLs."""
    if value is None or not value.strip():
        return value
    endpoint = value.strip()
    try:
        parts = urlsplit(endpoint)
        host = parts.hostname or ""
        local = host.lower() == "localhost"
        if not local:
            try:
                local = ip_address(host).is_loopback
            except ValueError:
                local = False
        valid = (
            parts.scheme in {"https", "wss", "http", "ws"}
            and bool(host)
            and not parts.username
            and not parts.password
            and not parts.query
            and not parts.fragment
            and (parts.scheme in {"https", "wss"} or local)
        )
        # Accessing port also validates malformed/out-of-range ports.
        _ = parts.port
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(
            "语音地址必须为 HTTPS/WSS，或仅本机开发使用的 HTTP/WS，且不能含凭据、查询或片段。"
        )
    return endpoint


def redact_secret(value: Any) -> str:
    """Never expose any part of a credential, including short credentials."""

    return "****" if value else ""


def _public_endpoint(value: str, *, speech: bool = False) -> str:
    # Environment/legacy files predate URL validation: never expose embedded
    # authentication through an otherwise masked public settings payload.
    try:
        return (_validate_stt_endpoint(value) if speech else _validate_endpoint(value)) or ""
    except ValueError:
        return ""


def _model_payload(settings: Settings) -> dict[str, Any]:
    token = settings.cloud_stt_access_token or settings.cloud_access_token
    endpoint = _public_endpoint(settings.cloud_stt_endpoint or settings.cloud_endpoint, speech=True)
    app_id = settings.cloud_stt_app_id or settings.cloud_app_id
    key = settings.llm_api_key
    key_hint = redact_secret(key)
    token_hint = redact_secret(token)
    presets = LLMSemanticRefiner.presets()
    llm = {
        "align_mode": settings.llm_align_mode,
        "provider_preset": settings.llm_provider_preset,
        "base_url": _public_endpoint(settings.llm_base_url),
        "model": settings.llm_model,
        "api_key": key_hint,
        "has_api_key": bool(key),
        "timeout_ms": settings.llm_timeout_ms,
        "enabled": LLMSemanticRefiner(settings).enabled,
    }
    stt = {
        "protocol": settings.cloud_stt_protocol,
        "auth_mode": settings.cloud_stt_auth_mode,
        "resource_id": settings.cloud_stt_resource_id,
        "timeout_ms": settings.cloud_stt_timeout_ms,
        "enable_nonstream": settings.cloud_stt_enable_nonstream,
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
        "cloud_stt_protocol": settings.cloud_stt_protocol,
        "cloud_stt_auth_mode": settings.cloud_stt_auth_mode,
        "cloud_stt_resource_id": settings.cloud_stt_resource_id,
        "cloud_stt_timeout_ms": settings.cloud_stt_timeout_ms,
        "cloud_stt_enable_nonstream": settings.cloud_stt_enable_nonstream,
        "cloud_stt_app_id": app_id,
        "cloud_stt_token_configured": bool(token),
        "cloud_stt_token_hint": token_hint,
        "llm_align_mode": settings.llm_align_mode,
        "llm_provider_preset": settings.llm_provider_preset,
        "llm_base_url": _public_endpoint(settings.llm_base_url),
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
            "protocol": "cloud_stt_protocol",
            "auth_mode": "cloud_stt_auth_mode",
            "resource_id": "cloud_stt_resource_id",
            "timeout_ms": "cloud_stt_timeout_ms",
            "enable_nonstream": "cloud_stt_enable_nonstream",
            "cloud_stt_protocol": "cloud_stt_protocol",
            "cloud_stt_auth_mode": "cloud_stt_auth_mode",
            "cloud_stt_resource_id": "cloud_stt_resource_id",
            "cloud_stt_timeout_ms": "cloud_stt_timeout_ms",
            "cloud_stt_enable_nonstream": "cloud_stt_enable_nonstream",
            "cloud_opt_in": "cloud_opt_in",
            "cloud_endpoint": "cloud_stt_endpoint",
            "cloud_stt_endpoint": "cloud_stt_endpoint",
            "cloud_app_id": "cloud_stt_app_id",
            "cloud_stt_app_id": "cloud_stt_app_id",
            "access_token": "cloud_stt_access_token",
            "cloud_stt_access_token": "cloud_stt_access_token",
        }
        for name, value in payload.stt.model_dump(exclude_none=True).items():
            if name in mapping:
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
    for flag in (
        "clear_llm_api_key",
        "clear_cloud_stt_access_token",
        "clear_api_key",
        "clear_access_token",
    ):
        values.pop(flag, None)
    if (
        payload.clear_llm_api_key
        or payload.clear_api_key
        or (payload.llm and payload.llm.clear_api_key)
    ):
        values["llm_api_key"] = ""
    if (
        payload.clear_cloud_stt_access_token
        or payload.clear_access_token
        or (payload.stt and payload.stt.clear_access_token)
    ):
        values["cloud_stt_access_token"] = ""
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
    with _repository_errors(), request.app.state.model_config_lock:
        _require_no_session(request.app)
        settings = _apply_update(payload, request.app.state.settings)
        # Legacy callers still receive their historical persistence file. The
        # profile store is authoritative after migration and activation.
        legacy_path = settings.model_settings_path
        if legacy_path.exists() and legacy_path.stat().st_size > 64 * 1024:
            raise OSError("Legacy settings file exceeds safe migration limit")
        previous_legacy = legacy_path.read_bytes() if legacy_path.is_file() else None
        persist_model_settings(settings)
        try:
            request.app.state.model_configs.legacy_update(settings)
        except Exception:
            if previous_legacy is None:
                legacy_path.unlink(missing_ok=True)
            else:
                persist_private_bytes(legacy_path, previous_legacy)
            raise
        _activate_settings(request.app, settings)
        return _model_payload(settings)


@router.post("/models/test")
async def test_model_settings(
    request: Request, payload: ModelSettingsUpdate | None = None
) -> dict[str, Any]:
    settings = request.app.state.settings
    candidate = _apply_update(payload, settings) if payload is not None else settings
    refiner = LLMSemanticRefiner(candidate)
    try:
        result = await refiner.test_connection()
    except Exception:
        result = {"ok": False, "latency_ms": 0}
    return _public_llm_test_result(result)


def _public_llm_test_result(
    result: Any, *, success_message: str = "连接成功。", discovery: bool = False
) -> dict[str, Any]:
    """Expose only controlled diagnostics; upstream messages may echo secrets."""

    values = result if isinstance(result, dict) else {}
    ok = values.get("ok") is True
    latency = values.get("latency_ms", 0)
    public = {
        "ok": ok,
        "latency_ms": latency if type(latency) is int and latency >= 0 else 0,
        "message": success_message,
    }
    if ok:
        return public
    messages = {
        "CONFIG_INCOMPLETE": "配置不完整，请填写 Base URL 和模型名称。",
        "TIMEOUT": "模型请求超时，请适当增大请求超时，并检查网络连接。",
        "NETWORK_ERROR": "无法连接模型服务，请检查服务端网络、DNS、代理及 HTTPS 证书。",
        "INVALID_RESPONSE": "模型服务响应格式不兼容，请确认接口支持 Chat Completions。",
        "UNKNOWN_ERROR": "模型测试失败，请检查接口配置或服务端运行环境。",
        "HTTP_ERROR": "模型服务拒绝请求，请核对接口配置。",
    }
    if discovery:
        messages.update(
            {
                "CONFIG_INCOMPLETE": "请先填写 Base URL，再获取模型列表。",
                "INVALID_RESPONSE": "模型列表响应格式不兼容，可手动填写模型 ID。",
                "RESPONSE_TOO_LARGE": "模型列表响应超过安全大小限制，请手动填写模型 ID。",
            }
        )
    code = values.get("error_code")
    if not isinstance(code, str) or code not in messages:
        code = "UNKNOWN_ERROR"
    public["error_code"] = code
    public["message"] = messages[code]
    status = values.get("http_status")
    if code != "HTTP_ERROR" or type(status) is not int or not 100 <= status <= 599:
        return public
    public["http_status"] = status
    suggestions = {
        400: "请求参数不兼容，请核对模型名称、接口协议及模型参数。",
        401: "鉴权失败，请检查 API Key 是否有效、是否属于当前服务商。",
        403: "访问被拒绝，请检查模型开通状态、项目权限及账户状态。",
        404: "接口或模型不存在，或密钥无访问权限；请核对 Base URL、模型 ID 及开通状态。",
        429: "请求受限，请检查额度、余额或限流状态，稍后重试。",
    }
    if discovery:
        suggestions[404] = "服务未提供此模型列表接口，请核对 Base URL，或手动填写模型 ID。"
        suggestions[405] = "服务不支持获取模型列表，请手动填写模型 ID。"
    advice = suggestions.get(status)
    if advice is None:
        advice = "模型服务暂时异常，请稍后重试。" if status >= 500 else "请核对模型服务的接口配置。"
    public["message"] = f"模型服务返回 HTTP {status}：{advice}"
    return public


class ProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=512)
    settings: ModelSettingsUpdate = Field(default_factory=ModelSettingsUpdate)

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("配置名称不能为空。")
        return value.strip()


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1, strict=True)
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=512)
    settings: ModelSettingsUpdate = Field(default_factory=ModelSettingsUpdate)

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("配置名称不能为空。")
        return value.strip() if value is not None else None


class ProfileDuplicate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value: str | None) -> str | None:
        return ProfileUpdate.nonempty_name(value)


class ProfileActivation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int | None = Field(default=None, ge=1, strict=True)


class ProfileDiscovery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = None
    settings: ModelSettingsUpdate | None = None


class ProfileTest(ProfileDiscovery):
    target: Literal["llm", "stt"] = "llm"


@contextmanager
def _repository_errors():
    try:
        yield
    except ModelConfigError as error:
        raise HTTPException(
            status_code=error.status,
            detail={"code": error.code, "message": error.message},
        ) from error
    except OSError as error:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "PROFILE_STORE_UNAVAILABLE",
                "message": "配置保存失败，请检查数据目录权限或配置文件。",
            },
        ) from error


def _require_no_session(application: Any) -> None:
    from app.domain.session import SessionState

    if any(
        runtime.session.state != SessionState.TERMINATED
        for runtime in getattr(application.state, "sessions", {}).values()
    ):
        raise ModelConfigError(
            "SESSION_ACTIVE", "请结束当前会话后再激活配置；暂停仍属于未结束会话。"
        )


def _profile_payload(repository: ModelConfigRepository, profile: dict[str, Any]) -> dict[str, Any]:
    active = repository.active_snapshot()
    is_active = profile["id"] == active["id"]
    settings = clone_model_settings(repository.base_settings, profile["settings"])
    return {
        **{
            key: profile[key]
            for key in (
                "id",
                "name",
                "description",
                "revision",
                "created_at",
                "updated_at",
                "is_default",
            )
        },
        "is_active": is_active,
        "active_revision": active["revision"] if is_active else None,
        "pending_activation": is_active and profile["revision"] != active["revision"],
        "settings": _model_payload(settings),
    }


@router.get("/model-configs")
def list_model_configs(request: Request) -> dict[str, Any]:
    repository = request.app.state.model_configs
    with repository.lock:
        active = repository.active_snapshot()
        return {
            "configs": [_profile_payload(repository, item) for item in repository.list()],
            "active_id": active["id"],
            "active_revision": active["revision"],
            "active_profile": {
                "id": active["id"],
                "name": active["name"],
                "revision": active["revision"],
                "settings": _model_payload(
                    clone_model_settings(repository.base_settings, active["settings"])
                ),
            },
            "presets": LLMSemanticRefiner.presets(),
            "store_available": not repository.load_failed,
        }


@router.post("/model-configs", status_code=201)
def create_model_config(request: Request, payload: ProfileCreate) -> dict[str, Any]:
    repository = request.app.state.model_configs
    with _repository_errors(), repository.lock:
        # Start new profiles from the credential-free local fallback, not the
        # currently active remote credentials.
        base = repository.get("local-default")
        settings = _apply_update(
            payload.settings, clone_model_settings(repository.base_settings, base["settings"])
        )
        profile = repository.create(payload.name, payload.description, settings)
        return _profile_payload(repository, profile)


def _profile_candidate(request: Request, payload: ProfileDiscovery) -> Settings:
    repository = request.app.state.model_configs
    with _repository_errors(), repository.lock:
        values = repository.get(payload.id or "local-default")["settings"]
        candidate = clone_model_settings(repository.base_settings, values)
        return _apply_update(payload.settings, candidate) if payload.settings else candidate


@router.post("/model-configs/discover-models")
async def discover_model_config(request: Request, payload: ProfileDiscovery) -> dict[str, Any]:
    candidate = _profile_candidate(request, payload)
    result = await discover_models(candidate)
    public = _public_llm_test_result(
        result, success_message="模型列表已获取；选择后仍需测试模型调用权限。", discovery=True
    )
    models = result.get("models", []) if public["ok"] else []
    if public["ok"] and not models:
        public["message"] = "接口返回的模型列表为空，可手动填写模型 ID。"
    return {
        **public,
        "models": models,
        "truncated": result.get("truncated") is True,
    }


@router.post("/model-configs/test")
async def test_model_config(request: Request, payload: ProfileTest) -> dict[str, Any]:
    candidate = _profile_candidate(request, payload)
    if payload.target == "stt":
        repository = request.app.state.model_configs
        with _repository_errors(), repository.lock:
            requested = repository.get(payload.id or "local-default")["settings"]["stt_provider"]
        if payload.settings:
            requested = _updates(payload.settings).get("stt_provider", requested)
        if requested == "cloud" and not candidate.cloud_opt_in:
            return _public_speech_test_result({"ok": False, "error_code": "CLOUD_OPT_IN_REQUIRED"})
        return await _test_speech_config(request, candidate)
    try:
        result = await LLMSemanticRefiner(candidate).test_connection()
    except Exception:
        result = {"ok": False, "latency_ms": 0}
    public_result = _public_llm_test_result(result, success_message="模型接口请求成功。")
    return {
        "target": "llm",
        **public_result,
        "verified": "network" if public_result["ok"] else "none",
    }


async def _test_speech_config(request: Request, candidate: Settings) -> dict[str, Any]:
    from app.infrastructure.speech import (
        HttpStreamingProvider,
        ProviderSpeechServices,
        RemoteProviderConfig,
    )

    started = time.perf_counter()
    ok, verified = False, "none"
    message = "语音接口验证失败，请检查授权、地址、凭据或响应协议。"
    try:
        if candidate.stt_provider == "local":
            services = getattr(request.app.state, "speech_services", None)
            if not isinstance(services, ProviderSpeechServices):
                services = ProviderSpeechServices(candidate)
            stream = await asyncio.to_thread(services.create_stream, "local")
            try:
                ok = bool(stream.available)
            finally:
                stream.close()
            verified = "capability" if ok else "none"
            message = (
                "本地识别模型已加载；未进行语音准确率测试。"
                if ok
                else "本地识别模型未就绪，请检查模型文件。"
            )
        else:
            config = RemoteProviderConfig(
                id="probe",
                label="probe",
                endpoint=candidate.cloud_stt_endpoint,
                app_id=candidate.cloud_stt_app_id,
                access_token=candidate.cloud_stt_access_token,
                timeout_seconds=candidate.cloud_stt_timeout_ms / 1000,
                protocol=candidate.cloud_stt_protocol,
                auth_mode=candidate.cloud_stt_auth_mode,
                resource_id=candidate.cloud_stt_resource_id,
                enable_nonstream=candidate.cloud_stt_enable_nonstream,
            )
            if config.protocol == "volcengine-ws":
                if not candidate.cloud_opt_in:
                    result = {"ok": False, "error_code": "CLOUD_OPT_IN_REQUIRED"}
                elif (
                    not config.access_token.strip()
                    or not config.resource_id.strip()
                    or (config.auth_mode == "app-token" and not config.app_id.strip())
                ):
                    result = {"ok": False, "error_code": "CONFIG_INCOMPLETE"}
                else:
                    from app.infrastructure.speech_websocket import probe_websocket

                    result = await probe_websocket(config)
                return _public_speech_test_result(result)
            provider = HttpStreamingProvider(
                config, opt_in=candidate.cloud_opt_in, sample_rate=candidate.speech_sample_rate
            )
            if not provider.configured:
                raise ValueError("Speech provider is not configured")
            headers = {"Authorization": f"Bearer {config.access_token}"}
            if config.app_id:
                headers["X-Application-Id"] = config.app_id
            async with asyncio.timeout(config.timeout_seconds):
                async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
                    response = await client.post(
                        config.endpoint,
                        headers=headers,
                        json={
                            "stream_id": f"probe-{uuid4()}",
                            "audio": base64.b64encode(
                                bytes(candidate.speech_sample_rate // 5)
                            ).decode("ascii"),
                            "sample_rate": candidate.speech_sample_rate,
                            "segment_id": 0,
                            "turn_token": 0,
                        },
                    )
                    response.raise_for_status()
                    data = response.json()
            if not isinstance(data, dict) or not isinstance(
                data.get("text", data.get("transcript")), str
            ):
                raise ValueError("Invalid speech gateway response")
            ok, verified = True, "network"
            message = "语音网关请求与响应协议验证成功；未验证识别准确率。"
    except Exception:
        pass
    return {
        "target": "stt",
        "ok": ok,
        "verified": verified,
        "message": message,
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }


def _public_speech_test_result(result: Any) -> dict[str, Any]:
    """Only controlled speech diagnostics cross the backend credential boundary."""
    values = result if isinstance(result, dict) else {}
    ok = values.get("ok") is True
    latency = values.get("latency_ms", 0)
    public = {
        "target": "stt",
        "ok": ok,
        "verified": "network" if ok else "none",
        "latency_ms": latency if type(latency) is int and latency >= 0 else 0,
        "message": "流式语音握手与协议响应验证成功；未验证识别准确率。",
    }
    if ok:
        if values.get("probe_empty_audio") is True:
            public["probe_empty_audio"] = True
            public["message"] = (
                "语音服务鉴权与协议可达；静音探测返回空音频，实际识别效果请在工作台朗读验证。"
            )
        return public
    messages = {
        "CONFIG_INCOMPLETE": "语音配置不完整，请检查地址、资源 ID、语音凭据；旧版还需要 App ID。",
        "CLOUD_OPT_IN_REQUIRED": "请先明确授权发送语音片段到云端。",
        "TIMEOUT": "语音接口请求超时，请检查网络或适当增大语音超时。",
        "NETWORK_ERROR": "无法连接语音服务，请检查服务端网络、DNS、代理与证书。",
        "HTTP_ERROR": "语音服务拒绝 WebSocket 握手，请检查地址、鉴权、资源开通与账户状态。",
        "INVALID_RESPONSE": "语音接口协议响应不兼容，请检查所选协议与接口地址。",
        "PROVIDER_ERROR": "语音服务返回错误，请检查独立语音凭据、资源开通、配额与账户状态。",
        "UNKNOWN_ERROR": "语音接口验证失败，请检查配置或服务端运行环境。",
    }
    code = values.get("error_code")
    if not isinstance(code, str) or code not in messages:
        code = "UNKNOWN_ERROR"
    public["error_code"] = code
    public["message"] = messages[code]
    status = values.get("http_status")
    if code == "HTTP_ERROR" and type(status) is int and 100 <= status <= 599:
        public["http_status"] = status
    provider_code = values.get("provider_error_code")
    if code == "PROVIDER_ERROR" and type(provider_code) is int and 0 <= provider_code <= 99999999:
        public["provider_error_code"] = provider_code
    return public


@router.get("/model-configs/{identifier}")
def get_model_config(request: Request, identifier: str) -> dict[str, Any]:
    repository = request.app.state.model_configs
    with _repository_errors(), repository.lock:
        return _profile_payload(repository, repository.get(identifier))


@router.put("/model-configs/{identifier}")
def update_model_config(
    request: Request, identifier: str, payload: ProfileUpdate
) -> dict[str, Any]:
    repository = request.app.state.model_configs
    with _repository_errors(), repository.lock:
        current = repository.get(identifier)
        settings = _apply_update(
            payload.settings,
            clone_model_settings(repository.base_settings, current["settings"]),
        )
        profile = repository.update(
            identifier, payload.name, payload.description, settings, payload.revision
        )
        return _profile_payload(repository, profile)


@router.delete("/model-configs/{identifier}", status_code=204)
def delete_model_config(request: Request, identifier: str) -> Response:
    with _repository_errors():
        request.app.state.model_configs.delete(identifier)
    return Response(status_code=204)


@router.post("/model-configs/{identifier}/duplicate", status_code=201)
def duplicate_model_config(
    request: Request, identifier: str, payload: ProfileDuplicate | None = None
) -> dict[str, Any]:
    repository = request.app.state.model_configs
    with _repository_errors(), repository.lock:
        profile = repository.duplicate(identifier, payload.name if payload else None)
        return _profile_payload(repository, profile)


@router.post("/model-configs/{identifier}/activate")
def activate_model_config(
    request: Request, identifier: str, payload: ProfileActivation | None = None
) -> dict[str, Any]:
    repository = request.app.state.model_configs
    with _repository_errors(), request.app.state.model_config_lock:
        _require_no_session(request.app)
        active = repository.activate(identifier, payload.revision if payload else None)
        settings = clone_model_settings(repository.base_settings, active["settings"])
        _activate_settings(request.app, settings)
        return _profile_payload(repository, repository.get(identifier))


__all__ = ["ModelSettingsUpdate", "get_model_settings", "redact_secret", "router"]
