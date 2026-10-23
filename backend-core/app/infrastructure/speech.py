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
from array import array
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class SpeechUnavailableError(RuntimeError):
    """Raised when a requested speech capability is unavailable."""


@dataclass(frozen=True, slots=True)
class RecognitionUpdate:
    """A complete, normalized hypothesis for one streaming segment."""

    text: str
    is_final: bool = False
    segment_id: int = 0
    is_filler_only: bool = False


class SpeechStream:
    """Streaming STT and VAD facade with no audio persistence."""

    def __init__(
        self,
        *,
        recognizer: Any | None = None,
        sample_rate: int = 16_000,
        vad: Any | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        self._recognizer = recognizer
        self._vad = vad
        self._closed = False

    @property
    def available(self) -> bool:
        return self._recognizer is not None

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
            )
        except SpeechUnavailableError:
            raise
        except Exception as error:  # pragma: no cover - engine-specific
            raise SpeechUnavailableError("语音识别处理失败。") from error

    def is_speech(self, pcm: bytes) -> bool:
        if self._closed or not pcm:
            return False
        if self._vad is not None:
            try:
                return bool(self._vad.is_speech(pcm, self.sample_rate))
            except Exception:
                pass
        if len(pcm) % 2:
            return False
        samples = array("h", pcm)
        if not samples:
            return False
        rms = math.sqrt(sum(value * value for value in samples) / len(samples))
        return rms >= 500

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
    def __init__(self, recognizer: Any, sample_rate: int) -> None:
        self.recognizer = recognizer
        self.sample_rate = sample_rate
        self.stream = recognizer.create_stream()
        self.segment_id = 0

    def __call__(self, pcm: bytes) -> RecognitionUpdate | None:
        import numpy as np

        waveform = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        self.stream.accept_waveform(self.sample_rate, waveform)
        while self.recognizer.is_ready(self.stream):
            self.recognizer.decode_stream(self.stream)
        text = _normalize_transcript(self.recognizer.get_result(self.stream).strip())
        is_final = bool(self.recognizer.is_endpoint(self.stream))
        update = RecognitionUpdate(
            text=text,
            is_final=is_final,
            segment_id=self.segment_id,
            is_filler_only=_is_filler_only(text),
        )
        if is_final:
            self.recognizer.reset(self.stream)
            self.segment_id += 1
        return update if text or is_final else None

    def reset(self) -> None:
        self.recognizer.reset(self.stream)
        self.segment_id += 1


FILLER_UTTERANCES = frozenset(
    {"嗯", "嗯嗯", "呃", "呃呃", "啊", "哦", "额", "唔", "诶", "欸", "那个", "这个", "就是"}
)


def _normalize_transcript(text: str) -> str:
    compact = " ".join(text.replace("\u3000", " ").split()).strip()
    return re.sub(r"([^\W\d_])\1{2,}", r"\1", compact)


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
        candidates.sort(key=lambda path: ("int8" not in path.name, path.name))
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
    requires_opt_in: bool = True
    privacy: str = "音频片段加密发送到已配置的云端 Provider，不在本服务持久化"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RemoteProviderConfig | None":
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
            kind=str(value.get("kind", "http-json")),
            requires_opt_in=bool(value.get("requires_opt_in", True)),
            privacy=str(
                value.get(
                    "privacy",
                    "音频片段加密发送到已配置的云端 Provider，不在本服务持久化",
                )
            ),
        )


class HttpStreamingProvider:
    """Generic remote STT adapter using a small HTTP JSON gateway contract."""

    def __init__(self, config: RemoteProviderConfig, *, opt_in: bool, sample_rate: int) -> None:
        self.config = config
        self.opt_in = opt_in
        self.sample_rate = sample_rate

    @property
    def configured(self) -> bool:
        return bool(self.config.endpoint and self.config.access_token) and (
            self.opt_in or not self.config.requires_opt_in
        )

    def descriptor(self) -> dict[str, Any]:
        return {
            "id": self.config.id,
            "label": self.config.label,
            "kind": self.config.kind,
            "model": self.config.model,
            "available": self.configured,
            "configured": bool(self.config.endpoint and self.config.access_token),
            "supports_streaming": True,
            "requires_opt_in": self.config.requires_opt_in,
            "privacy": self.config.privacy,
        }

    def create_stream(self, *, vad: Any | None, turn_token: int | None) -> SpeechStream:
        if not self.configured:
            raise SpeechUnavailableError("云端识别未授权或未配置。")
        return SpeechStream(
            recognizer=_HTTPStreamingRecognizer(self.config, self.sample_rate, turn_token),
            sample_rate=self.sample_rate,
            vad=vad,
        )


class ProviderRegistry:
    """Registry for local and arbitrary backend-configured STT providers."""

    def __init__(self, services: SpeechServices, settings: Any | None) -> None:
        self.local = LocalSherpaProvider(services)
        self.remote: dict[str, HttpStreamingProvider] = {}
        for config in self._configs(settings):
            if config.id not in self.remote:
                self.remote[config.id] = HttpStreamingProvider(
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
        if not values and getattr(settings, "cloud_endpoint", ""):
            values.append(
                {
                    "id": "cloud",
                    "label": "云端 Provider",
                    "endpoint": getattr(settings, "cloud_endpoint", ""),
                    "access_token": getattr(settings, "cloud_access_token", ""),
                    "app_id": getattr(settings, "cloud_app_id", ""),
                }
            )
        result: list[RemoteProviderConfig] = []
        for value in values:
            config = RemoteProviderConfig.from_mapping(value)
            if config is not None:
                result.append(config)
        return result

    def provider(self, provider_id: str) -> LocalSherpaProvider | HttpStreamingProvider | None:
        if provider_id == "local":
            return self.local
        if provider_id == "cloud" and provider_id not in self.remote:
            return next(iter(self.remote.values()), None)
        return self.remote.get(provider_id)

    def first_remote(self) -> HttpStreamingProvider | None:
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
    """Use a remote stream first and fall back to local on failure."""

    def __init__(self, remote: SpeechStream, local: SpeechStream) -> None:
        self.remote = remote
        self.local = local
        self.active = remote
        self.available = True

    def is_speech(self, pcm: bytes) -> bool:
        return self.local.is_speech(pcm)

    def accept_pcm(self, pcm: bytes) -> RecognitionUpdate | None:
        try:
            return self.active.accept_pcm(pcm)
        except SpeechUnavailableError:
            if self.active is self.local:
                raise
            self.remote.close()
            self.active = self.local
            return self.local.accept_pcm(pcm)

    def reset(self) -> None:
        self.active.reset()

    def close(self) -> None:
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
        if isinstance(provider, HttpStreamingProvider) and provider.configured:
            return provider.config.id
        return "local"

    def status(self, provider_name: str | None = None) -> dict[str, Any]:
        requested = provider_name or self.provider_name
        local = super().status()
        effective = self._effective_provider(requested)
        remote = self.registry.provider(effective)
        remote_enabled = isinstance(remote, HttpStreamingProvider) and remote.configured
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
        if not isinstance(remote, HttpStreamingProvider) or not remote.configured:
            return local
        try:
            remote_stream = remote.create_stream(
                vad=self._vad_factory(2) if self._vad_factory is not None else None,
                turn_token=turn_token,
            )
        except SpeechUnavailableError:
            return local
        return FallbackSpeechStream(remote_stream, local)


class _HTTPStreamingRecognizer:
    def __init__(
        self, config: RemoteProviderConfig, sample_rate: int, turn_token: int | None
    ) -> None:
        self.config = config
        self.sample_rate = sample_rate
        self.turn_token = turn_token
        self.segment_id = 0
        self.client: Any | None = None

    def __call__(self, pcm: bytes) -> RecognitionUpdate | None:
        try:
            import httpx
        except ImportError as error:
            raise SpeechUnavailableError("云端 Provider 需要 httpx。") from error
        if self.client is None:
            self.client = httpx.Client(timeout=self.config.timeout_seconds)
        headers = {"Authorization": f"Bearer {self.config.access_token}"}
        if self.config.app_id:
            headers["X-Application-Id"] = self.config.app_id
        if self.config.model:
            headers["X-Provider-Model"] = self.config.model
        try:
            response = self.client.post(
                self.config.endpoint,
                headers=headers,
                json={
                    "audio": base64.b64encode(pcm).decode("ascii"),
                    "sample_rate": self.sample_rate,
                    "segment_id": self.segment_id,
                    "turn_token": self.turn_token,
                },
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as error:
            raise SpeechUnavailableError("云端 Provider 请求失败。") from error
        text = _normalize_transcript(str(payload.get("text", payload.get("transcript", ""))))
        is_final = bool(payload.get("is_final", payload.get("final", False)))
        try:
            segment_id = int(payload.get("segment_id", self.segment_id))
        except (TypeError, ValueError):
            segment_id = self.segment_id
        if is_final:
            self.segment_id = max(self.segment_id + 1, segment_id + 1)
        if not text and not is_final:
            return None
        return RecognitionUpdate(
            text=text,
            is_final=is_final,
            segment_id=segment_id,
            is_filler_only=_is_filler_only(text),
        )

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None