"""Offline transport readiness, acoustic watchdog ownership, and safe stage diagnostics."""

import asyncio
import threading
from array import array
from collections import deque

import pytest

import app.infrastructure.speech_websocket as transport
from app.infrastructure.speech import RemoteProviderConfig
from app.infrastructure.speech_websocket import WebSocketSpeechError, WebSocketStreamingRecognizer

PRIVATE = "synthetic-private-upstream-value"


class Socket:
    def __init__(self, *, initialize_gate=None, stall_audio=False):
        self.initialize_gate = initialize_gate
        self.stall_audio = stall_audio
        self.initial_entered = threading.Event()
        self.sent = 0
        self.messages = deque()

    async def send(self, packet):
        self.sent += 1
        if self.sent == 1:
            self.initial_entered.set()
            while self.initialize_gate is not None and not self.initialize_gate.is_set():
                await asyncio.sleep(0.005)
        elif self.stall_audio:
            await asyncio.sleep(60)

    async def __aiter__(self):
        while True:
            await asyncio.sleep(0.005)
            if self.messages:
                yield self.messages.popleft()


def config(timeout=0.2):
    return RemoteProviderConfig(
        id="cloud",
        label="offline readiness",
        protocol="volcengine-ws",
        endpoint="ws://127.0.0.1:12345/bigmodel_async",
        access_token=PRIVATE,
        auth_mode="api-key",
        resource_id="synthetic-resource",
        timeout_seconds=timeout,
    )


def fake_connection(monkeypatch, *, connect_gate=None, socket=None, error=None):
    peer = socket or Socket()

    class Connection:
        async def __aenter__(self):
            while connect_gate is not None and not connect_gate.is_set():
                await asyncio.sleep(0.005)
            if error:
                raise error
            return peer

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(transport, "_connection", lambda *args: Connection())
    return peer


async def closed(recognizer):
    recognizer.close()
    assert await asyncio.to_thread(recognizer._done.wait, 2)


async def failure(recognizer):
    async with asyncio.timeout(2):
        while recognizer._failure is None:
            await asyncio.sleep(0.005)
    return recognizer._failure


def test_delayed_handshake_has_zero_audio_backlog_until_ready(monkeypatch):
    async def scenario():
        gate = threading.Event()
        fake_connection(monkeypatch, connect_gate=gate)
        recognizer = WebSocketStreamingRecognizer(config(2), 16000)
        waiter = asyncio.create_task(recognizer.wait_ready(1.8))
        try:
            await asyncio.sleep(1.05)
            assert not recognizer.ready
            assert not waiter.done()
            assert recognizer._failure is None
            assert len(recognizer._buffer) == 0
            gate.set()
            assert await waiter
            assert recognizer.ready
        finally:
            await closed(recognizer)

    asyncio.run(scenario())


def test_ready_requires_initial_frame_send_to_finish(monkeypatch):
    async def scenario():
        gate = threading.Event()
        socket = fake_connection(monkeypatch, socket=Socket(initialize_gate=gate))
        recognizer = WebSocketStreamingRecognizer(config(1), 16000)
        waiter = asyncio.create_task(recognizer.wait_ready())
        try:
            assert await asyncio.to_thread(socket.initial_entered.wait, 1)
            assert not recognizer.ready
            assert not waiter.done()
            gate.set()
            assert await waiter
            assert recognizer.ready
        finally:
            await closed(recognizer)

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["close", "timeout"])
def test_close_or_timeout_wakes_readiness_waiter(monkeypatch, action):
    async def scenario():
        gate = threading.Event()
        fake_connection(monkeypatch, connect_gate=gate)
        recognizer = WebSocketStreamingRecognizer(config(1), 16000)
        waiter = asyncio.create_task(recognizer.wait_ready(0.05 if action == "timeout" else 1))
        try:
            if action == "close":
                await asyncio.sleep(0.01)
                recognizer.close()
            with pytest.raises(WebSocketSpeechError) as caught:
                await waiter
            expected = (
                ("TIMEOUT", "connect") if action == "timeout" else ("NETWORK_ERROR", "closed")
            )
            assert (caught.value.error_code, caught.value.stage) == expected
            assert not recognizer.ready
        finally:
            await closed(recognizer)

    asyncio.run(scenario())


def test_cancelled_readiness_wait_does_not_poison_live_connection(monkeypatch):
    async def scenario():
        gate = threading.Event()
        fake_connection(monkeypatch, connect_gate=gate)
        recognizer = WebSocketStreamingRecognizer(config(1), 16000)
        waiter = asyncio.create_task(recognizer.wait_ready())
        try:
            await asyncio.sleep(0.01)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert recognizer._failure is None
            gate.set()
            assert await recognizer.wait_ready()
            assert recognizer.ready
        finally:
            await closed(recognizer)

    asyncio.run(scenario())


def test_connection_failure_has_safe_stage_and_no_upstream_text(monkeypatch):
    async def scenario():
        fake_connection(monkeypatch, error=OSError(PRIVATE))
        recognizer = WebSocketStreamingRecognizer(config(), 16000)
        try:
            with pytest.raises(WebSocketSpeechError) as caught:
                await recognizer.wait_ready()
            assert caught.value.error_code == "NETWORK_ERROR"
            assert caught.value.stage == "connect"
            assert PRIVATE not in str(caught.value)
            assert PRIVATE not in repr(caught.value.__dict__)
            assert not recognizer.ready
        finally:
            await closed(recognizer)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "pcm",
    [
        array("h", [1200] * 320).tobytes(),
        array("h", [-1200, 1200] * 160).tobytes(),
    ],
    ids=["dc-offset", "stationary-energy"],
)
def test_pcm_energy_alone_never_arms_result_deadline(monkeypatch, pcm):
    async def scenario():
        fake_connection(monkeypatch)
        recognizer = WebSocketStreamingRecognizer(config(0.05), 16000)
        try:
            assert await recognizer.wait_ready()
            for _ in range(25):
                assert recognizer(pcm) is None
                await asyncio.sleep(0.02)
            assert recognizer._pending_voice_at is None
            assert recognizer._failure is None
            assert recognizer.ready
        finally:
            await closed(recognizer)

    asyncio.run(scenario())


def test_confirmed_voice_still_arms_bounded_result_deadline(monkeypatch):
    async def scenario():
        fake_connection(monkeypatch)
        recognizer = WebSocketStreamingRecognizer(config(0.05), 16000)
        try:
            assert await recognizer.wait_ready()
            recognizer.notify_voice()
            result = await failure(recognizer)
            assert (result.error_code, result.stage) == ("TIMEOUT", "result")
            assert not recognizer.ready
        finally:
            await closed(recognizer)

    asyncio.run(scenario())


def test_audio_send_timeout_has_send_stage(monkeypatch):
    async def scenario():
        fake_connection(monkeypatch, socket=Socket(stall_audio=True))
        recognizer = WebSocketStreamingRecognizer(config(0.05), 16000)
        try:
            assert await recognizer.wait_ready()
            recognizer(bytes(6400))
            result = await failure(recognizer)
            assert (result.error_code, result.stage) == ("TIMEOUT", "send")
        finally:
            await closed(recognizer)

    asyncio.run(scenario())


def test_buffer_bound_remains_one_second_with_specific_stage(monkeypatch):
    async def scenario():
        gate = threading.Event()
        fake_connection(monkeypatch, connect_gate=gate)
        recognizer = WebSocketStreamingRecognizer(config(2), 16000)
        try:
            recognizer(bytes(32000))
            with pytest.raises(WebSocketSpeechError) as caught:
                recognizer(bytes(2))
            assert (caught.value.error_code, caught.value.stage) == ("NETWORK_ERROR", "buffer")
            assert len(recognizer._buffer) == 0
            with pytest.raises(WebSocketSpeechError) as waiter_error:
                await recognizer.wait_ready()
            assert waiter_error.value.stage == "buffer"
        finally:
            await closed(recognizer)

    asyncio.run(scenario())


def test_initialize_timeout_has_initialize_stage(monkeypatch):
    async def scenario():
        gate = threading.Event()
        fake_connection(monkeypatch, socket=Socket(initialize_gate=gate))
        recognizer = WebSocketStreamingRecognizer(config(0.05), 16000)
        try:
            with pytest.raises(WebSocketSpeechError) as caught:
                await recognizer.wait_ready(0.5)
            assert (caught.value.error_code, caught.value.stage) == ("TIMEOUT", "initialize")
        finally:
            await closed(recognizer)

    asyncio.run(scenario())


def test_arbitrary_stage_is_not_reflected():
    error = WebSocketSpeechError("NETWORK_ERROR", stage=PRIVATE)
    assert error.stage == "unknown"
    assert PRIVATE not in repr(error.__dict__)
