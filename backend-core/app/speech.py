"""Optional server-side speech adapters.

The web application owns the session state machine; this module keeps the
speech engines behind a small, testable interface.  Heavy model packages are
optional during development so the API can start before models are installed.
No microphone bytes are written to disk by these adapters.
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import math
import threading
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class SpeechUnavailableError(RuntimeError):
    """Raised when a requested speech capability is not ready."""


@dataclass(frozen=True, slots=True)
class RecognitionUpdate:
    """A recognition result emitted by a streaming recognizer."""

    text: str
    is_final: bool = False


class SpeechStream:
    """Streaming STT and VAD facade.

    ``sherpa-onnx`` is intentionally imported lazily.  A deployment that has
    not downloaded an acoustic model can still run the rest of the app and
    report a clear unavailable status instead of failing at import time.
    """

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
        """Feed signed 16-bit mono PCM and return the newest STT result.

        The adapter accepts the common sherpa-onnx ``OnlineStream`` shape but
        does not expose the engine's implementation details to the API layer.
        """
        if self._closed:
            return None
        if not pcm:
            return None
        recognizer = self._recognizer
        if recognizer is None:
            return None

        # The wrapper supports both a configured sherpa stream and a small
        # duck-typed test double used by unit tests.
        try:
            if hasattr(recognizer, "accept_waveform"):
                samples = _pcm16_to_float32(pcm)
                recognizer.accept_waveform(self.sample_rate, samples)
                if hasattr(recognizer, "is_ready") and recognizer.is_ready():
                    recognizer.decode_stream()
                text = _extract_text(recognizer)
                if text:
                    return RecognitionUpdate(
                        text=text,
                        is_final=bool(getattr(recognizer, "is_endpoint", lambda: False)()),
                    )
                return None
            result = recognizer(pcm)
            if result is None:
                return None
            if isinstance(result, RecognitionUpdate):
                return result
            if isinstance(result, str):
                return RecognitionUpdate(result)
            return RecognitionUpdate(
                text=str(getattr(result, "text", result)),
                is_final=bool(getattr(result, "is_final", False)),
            )
        except Exception as error:  # pragma: no cover - engine-specific errors
            raise SpeechUnavailableError("语音识别处理失败。") from error

    def is_speech(self, pcm: bytes) -> bool:
        """Return whether a 10/20/30 ms PCM frame contains speech."""
        if self._closed or not pcm:
            return False
        if self._vad is not None:
            try:
                return bool(self._vad.is_speech(pcm, self.sample_rate))
            except Exception:
                # Invalid frame sizes should not take down an active session;
                # use the energy fallback below.
                pass
        # Fallback keeps local development usable without a native VAD wheel.
        if len(pcm) % 2:
            return False
        samples = array("h", pcm)
        rms = math.sqrt(sum(value * value for value in samples) / len(samples))
        return rms >= 500

    def reset(self) -> None:
        """Reset the current stream endpoint when supported by the engine."""
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
    """Factory and server-side TTS entry point."""

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
        """Describe readiness without exposing model paths or audio data."""
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
        """Generate MP3 bytes in memory using edge-tts.

        The result is intentionally returned as bytes and never persisted as a
        file.  Callers may stream it to the browser or discard it immediately.
        """
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

    def __call__(self, pcm: bytes) -> RecognitionUpdate | None:
        import numpy as np

        waveform = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        self.stream.accept_waveform(self.sample_rate, waveform)
        while self.recognizer.is_ready(self.stream):
            self.recognizer.decode_stream(self.stream)
        text = self.recognizer.get_result(self.stream).strip()
        is_final = self.recognizer.is_endpoint(self.stream)
        if is_final:
            self.recognizer.reset(self.stream)
        return RecognitionUpdate(text, is_final) if text else None

    def reset(self) -> None:
        self.recognizer.reset(self.stream)


def _model_files(directory: Path) -> dict[str, Path] | None:
    result: dict[str, Path] = {}
    tokens = directory / "tokens.txt"
    if not tokens.is_file():
        return None
    result["tokens"] = tokens
    for component in ("encoder", "decoder", "joiner"):
        candidates = sorted(directory.glob(f"{component}*.onnx"))
        if not candidates:
            return None
        # The int8 export substantially reduces server memory; prefer it when
        # a downloaded archive includes both floating point and int8 exports.
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

        return hasattr(sherpa_onnx, "OnlineRecognizer")
    except (ImportError, OSError):
        return False
