"""Deterministic audio pacing checks without sockets, models or wall-clock waits."""

import asyncio
import threading
from collections import deque
from itertools import pairwise
from types import SimpleNamespace

import pytest

import app.infrastructure.speech_websocket as transport
from app.infrastructure.speech_websocket import WebSocketStreamingRecognizer


class AudioClock:
    def __init__(self, *, start_delay=0, gap=None):
        self.now = 100.0
        self.next_frame = self.now + start_delay + 0.02
        self.gap = gap
        self.stream = None
        self.failure = None
        self.peak_buffer = 0

    def advance(self, seconds):
        target = self.now + seconds
        while self.next_frame <= target + 1e-9:
            self.now = self.next_frame
            in_gap = self.gap and self.gap[0] <= self.now < self.gap[1]
            if not in_gap and self.failure is None:
                try:
                    self.stream(bytes(640))  # one live 20 ms PCM16 frame
                except transport.WebSocketSpeechError as error:
                    self.failure = error
                self.peak_buffer = max(self.peak_buffer, len(self.stream._buffer))
            self.next_frame += 0.02
        self.now = target

    async def sleep(self, seconds):
        self.advance(seconds)


class SlowSocket:
    def __init__(self, clock, send_seconds, limit=30):
        self.clock = clock
        self.send_seconds = send_seconds
        self.limit = limit
        self.sent_at = []
        self.packets = []

    async def send(self, packet):
        self.sent_at.append(self.clock.now)
        self.packets.append(packet)
        self.clock.advance(self.send_seconds)
        if len(self.sent_at) >= self.limit:
            self.clock.stream._closed = True


def recognizer(clock):
    # Construct only transport state; no connection thread or stream slot is used.
    stream = object.__new__(WebSocketStreamingRecognizer)
    stream.config = SimpleNamespace(timeout_seconds=0.5)
    stream.protocol = SimpleNamespace(audio_frame=lambda pcm: pcm)
    stream._lock = threading.Lock()
    stream._buffer = bytearray()
    stream._updates = deque()
    stream._closed = False
    stream._ended = False
    stream._failure = None
    stream._pending_voice_at = None
    stream._chunk_bytes = 6400
    stream._max_buffer_bytes = 32000
    stream._loop = None
    stream._task = None
    clock.stream = stream
    return stream


def run_sender(monkeypatch, clock, send_seconds, *, limit=30):
    stream = recognizer(clock)
    socket = SlowSocket(clock, send_seconds, limit)
    monkeypatch.setattr(transport, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(
        transport, "asyncio", SimpleNamespace(sleep=clock.sleep, timeout=asyncio.timeout)
    )
    asyncio.run(stream._send(socket))
    return stream, socket


@pytest.mark.parametrize("send_seconds", [0.02, 0.08])
def test_send_time_does_not_accumulate_into_live_audio_pacing(monkeypatch, send_seconds):
    clock = AudioClock()
    stream, socket = run_sender(monkeypatch, clock, send_seconds)
    assert clock.failure is None
    assert len(socket.sent_at) == 30
    assert len(socket.packets) == 30
    assert all(len(packet) == 6400 for packet in socket.packets)
    assert socket.sent_at[-1] - socket.sent_at[0] == pytest.approx(29 * 0.2)
    assert clock.peak_buffer <= 7040  # scheduler rounding may retain one 20 ms frame
    assert stream._pending_voice_at is None  # silence never starts a result timeout


def test_idle_start_and_audio_gap_do_not_trigger_catchup_bursts(monkeypatch):
    clock = AudioClock(start_delay=2, gap=(104.0, 106.0))
    _, socket = run_sender(monkeypatch, clock, 0.08)
    assert clock.failure is None
    assert socket.sent_at[0] >= 102.2 - 1e-9
    intervals = [later - earlier for earlier, later in pairwise(socket.sent_at)]
    assert min(intervals) >= 0.2 - 1e-9
    assert max(intervals) >= 1.8
    assert clock.peak_buffer <= 7040


def test_link_slower_than_audio_rate_still_hits_bounded_fallback(monkeypatch):
    clock = AudioClock()
    stream, socket = run_sender(monkeypatch, clock, 0.3, limit=100)
    assert clock.failure is not None
    assert clock.failure.error_code == "NETWORK_ERROR"
    assert stream._failure is clock.failure
    assert clock.peak_buffer <= 32000
    assert len(socket.sent_at) < 100
    assert not stream._buffer


def test_single_stalled_send_keeps_its_timeout(monkeypatch):
    clock = AudioClock()
    stream = recognizer(clock)
    stream.config.timeout_seconds = 0.05
    monkeypatch.setattr(transport, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(
        transport, "asyncio", SimpleNamespace(sleep=clock.sleep, timeout=asyncio.timeout)
    )

    async def stalled_send(packet):
        assert len(packet) == 6400
        await asyncio.sleep(1)

    with pytest.raises(TimeoutError):
        asyncio.run(stream._send(SimpleNamespace(send=stalled_send)))
