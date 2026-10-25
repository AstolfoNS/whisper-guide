"""Speech infrastructure and vendor-neutral STT provider registry."""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import io
import json
import math
import os
import re
import threading
from abc import ABC, abstractmethod
from array import array
from collections.abc import Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import uuid4


class SpeechUnavailableError(RuntimeError):
    """Raised when a requested speech capability is unavailable."""


def _safe_failure_reason(error: Exception, timeout_ms: int | None = None) -> dict[str, Any]:
    """Keep only bounded diagnostics, never exception text, headers, or endpoints."""
    reason = getattr(error, "failure_reason", None)
    source = reason if isinstance(reason, dict) else vars(error)
    code = source.get("error_code")
    stage = source.get("stage")
    http_status = source.get("http_status")
    provider_code = source.get("provider_error_code")
    timeout = source.get("timeout_ms", timeout_ms)
    return {
        "error_code": code
        if isinstance(code, str)
        and code
        in {
            "TIMEOUT",
            "HTTP_ERROR",
            "PROVIDER_ERROR",
            "INVALID_RESPONSE",
            "NETWORK_ERROR",
            "UNKNOWN_ERROR",
            "CONFIG_INCOMPLETE",
        }
        else "TIMEOUT"
        if isinstance(error, TimeoutError)
        else "UNKNOWN_ERROR",
        "stage": stage
        if isinstance(stage, str)
        and stage
        in {
            "configuration",
            "capacity",
            "connect",
            "initialize",
            "send",
            "result",
            "buffer",
            "receive",
            "closed",
            "unknown",
        }
        else "unknown",
        "http_status": http_status
        if type(http_status) is int and 100 <= http_status <= 599
        else None,
        "provider_error_code": provider_code
        if type(provider_code) is int and 0 <= provider_code <= 0xFFFFFFFF
        else None,
        "timeout_ms": timeout if type(timeout) is int and 0 < timeout <= 30000 else None,
    }


def _recognizer_timeout_ms(recognizer: Any) -> int | None:
    value = getattr(getattr(recognizer, "config", None), "timeout_seconds", None)
    if type(value) in {int, float} and math.isfinite(value) and 0 < value <= 30:
        return max(1, round(value * 1000))
    return None


def _speech_failure(error: Exception, recognizer: Any) -> SpeechUnavailableError:
    failure = SpeechUnavailableError("语音识别服务暂时不可用。")
    failure.failure_reason = _safe_failure_reason(error, _recognizer_timeout_ms(recognizer))
    return failure


@dataclass(frozen=True, slots=True)
class RecognitionUpdate:
    """A complete, normalized hypothesis for one streaming segment."""

    text: str
    is_final: bool = False
    segment_id: int = 0
    is_filler_only: bool = False
    start_ms: int | None = None
    end_ms: int | None = None


class SpeechStream:
    """Streaming STT and VAD facade with no audio persistence."""

    def __init__(
        self,
        *,
        recognizer: Any | None = None,
        sample_rate: int = 16_000,
        vad: Any | None = None,
        provider_name: str = "local",
    ) -> None:
        self.provider_name = provider_name
        self.sample_rate = sample_rate
        self._recognizer = recognizer
        self._vad = vad
        self.vad_is_fallback = vad is None
        self._closed = False
        self.fallback_reason: dict[str, Any] | None = None

    @property
    def ready(self) -> bool:
        return (
            not self._closed and self.available and bool(getattr(self._recognizer, "ready", True))
        )

    async def wait_ready(self, timeout: float | None = None) -> bool:
        if self._closed or not self.available:
            raise SpeechUnavailableError("语音识别流已关闭或不可用。")
        wait = getattr(self._recognizer, "wait_ready", None)
        if callable(wait):
            try:
                if not await wait(timeout=timeout):
                    raise TimeoutError()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                raise _speech_failure(error, self._recognizer) from None
        if not self.ready:
            raise SpeechUnavailableError("语音识别流尚未就绪。")
        return True

    @property
    def effective_provider(self) -> str:
        return self.provider_name

    @property
    def uses_connection_timestamps(self) -> bool:
        return bool(getattr(self._recognizer, "uses_connection_timestamps", False))

    @property
    def available(self) -> bool:
        return self._recognizer is not None

    def accept_pcm_batch(self, frames: list[bytes]) -> list[RecognitionUpdate | None]:
        """Keep one ordered outcome per packet, including silent/no-result packets."""
        return [self.accept_pcm(pcm) for pcm in frames]

    def accept_pcm(self, pcm: bytes) -> RecognitionUpdate | None:
        if self._closed or not pcm or self._recognizer is None:
            return None
        recognizer = self._recognizer
        try:
            if hasattr(recognizer, "accept_waveform"):
                samples = _pcm16_to_float32(pcm)
                recognizer.accept_waveform(self.sample_rate, samples)
                if hasattr(recognizer, "is_ready") and recognizer.is_ready():
                    recognizer.decode_stream()
                text = _normalize_transcript(_extract_text(recognizer))
                if not text:
                    return None
                return RecognitionUpdate(
                    text=text,
                    is_final=bool(getattr(recognizer, "is_endpoint", lambda: False)()),
                    is_filler_only=_is_filler_only(text),
                )
            result = recognizer(pcm)
            if result is None:
                return None
            if isinstance(result, RecognitionUpdate):
                text = _normalize_transcript(result.text)
                return RecognitionUpdate(
                    text=text,
                    is_final=result.is_final,
                    segment_id=result.segment_id,
                    is_filler_only=_is_filler_only(text),
                    start_ms=result.start_ms,
                    end_ms=result.end_ms,
                )
            if isinstance(result, str):
                text = _normalize_transcript(result)
                return RecognitionUpdate(text=text, is_filler_only=_is_filler_only(text))
            text = _normalize_transcript(str(getattr(result, "text", result)))
            return RecognitionUpdate(
                text=text,
                is_final=bool(getattr(result, "is_final", False)),
                segment_id=int(getattr(result, "segment_id", 0)),
                is_filler_only=_is_filler_only(text),
                start_ms=getattr(result, "start_ms", None),
                end_ms=getattr(result, "end_ms", None),
            )
        except SpeechUnavailableError:
            raise
        except Exception as error:  # engine-specific failures become safe metadata
            raise _speech_failure(error, recognizer) from None

    def is_speech(self, pcm: bytes) -> bool:
        if self._closed or not pcm:
            return False
        if self._vad is not None:
            try:
                voiced = bool(self._vad.is_speech(pcm, self.sample_rate))
            except Exception:
                pass
            else:
                self.vad_is_fallback = False
                return voiced
        self.vad_is_fallback = True
        if len(pcm) % 2:
            return False
        samples = array("h", pcm)
        if not samples:
            return False
        rms = math.sqrt(sum(value * value for value in samples) / len(samples))
        return rms >= 500

    def notify_voice(self) -> None:
        notify = getattr(self._recognizer, "notify_voice", None)
        if not self._closed and callable(notify):
            notify()

    def reset(self) -> None:
        reset = getattr(self._recognizer, "reset", None)
        if callable(reset):
            reset()

    def close(self) -> None:
        self._closed = True
        close = getattr(self._recognizer, "close", None)
        if callable(close):
            close()
        self._recognizer = None


class SpeechServices:
    """Local sherpa-onnx STT, WebRTC VAD and in-memory edge-tts services."""

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings
        self.sample_rate = int(getattr(settings, "speech_sample_rate", 16_000))
        self._stt_model_dir = _setting_path(settings, "resolved_speech_model_dir")
        if self._stt_model_dir is None:
            self._stt_model_dir = _setting_path(settings, "speech_model_dir")
        self.tts_voice = str(getattr(settings, "tts_voice", "zh-CN-XiaoxiaoNeural"))
        self._vad_factory = _load_vad_factory()
        self._online_recognizer: Any | None = None
        self._model_lock = threading.Lock()

    def status(self) -> dict[str, Any]:
        stt_ready = (
            self._stt_model_dir is not None
            and _model_files(self._stt_model_dir) is not None
            and _sherpa_available()
        )
        return {
            "detail": "" if stt_ready else "识别模型未准备完整，请在服务器运行模型下载脚本。",
            "stt": "ready" if stt_ready else "unavailable",
            "vad": "ready" if self._vad_factory is not None else "fallback",
            "tts": "ready" if _edge_tts_available() else "unavailable",
        }

    def create_stream(self) -> SpeechStream:
        with self._model_lock:
            recognizer = self._create_recognizer()
        vad = self._vad_factory(2) if self._vad_factory is not None else None
        return SpeechStream(recognizer=recognizer, sample_rate=self.sample_rate, vad=vad)

    async def synthesize(self, text: str) -> bytes:
        if not text.strip():
            return b""
        try:
            import edge_tts  # type: ignore[import-not-found]
        except ImportError as error:
            raise SpeechUnavailableError("TTS 服务未安装。") from error
        communicate = edge_tts.Communicate(text, self.tts_voice)
        output = io.BytesIO()
        try:
            async with asyncio.timeout(30):
                async for chunk in communicate.stream():
                    if chunk.get("type") == "audio":
                        output.write(chunk["data"])
        except Exception as error:
            raise SpeechUnavailableError("语音合成服务暂时不可用，请检查网络后继续。") from error
        if not output.tell():
            raise SpeechUnavailableError("语音合成服务没有返回音频。")
        return output.getvalue()

    def _create_recognizer(self) -> Any | None:
        if self._stt_model_dir is None or not self._stt_model_dir.exists():
            return None
        try:
            import sherpa_onnx  # type: ignore[import-not-found]
        except (ImportError, OSError):
            return None
        factory = getattr(self.settings, "speech_recognizer_factory", None)
        if callable(factory):
            return factory(sherpa_onnx, self._stt_model_dir, self.sample_rate)
        model_files = _model_files(self._stt_model_dir)
        if model_files is None:
            return None
        if self._online_recognizer is None:
            try:
                self._online_recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                    tokens=str(model_files["tokens"]),
                    encoder=str(model_files["encoder"]),
                    decoder=str(model_files["decoder"]),
                    joiner=str(model_files["joiner"]),
                    num_threads=2,
                    sample_rate=self.sample_rate,
                    feature_dim=80,
                    decoding_method="greedy_search",
                    enable_endpoint_detection=True,
                    rule1_min_trailing_silence=2.4,
                    rule2_min_trailing_silence=1.2,
                    rule3_min_utterance_length=20,
                )
            except Exception as error:
                raise SpeechUnavailableError("无法加载语音识别模型，请重新准备模型。") from error
        return _SherpaRecognizerStream(self._online_recognizer, self.sample_rate)


class _SherpaRecognizerStream:
    uses_connection_timestamps = True

    def __init__(self, recognizer: Any, sample_rate: int) -> None:
        self.recognizer = recognizer
        self.sample_rate = sample_rate
        self.stream = recognizer.create_stream()
        self.segment_id = 0
        self._samples = 0
        self._segment_start_samples = 0

    def __call__(self, pcm: bytes) -> RecognitionUpdate | None:
        import numpy as np

        waveform = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        self.stream.accept_waveform(self.sample_rate, waveform)
        self._samples += len(waveform)
        while self.recognizer.is_ready(self.stream):
            self.recognizer.decode_stream(self.stream)
        text = _normalize_transcript(self.recognizer.get_result(self.stream).strip())
        is_final = bool(self.recognizer.is_endpoint(self.stream))
        update = RecognitionUpdate(
            text=text,
            is_final=is_final,
            segment_id=self.segment_id,
            is_filler_only=_is_filler_only(text),
            start_ms=self._segment_start_samples * 1000 // self.sample_rate,
            end_ms=self._samples * 1000 // self.sample_rate,
        )
        if is_final:
            self.recognizer.reset(self.stream)
            self.segment_id += 1
            self._segment_start_samples = self._samples
        return update if text or is_final else None

    def reset(self) -> None:
        self.recognizer.reset(self.stream)
        self.segment_id += 1
        self._segment_start_samples = self._samples


FILLER_UTTERANCES = frozenset(
    {"嗯", "嗯嗯", "呃", "呃呃", "啊", "哦", "额", "唔", "诶", "欸", "那个", "这个", "就是"}
)


def _normalize_transcript(text: str) -> str:
    """Preserve the recognizer hypothesis; canonicalize whitespace only."""
    return " ".join(text.replace("\u3000", " ").split()).strip()


def _is_filler_only(text: str) -> bool:
    compact = re.sub(r"[\s，。！？!?；;,.、]+", "", text).casefold()
    return bool(compact) and compact in FILLER_UTTERANCES


def _model_files(directory: Path) -> dict[str, Path] | None:
    result: dict[str, Path] = {}
    tokens = directory / "tokens.txt"
    if not tokens.is_file() or tokens.stat().st_size == 0:
        return None
    result["tokens"] = tokens
    for component in ("encoder", "decoder", "joiner"):
        candidates = [
            path
            for path in sorted(directory.glob(f"{component}*.onnx"))
            if path.is_file() and path.stat().st_size > 0
        ]
        if not candidates:
            return None
        # Quantizing the prediction network causes repeated Chinese characters
        # in this bundled model. Keep its FP32 decoder while using the efficient
        # INT8 encoder/joiner; older INT8-only installations remain loadable.
        candidates.sort(
            key=lambda path: (
                "int8" in path.name if component == "decoder" else "int8" not in path.name,
                path.name,
            )
        )
        result[component] = candidates[0]
    return result


def _setting_path(settings: Any | None, name: str) -> Path | None:
    value = getattr(settings, name, None)
    if value in (None, ""):
        return None
    return Path(value).expanduser()


def _load_vad_factory() -> Any | None:
    try:
        import webrtcvad  # type: ignore[import-not-found]
    except ImportError:
        return None
    return webrtcvad.Vad


def _edge_tts_available() -> bool:
    return importlib.util.find_spec("edge_tts") is not None


def _pcm16_to_float32(pcm: bytes) -> list[float]:
    if len(pcm) % 2:
        pcm = pcm[:-1]
    scale = 1.0 / 32768.0
    return [sample * scale for sample in memoryview(pcm).cast("h")]


def _extract_text(recognizer: Any) -> str:
    result = getattr(recognizer, "result", None)
    if callable(result):
        result = result()
    if result is None:
        result = getattr(recognizer, "text", "")
    if isinstance(result, str):
        return result.strip()
    return str(getattr(result, "text", result)).strip()


def _sherpa_available() -> bool:
    try:
        import sherpa_onnx
    except (ImportError, OSError):
        return False
    return hasattr(sherpa_onnx, "OnlineRecognizer")


class SpeechProvider(Protocol):
    """Strategy contract shared by local and remote speech providers."""

    id: str

    def descriptor(self) -> dict[str, Any]: ...

    def create_stream(self, *, turn_token: int | None = None) -> SpeechStream: ...


class LocalSherpaProvider:
    id = "local"
    label = "本地 sherpa-onnx"
    kind = "local"

    def __init__(self, services: SpeechServices) -> None:
        self.services = services

    def is_available(self) -> bool:
        return self.services._local_stt_ready()

    def descriptor(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "model": "sherpa-onnx",
            "available": self.is_available(),
            "configured": True,
            "supports_streaming": True,
            "requires_opt_in": False,
            "privacy": "音频只在本地服务处理",
        }

    def create_stream(self, *, turn_token: int | None = None) -> SpeechStream:
        del turn_token
        return SpeechServices.create_stream(self.services)


@dataclass(frozen=True, slots=True)
class RemoteProviderConfig:
    id: str
    label: str
    endpoint: str
    access_token: str = ""
    app_id: str = ""
    model: str = ""
    timeout_seconds: float = 2.0
    kind: str = "http-json"
    protocol: str = "http-json"
    auth_mode: str = "api-key"
    resource_id: str = ""
    enable_nonstream: bool = True
    requires_opt_in: bool = True
    privacy: str = "音频片段加密发送到已配置的云端 Provider，不在本服务持久化"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> RemoteProviderConfig | None:
        provider_id = str(value.get("id", "")).strip()
        endpoint = str(value.get("endpoint", "")).strip()
        if not provider_id or not endpoint or provider_id == "local":
            return None
        token = str(value.get("access_token", ""))
        token_env = str(value.get("access_token_env", "")).strip()
        if token_env:
            token = os.getenv(token_env, token)
        try:
            timeout = max(0.2, float(value.get("timeout_seconds", 2.0)))
        except (TypeError, ValueError):
            timeout = 2.0
        return cls(
            id=provider_id,
            label=str(value.get("label", provider_id)),
            endpoint=endpoint,
            access_token=token,
            app_id=str(value.get("app_id", "")),
            model=str(value.get("model", "")),
            timeout_seconds=timeout,
            kind=str(value.get("kind", value.get("protocol", "http-json"))),
            protocol=str(value.get("protocol", value.get("kind", "http-json"))),
            auth_mode=str(value.get("auth_mode", "api-key")),
            resource_id=str(value.get("resource_id", "")),
            enable_nonstream=bool(value.get("enable_nonstream", True)),
            requires_opt_in=bool(value.get("requires_opt_in", True)),
            privacy=str(
                value.get(
                    "privacy",
                    "音频片段加密发送到已配置的云端 Provider，不在本服务持久化",
                )
            ),
        )


def _secure_speech_endpoint(endpoint: str) -> bool:
    """Cloud microphone audio requires TLS; loopback gateways may use HTTP."""
    try:
        parts = urlsplit(endpoint)
        return bool(
            parts.hostname
            and not (parts.username or parts.password or parts.query or parts.fragment)
            and (
                parts.scheme == "https"
                or (parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1", "::1"})
            )
        )
    except ValueError:
        return False


class CloudSpeechProvider(ABC):
    """Shared configuration and capabilities for remote transport strategies."""

    def __init__(self, config: RemoteProviderConfig, *, opt_in: bool, sample_rate: int) -> None:
        self.config = config
        self.opt_in = opt_in
        self.sample_rate = sample_rate

    @property
    @abstractmethod
    def configured(self) -> bool:
        pass

    def descriptor(self) -> dict[str, Any]:
        return {
            "id": self.config.id,
            "label": self.config.label,
            "kind": self.config.protocol,
            "protocol": self.config.protocol,
            "model": self.config.model,
            "available": self.configured,
            "configured": bool(self.config.endpoint and self.config.access_token),
            "supports_streaming": True,
            "requires_opt_in": True,
            "privacy": self.config.privacy,
        }

    @abstractmethod
    def create_stream(self, *, vad: Any | None, turn_token: int | None) -> SpeechStream:
        pass


class HttpStreamingProvider(CloudSpeechProvider):
    """Small HTTP JSON gateway adapter retained for existing deployments."""

    @property
    def configured(self) -> bool:
        return bool(
            self.opt_in
            and self.config.access_token
            and self.config.protocol == "http-json"
            and _secure_speech_endpoint(self.config.endpoint)
        )

    def create_stream(self, *, vad: Any | None, turn_token: int | None) -> SpeechStream:
        if not self.configured:
            raise SpeechUnavailableError("云端识别未授权或未配置。")
        return SpeechStream(
            recognizer=_HTTPStreamingRecognizer(self.config, self.sample_rate, turn_token),
            sample_rate=self.sample_rate,
            vad=vad,
            provider_name=self.config.id,
        )


class WebSocketStreamingProvider(CloudSpeechProvider):
    """Generic persistent transport; wire adapters own vendor-specific details."""

    @property
    def configured(self) -> bool:
        from app.infrastructure.speech_websocket import websocket_configured

        return self.opt_in and self.sample_rate == 16000 and websocket_configured(self.config)

    def create_stream(self, *, vad: Any | None, turn_token: int | None) -> SpeechStream:
        from app.infrastructure.speech_websocket import WebSocketStreamingRecognizer

        if not self.configured:
            raise SpeechUnavailableError("云端识别未授权或未配置。")
        try:
            recognizer = WebSocketStreamingRecognizer(self.config, self.sample_rate, turn_token)
        except Exception as error:
            failure = _speech_failure(error, None)
            failure.failure_reason = _safe_failure_reason(
                error, max(1, round(self.config.timeout_seconds * 1000))
            )
            raise failure from None
        return SpeechStream(
            recognizer=recognizer,
            sample_rate=self.sample_rate,
            vad=vad,
            provider_name=self.config.id,
        )


class ProviderRegistry:
    """Registry for local and arbitrary backend-configured STT providers."""

    def __init__(self, services: SpeechServices, settings: Any | None) -> None:
        self.local = LocalSherpaProvider(services)
        self.remote: dict[str, CloudSpeechProvider] = {}
        for config in self._configs(settings):
            if config.id not in self.remote:
                factory = (
                    WebSocketStreamingProvider
                    if config.protocol != "http-json"
                    else HttpStreamingProvider
                )
                self.remote[config.id] = factory(
                    config,
                    opt_in=bool(getattr(settings, "cloud_opt_in", False)),
                    sample_rate=services.sample_rate,
                )

    @staticmethod
    def _configs(settings: Any | None) -> list[RemoteProviderConfig]:
        raw = getattr(settings, "stt_provider_configs", None) or []
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = []
        values: list[Mapping[str, Any]] = [item for item in raw if isinstance(item, Mapping)]
        cloud_endpoint = getattr(settings, "cloud_stt_endpoint", "") or getattr(
            settings, "cloud_endpoint", ""
        )
        cloud_access_token = getattr(settings, "cloud_stt_access_token", "") or getattr(
            settings, "cloud_access_token", ""
        )
        cloud_app_id = getattr(settings, "cloud_stt_app_id", "") or getattr(
            settings, "cloud_app_id", ""
        )
        if cloud_endpoint:
            values = [
                {
                    "id": "cloud",
                    "label": "云端 Provider",
                    "endpoint": cloud_endpoint,
                    "access_token": cloud_access_token,
                    "app_id": cloud_app_id,
                    "protocol": getattr(settings, "cloud_stt_protocol", "http-json"),
                    "auth_mode": getattr(settings, "cloud_stt_auth_mode", "api-key"),
                    "resource_id": getattr(settings, "cloud_stt_resource_id", ""),
                    "timeout_seconds": getattr(settings, "cloud_stt_timeout_ms", 5000) / 1000,
                    "enable_nonstream": getattr(settings, "cloud_stt_enable_nonstream", True),
                },
                *(value for value in values if value.get("id") != "cloud"),
            ]
        result: list[RemoteProviderConfig] = []
        for value in values:
            config = RemoteProviderConfig.from_mapping(value)
            if config is not None:
                result.append(config)
        return result

    def provider(self, provider_id: str) -> LocalSherpaProvider | CloudSpeechProvider | None:
        if provider_id == "local":
            return self.local
        if provider_id == "cloud" and provider_id not in self.remote:
            return next(iter(self.remote.values()), None)
        return self.remote.get(provider_id)

    def first_remote(self) -> CloudSpeechProvider | None:
        return next(iter(self.remote.values()), None)

    def descriptors(self) -> list[dict[str, Any]]:
        items = [self.local.descriptor()]
        items.extend(provider.descriptor() for provider in self.remote.values())
        items.append(
            {
                "id": "hybrid",
                "label": "混合模式（云端优先，失败回退本地）",
                "kind": "hybrid",
                "model": "",
                "available": self.local.is_available()
                or any(provider.configured for provider in self.remote.values()),
                "configured": bool(self.remote),
                "supports_streaming": True,
                "requires_opt_in": True,
                "privacy": "优先发送到已配置云端；超时或断网自动回退本地",
            }
        )
        return items


class FallbackSpeechStream:
    """Keep local recognition warm while a remote stream has priority."""

    def __init__(self, remote: SpeechStream, local: SpeechStream) -> None:
        self.remote = remote
        self.local = local
        self.active = remote
        self.available = True
        self._closed = False
        self._last_segment_id = -1
        self._segment_offset = 0
        self._needs_remap = False
        self._latest_local: RecognitionUpdate | None = None
        self._frame_count = 0
        self._latest_local_frame = -1
        self._last_cloud_final_frame = -1
        self.fallback_reason: dict[str, Any] | None = None

    @property
    def ready(self) -> bool:
        return not self._closed and bool(getattr(self.active, "ready", self.active.available))

    async def wait_ready(self, timeout: float | None = None) -> bool:
        if self._closed:
            raise SpeechUnavailableError("语音识别流已关闭。")
        active = self.active
        wait = getattr(active, "wait_ready", None)
        try:
            if callable(wait) and not await wait(timeout=timeout):
                raise TimeoutError()
            if not self.ready:
                raise TimeoutError()
            return True
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if self._closed:
                raise SpeechUnavailableError("语音识别流已关闭。") from None
            if active is self.local or not self.local.available:
                raise _speech_failure(error, getattr(active, "_recognizer", None)) from None
            self._activate_local(error)
            # The local model was constructed before starting the remote stream;
            # readiness does not replay audio, reconnect, or grow network buffers.
            return await self.wait_ready(timeout=timeout)

    def _activate_local(self, error: Exception) -> None:
        if self.active is self.local:
            return
        self.fallback_reason = _safe_failure_reason(
            error, _recognizer_timeout_ms(getattr(self.remote, "_recognizer", None))
        )
        self.remote.close()
        self.active = self.local
        self._needs_remap = True

    @property
    def effective_provider(self) -> str:
        return getattr(
            self.active, "effective_provider", "local" if self.active is self.local else "cloud"
        )

    @property
    def fallback_active(self) -> bool:
        return self.active is self.local

    @property
    def vad_is_fallback(self) -> bool:
        return bool(getattr(self.local, "vad_is_fallback", False))

    def is_speech(self, pcm: bytes) -> bool:
        return self.local.is_speech(pcm)

    def notify_voice(self) -> None:
        if self.active is self.remote:
            notify = getattr(self.remote, "notify_voice", None)
            if callable(notify):
                notify()

    def accept_pcm_batch(self, frames: list[bytes]) -> list[RecognitionUpdate | None]:
        """Keep one ordered outcome per packet, including silent/no-result packets."""
        return [self.accept_pcm(pcm) for pcm in frames]

    def accept_pcm(self, pcm: bytes) -> RecognitionUpdate | None:
        if self._closed:
            return None
        self._frame_count += 1
        frame = self._frame_count
        try:
            local_update = self.local.accept_pcm(pcm)
        except SpeechUnavailableError:
            if self.active is self.local:
                raise
            local_update = None
        if self._closed:
            return None
        # Empty silence endpoints must not erase the last useful warm result.
        if local_update is not None and local_update.text.strip():
            self._latest_local = local_update
            self._latest_local_frame = frame
        if self.active is self.local:
            return self._map_update(local_update)
        try:
            remote_update = self.remote.accept_pcm(pcm)
            if remote_update is not None and remote_update.is_final:
                self._last_cloud_final_frame = frame
            return self._map_update(remote_update)
        except SpeechUnavailableError as error:
            if self._closed:
                return None
            if not self.local.available:
                raise
            self._activate_local(error)
            # A warmed local final may lag behind cloud commits. Replaying it
            # with a new segment ID would look like an intentional readback.
            # Only reuse local hypotheses observed after the latest cloud final;
            # a fresh result on this failing frame remains immediately usable.
            fresh_local = (
                self._latest_local
                if self._latest_local_frame > self._last_cloud_final_frame
                else None
            )
            return self._map_update(fresh_local)

    def _map_update(self, update: RecognitionUpdate | None) -> RecognitionUpdate | None:
        if self._closed or update is None:
            return None
        if self._needs_remap:
            self._segment_offset = self._last_segment_id + 1 - update.segment_id
            self._needs_remap = False
        segment_id = update.segment_id + self._segment_offset
        self._last_segment_id = max(self._last_segment_id, segment_id)
        trusted_times = self.active is self.remote or bool(
            getattr(self.local, "uses_connection_timestamps", False)
        )
        return RecognitionUpdate(
            text=update.text,
            is_final=update.is_final,
            segment_id=segment_id,
            is_filler_only=update.is_filler_only,
            # Native warm standby shares the connection's PCM clock, including
            # cached finals returned after a long cloud timeout. Custom local
            # adapters may use segment-relative clocks and remain untimed.
            start_ms=update.start_ms if trusted_times else None,
            end_ms=update.end_ms if trusted_times else None,
        )

    def reset(self) -> None:
        self.active.reset()

    def close(self) -> None:
        self._closed = True
        self._latest_local = None
        self.remote.close()
        self.local.close()
        self.active = self.local


class ProviderSpeechServices(SpeechServices):
    """Provider-selecting facade preserving the local SpeechServices API."""

    def __init__(self, settings: Any | None = None) -> None:
        super().__init__(settings)
        self.provider_name = str(getattr(settings, "stt_provider", "local") or "local")
        self.registry = ProviderRegistry(self, settings)

    def provider_options(self) -> list[dict[str, Any]]:
        return self.registry.descriptors()

    def _local_stt_ready(self) -> bool:
        return super().status().get("stt") == "ready"

    def _effective_provider(self, requested: str | None = None) -> str:
        requested = str(requested or self.provider_name or "local")
        if requested == "local":
            return "local"
        if requested == "hybrid":
            remote = self.registry.first_remote()
            return remote.config.id if remote and remote.configured else "local"
        provider = self.registry.provider(requested)
        if isinstance(provider, CloudSpeechProvider) and provider.configured:
            return provider.config.id
        return "local"

    def status(self, provider_name: str | None = None) -> dict[str, Any]:
        requested = provider_name or self.provider_name
        local = super().status()
        effective = self._effective_provider(requested)
        remote = self.registry.provider(effective)
        remote_enabled = isinstance(remote, CloudSpeechProvider) and remote.configured
        local.update(
            {
                "provider": effective,
                "requested_provider": requested,
                "cloud_enabled": remote_enabled,
                "cloud_notice": remote_enabled,
                "providers": self.provider_options(),
            }
        )
        if remote_enabled:
            local["stt"] = "ready"
            local["detail"] = ""
        elif requested not in {"local", "hybrid"} and effective == "local":
            local["detail"] = "所选 Provider 未配置或未获得隐私授权，已回退本地。"
        return local

    def create_stream(
        self,
        provider_name: str | None = None,
        turn_token: int | None = None,
    ) -> SpeechStream | FallbackSpeechStream:
        requested = str(provider_name or self.provider_name or "local")
        try:
            local = self.registry.local.create_stream(turn_token=turn_token)
        except SpeechUnavailableError:
            # A remote-only deployment may intentionally omit local model files.
            vad = self._vad_factory(2) if self._vad_factory is not None else None
            local = SpeechStream(sample_rate=self.sample_rate, vad=vad)
        if requested == "local":
            return local
        remote = (
            self.registry.first_remote()
            if requested == "hybrid"
            else self.registry.provider(requested)
        )
        if not isinstance(remote, CloudSpeechProvider) or not remote.configured:
            return local
        try:
            remote_stream = remote.create_stream(
                vad=self._vad_factory(2) if self._vad_factory is not None else None,
                turn_token=turn_token,
            )
        except SpeechUnavailableError as error:
            local.fallback_reason = _safe_failure_reason(
                error, max(1, round(remote.config.timeout_seconds * 1000))
            )
            return local
        return FallbackSpeechStream(remote_stream, local)


_CLOUD_REQUESTS = ThreadPoolExecutor(max_workers=4, thread_name_prefix="cloud-stt")


class _HTTPStreamingRecognizer:
    """Send bounded PCM chunks without blocking the real-time audio consumer.

    There is at most one request and one second of queued PCM per stream. A slow
    gateway falls back to the continuously warmed local recognizer. A request
    owns its HTTP client, so pause/close never races a request's client lifetime.
    """

    def __init__(
        self, config: RemoteProviderConfig, sample_rate: int, turn_token: int | None
    ) -> None:
        self.config = config
        self.sample_rate = sample_rate
        self.turn_token = turn_token
        self.stream_id = str(uuid4())
        self._lock = threading.Lock()
        self.segment_id = 0
        self._closed = False
        self._buffer = bytearray()
        self._future: Future[RecognitionUpdate | None] | None = None
        self._chunk_bytes = sample_rate * 2 * 2 // 5  # 400 ms PCM16
        self._max_buffer_bytes = sample_rate * 2  # one second

    def __call__(self, pcm: bytes) -> RecognitionUpdate | None:
        with self._lock:
            return self._accept(pcm)

    def _accept(self, pcm: bytes) -> RecognitionUpdate | None:
        if self._closed:
            return None
        update = None
        if self._future is not None and self._future.done():
            future, self._future = self._future, None
            update = future.result()
            if update is not None and update.is_final:
                self.segment_id = max(self.segment_id + 1, update.segment_id + 1)
        self._buffer.extend(pcm)
        if len(self._buffer) > self._max_buffer_bytes:
            raise SpeechUnavailableError("云端识别积压，已回退本地。")
        if self._future is None and len(self._buffer) >= self._chunk_bytes:
            chunk = bytes(self._buffer)
            self._buffer.clear()
            self._future = _CLOUD_REQUESTS.submit(self._request, chunk, self.segment_id)
        return update

    def _request(self, pcm: bytes, request_segment_id: int) -> RecognitionUpdate | None:
        return asyncio.run(self._request_async(pcm, request_segment_id))

    async def _request_async(self, pcm: bytes, request_segment_id: int) -> RecognitionUpdate | None:
        if self._closed:
            return None
        try:
            import httpx
        except ImportError as error:
            raise SpeechUnavailableError("云端 Provider 需要 httpx。") from error
        headers = {"Authorization": f"Bearer {self.config.access_token}"}
        if self.config.app_id:
            headers["X-Application-Id"] = self.config.app_id
        if self.config.model:
            headers["X-Provider-Model"] = self.config.model
        try:
            async with asyncio.timeout(self.config.timeout_seconds):
                async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                    response = await client.post(
                        self.config.endpoint,
                        headers=headers,
                        json={
                            "stream_id": self.stream_id,
                            "audio": base64.b64encode(pcm).decode("ascii"),
                            "sample_rate": self.sample_rate,
                            "segment_id": request_segment_id,
                            "turn_token": self.turn_token,
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
            if not isinstance(payload, Mapping):
                raise ValueError("Cloud STT response must be a JSON object.")
        except Exception as error:
            raise SpeechUnavailableError("云端 Provider 请求失败。") from error
        text = _normalize_transcript(str(payload.get("text", payload.get("transcript", ""))))
        is_final = bool(payload.get("is_final", payload.get("final", False)))
        try:
            segment_id = int(payload.get("segment_id", request_segment_id))
        except (TypeError, ValueError):
            segment_id = request_segment_id
        if not text and not is_final:
            return None
        return RecognitionUpdate(
            text=text,
            is_final=is_final,
            segment_id=segment_id,
            is_filler_only=_is_filler_only(text),
        )

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._buffer.clear()
            if self._future is not None:
                self._future.cancel()
                self._future = None
