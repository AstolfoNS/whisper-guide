"""Bounded WebSocket audio transport with pluggable speech wire protocols."""

from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from array import array
from collections import deque
from contextlib import suppress
from typing import Any
from urllib.parse import urlsplit

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus

from app.infrastructure.speech_protocols import (
    ProtocolTranscript,
    SpeechProtocolError,
    VolcengineSpeechProtocol,
)

# The transport owns bytes and lifecycle; adapters own provider-specific framing.
PROTOCOL_ADAPTERS = {"volcengine-ws": VolcengineSpeechProtocol}
_STREAM_SLOTS = threading.BoundedSemaphore(4)
_MAX_UPDATES = 32
_MAX_MESSAGE_BYTES = 1024 * 1024
# The library's DEBUG output includes handshake headers and binary frames.
# Always use this disabled logger, including when application debugging is on.
_WIRE_LOGGER = logging.getLogger("whisper_guide.private_speech_wire")
_WIRE_LOGGER.disabled = True


class WebSocketSpeechError(RuntimeError):
    """Safe diagnostics without retaining upstream exceptions or response bodies."""

    def __init__(
        self,
        error_code: str,
        *,
        http_status: int | None = None,
        provider_error_code: int | None = None,
    ) -> None:
        super().__init__("云端语音连接不可用，已使用本地识别保护会话。")
        self.error_code = error_code
        self.http_status = http_status
        self.provider_error_code = provider_error_code


class _NoRedirectConnect(connect):
    # X-Api-* credentials are custom headers and aren't stripped by the
    # library's ordinary cross-origin Authorization header redirect protection.
    def process_redirect(self, exc: Exception) -> Exception:
        return exc


def secure_websocket_endpoint(endpoint: str) -> bool:
    try:
        parts = urlsplit(endpoint)
        return bool(
            parts.hostname
            and not (parts.username or parts.password or parts.query or parts.fragment)
            and (
                parts.scheme == "wss"
                or (parts.scheme == "ws" and parts.hostname in {"localhost", "127.0.0.1", "::1"})
            )
        )
    except (ValueError, TypeError):
        return False


def websocket_configured(config: Any) -> bool:
    return bool(
        config.protocol in PROTOCOL_ADAPTERS
        and secure_websocket_endpoint(config.endpoint)
        and config.access_token.strip()
        and config.resource_id.strip()
        and config.auth_mode in {"api-key", "app-token"}
        and (config.auth_mode != "app-token" or config.app_id.strip())
    )


def _wire(config: Any, sample_rate: int = 16000) -> VolcengineSpeechProtocol:
    if not websocket_configured(config):
        raise WebSocketSpeechError("CONFIG_INCOMPLETE")
    optimized = urlsplit(config.endpoint).path.rstrip("/").endswith("/bigmodel_async")
    return PROTOCOL_ADAPTERS[config.protocol](
        sample_rate=sample_rate,
        enable_nonstream=config.enable_nonstream and optimized,
        end_window_ms=600,
    )


def _timeout(config: Any) -> float:
    return max(0.05, min(30.0, float(config.timeout_seconds)))


def _connection(config: Any, protocol: VolcengineSpeechProtocol):
    timeout = _timeout(config)
    return _NoRedirectConnect(
        config.endpoint,
        additional_headers=protocol.headers(
            credential=config.access_token,
            auth_mode=config.auth_mode,
            app_id=config.app_id,
            resource_id=config.resource_id,
        ),
        open_timeout=timeout,
        proxy=None
        if urlsplit(config.endpoint).hostname in {"localhost", "127.0.0.1", "::1"}
        else True,
        close_timeout=0.3,
        ping_interval=10,
        ping_timeout=timeout,
        compression=None,
        logger=_WIRE_LOGGER,
        max_size=_MAX_MESSAGE_BYTES,
        max_queue=4,
        write_limit=32000,
    )


def _diagnostic(error: Exception) -> WebSocketSpeechError:
    if isinstance(error, WebSocketSpeechError):
        return error
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return WebSocketSpeechError("TIMEOUT")
    if isinstance(error, SpeechProtocolError):
        return WebSocketSpeechError(
            "PROVIDER_ERROR" if error.code is not None else "INVALID_RESPONSE",
            provider_error_code=error.code,
        )
    if isinstance(error, InvalidStatus):
        return WebSocketSpeechError("HTTP_ERROR", http_status=error.response.status_code)
    if isinstance(error, (ConnectionClosed, InvalidHandshake, OSError)):
        return WebSocketSpeechError("NETWORK_ERROR")
    return WebSocketSpeechError("UNKNOWN_ERROR")


async def _finish(socket: Any, protocol: VolcengineSpeechProtocol) -> None:
    # A pause never waits for a remote final; send an empty last packet as a
    # best-effort release of upstream resources, then discard every late result.
    with suppress(Exception):
        async with asyncio.timeout(0.2):
            await socket.send(protocol.audio_frame(b"", final=True))


async def probe_websocket(config: Any) -> dict[str, Any]:
    """Verify auth and binary initialization without using a local fallback."""
    started = time.monotonic()
    try:
        protocol = _wire(config)
        async with asyncio.timeout(_timeout(config)):
            async with _connection(config, protocol) as socket:
                final_sent = False
                try:
                    await socket.send(protocol.initial_frame())
                    await socket.send(protocol.audio_frame(bytes(6400)))
                    await asyncio.sleep(0.2)
                    await socket.send(protocol.audio_frame(b"", final=True))
                    final_sent = True
                    # Verify the audio terminal response as well as the initial
                    # acknowledgement, so a later protocol error cannot be hidden.
                    while not protocol.last_package:
                        protocol.decode(await socket.recv())
                finally:
                    if not final_sent and not protocol.last_package:
                        await _finish(socket, protocol)
        return {"ok": True, "latency_ms": int((time.monotonic() - started) * 1000)}
    except Exception as error:
        if isinstance(error, SpeechProtocolError) and error.code == 45000002:
            # This probe intentionally sends silence. Only this documented
            # empty-audio outcome confirms reachability without claiming ASR quality.
            return {
                "ok": True,
                "probe_empty_audio": True,
                "latency_ms": int((time.monotonic() - started) * 1000),
            }
        safe = _diagnostic(error)
        result: dict[str, Any] = {
            "ok": False,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error_code": safe.error_code,
        }
        if safe.http_status is not None:
            result["http_status"] = safe.http_status
        if safe.provider_error_code is not None:
            result["provider_error_code"] = safe.provider_error_code
        return result


class WebSocketStreamingRecognizer:
    """Keep one binary connection per stream without blocking audio/control.

    PCM is bounded to one second, output to 32 updates, and at most four network
    threads are live. Closing invalidates queues immediately and cancels network
    work. No PCM, transcripts, credentials or upstream bodies are logged/written.
    """

    def __init__(self, config: Any, sample_rate: int, turn_token: int | None = None) -> None:
        self.config = config
        self.sample_rate = sample_rate
        self.turn_token = turn_token
        self.protocol = _wire(config, sample_rate)
        self._lock = threading.Lock()
        self._buffer = bytearray()
        self._updates: deque[ProtocolTranscript] = deque()
        self._closed = False
        self._ended = False
        self._failure: WebSocketSpeechError | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[None] | None = None
        self._pending_voice_at: float | None = None
        self._chunk_bytes = sample_rate * 2 // 5  # 200 ms PCM16
        self._max_buffer_bytes = sample_rate * 2
        self._done = threading.Event()
        if not _STREAM_SLOTS.acquire(blocking=False):
            raise WebSocketSpeechError("NETWORK_ERROR")
        self._thread = threading.Thread(target=self._run_thread, name="cloud-stt-ws", daemon=True)
        try:
            self._thread.start()
        except Exception:
            _STREAM_SLOTS.release()
            raise

    def __call__(self, pcm: bytes) -> ProtocolTranscript | None:
        failure = None
        with self._lock:
            if self._closed:
                return None
            if self._failure is not None:
                raise self._failure
            if self._ended:
                if self._updates:
                    return self._updates.popleft()
                failure = WebSocketSpeechError("NETWORK_ERROR")
            elif len(pcm) % 2 or len(self._buffer) + len(pcm) > self._max_buffer_bytes:
                failure = WebSocketSpeechError("NETWORK_ERROR")
            else:
                self._buffer.extend(pcm)
                return self._updates.popleft() if self._updates else None
        self._fail(failure)
        self._cancel()
        raise failure

    def _cancel(self) -> None:
        with self._lock:
            loop, task = self._loop, self._task
        if loop is not None and task is not None:
            with suppress(RuntimeError):
                loop.call_soon_threadsafe(task.cancel)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._buffer.clear()
            self._updates.clear()
            self._pending_voice_at = None
        self._cancel()

    def _fail(self, failure: WebSocketSpeechError) -> None:
        with self._lock:
            if not self._closed:
                self._failure = failure
                self._buffer.clear()
                self._updates.clear()

    def _run_thread(self) -> None:
        try:
            asyncio.run(self._run())
        finally:
            with self._lock:
                self._loop = None
                self._task = None
                self._buffer.clear()
            _STREAM_SLOTS.release()
            self._done.set()

    async def _run(self) -> None:
        tasks: list[asyncio.Task[None]] = []
        with self._lock:
            self._loop = asyncio.get_running_loop()
            self._task = asyncio.current_task()
            if self._closed or self._failure:
                return
        try:
            async with _connection(self.config, self.protocol) as socket:
                try:
                    async with asyncio.timeout(_timeout(self.config)):
                        await socket.send(self.protocol.initial_frame())
                    tasks = [
                        asyncio.create_task(self._send(socket)),
                        asyncio.create_task(self._receive(socket)),
                        asyncio.create_task(self._watch()),
                    ]
                    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        task.result()
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    if not self.protocol.last_package:
                        await _finish(socket, self.protocol)
        except asyncio.CancelledError:
            pass
        except Exception as error:
            self._fail(_diagnostic(error))

    async def _send(self, socket: Any) -> None:
        sent_at = 0.0
        while True:
            await asyncio.sleep(max(0.02, 0.2 - (time.monotonic() - sent_at)))
            with self._lock:
                if self._closed or self._failure:
                    return
                if len(self._buffer) < self._chunk_bytes:
                    continue
                chunk = bytes(self._buffer[: self._chunk_bytes])
                del self._buffer[: self._chunk_bytes]
                if self._pending_voice_at is None and _voiced(chunk):
                    self._pending_voice_at = time.monotonic()
            async with asyncio.timeout(_timeout(self.config)):
                await socket.send(self.protocol.audio_frame(chunk))
            sent_at = time.monotonic()

    async def _receive(self, socket: Any) -> None:
        async for message in socket:
            updates = self.protocol.decode(message)
            with self._lock:
                if self._closed:
                    return
                if updates:
                    self._pending_voice_at = None
                for update in updates:
                    if (
                        self._updates
                        and not self._updates[-1].is_final
                        and self._updates[-1].segment_id == update.segment_id
                    ):
                        self._updates[-1] = update
                    else:
                        self._updates.append(update)
                    if len(self._updates) > _MAX_UPDATES:
                        raise WebSocketSpeechError("NETWORK_ERROR")
                if self.protocol.last_package:
                    self._ended = True
                    return
        raise WebSocketSpeechError("NETWORK_ERROR")

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(0.05)
            with self._lock:
                if self._closed:
                    return
                pending = self._pending_voice_at
            # Async ASR intentionally sends no message when silence doesn't
            # change a hypothesis. Only voiced audio starts a result deadline.
            if pending is not None and time.monotonic() - pending > _timeout(self.config):
                raise WebSocketSpeechError("TIMEOUT")


def _voiced(pcm: bytes) -> bool:
    if not pcm:
        return False
    samples = array("h", pcm)
    return bool(
        samples and math.sqrt(sum(value * value for value in samples) / len(samples)) >= 500
    )
