"""Real local WebSocket peers exercise framing, lifecycle and safe fallback."""

import asyncio
import gzip
import json
import struct
import time
from array import array
from http import HTTPStatus
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from app.api.session import SessionRuntime
from app.domain.session import SpeechSession
from app.infrastructure.speech import (
    FallbackSpeechStream,
    RecognitionUpdate,
    RemoteProviderConfig,
    SpeechStream,
    WebSocketStreamingProvider,
)
from app.infrastructure.speech_websocket import (
    WebSocketSpeechError,
    WebSocketStreamingRecognizer,
    probe_websocket,
    secure_websocket_endpoint,
)

PRIVATE = "synthetic-speech-private-value"
PCM = array("h", [1000] * 320).tobytes()


def config(port, **overrides):
    values = {
        "id": "cloud",
        "label": "test",
        "protocol": "volcengine-ws",
        "endpoint": f"ws://127.0.0.1:{port}/bigmodel_async",
        "access_token": PRIVATE,
        "auth_mode": "api-key",
        "resource_id": "volc.seedasr.sauc.duration",
        "timeout_seconds": 2,
    }
    values.update(overrides)
    return RemoteProviderConfig(**values)


def server_frame(payload, *, final=False, error=None):
    if error is not None:
        body = PRIVATE.encode()
        return bytes((0x11, 0xF0, 0x10, 0)) + struct.pack(">II", error, len(body)) + body
    body = gzip.compress(json.dumps(payload).encode(), mtime=0)
    return (
        bytes((0x11, 0x93 if final else 0x91, 0x11, 0))
        + struct.pack(">iI", -1 if final else 1, len(body))
        + body
    )


def client_payload(frame):
    assert len(frame) >= 8
    assert struct.unpack_from(">I", frame, 4)[0] == len(frame) - 8
    return gzip.decompress(frame[8:])


@pytest.mark.parametrize(
    "endpoint,expected",
    [
        ("wss://speech.example/asr", True),
        ("ws://127.0.0.1:1234/asr", True),
        ("ws://localhost/asr", True),
        ("ws://[::1]/asr", True),
        ("ws://speech.example/asr", False),
        ("https://speech.example/asr", False),
        ("wss://user:secret@speech.example/asr", False),
        ("wss://speech.example/asr?key=secret", False),
    ],
)
def test_audio_endpoint_security(endpoint, expected):
    assert secure_websocket_endpoint(endpoint) is expected


def test_bidirectional_connection_partial_final_and_fast_close():
    async def scenario():
        packets = []
        ended = asyncio.Event()

        async def handler(socket):
            assert socket.request.headers["X-Api-Key"] == PRIVATE
            assert "Authorization" not in socket.request.headers
            initial = json.loads(client_payload(await socket.recv()))
            assert initial["audio"] == {
                "format": "pcm",
                "codec": "raw",
                "rate": 16000,
                "bits": 16,
                "channel": 1,
            }
            assert initial["request"]["result_type"] == "single"
            assert initial["request"]["enable_nonstream"] is True
            await socket.send(server_frame({}))
            try:
                async for frame in socket:
                    if frame[1] & 2:
                        ended.set()
                        await socket.send(server_frame({}, final=True))
                        return
                    packets.append(client_payload(frame))
                    if len(packets) <= 2:
                        await socket.send(
                            server_frame(
                                {
                                    "result": {
                                        "utterances": [
                                            {
                                                "text": "大家" if len(packets) == 1 else "大家好",
                                                "start_time": 0,
                                                "end_time": 400,
                                                "definite": len(packets) == 2,
                                            }
                                        ]
                                    }
                                }
                            )
                        )
            except ConnectionClosed:
                return

        async with serve(handler, "127.0.0.1", 0) as server:
            recognizer = WebSocketStreamingRecognizer(
                config(server.sockets[0].getsockname()[1]), 16000, 7
            )
            updates = []
            try:
                for _ in range(35):
                    update = recognizer(PCM)
                    if update:
                        updates.append(update)
                    await asyncio.sleep(0.02)
                assert any(update.text == "大家" and not update.is_final for update in updates)
                assert any(update.text == "大家好" and update.is_final for update in updates)
                assert {update.segment_id for update in updates} == {0}
                assert all(len(packet) == 6400 for packet in packets)
            finally:
                began = time.monotonic()
                recognizer.close()
                recognizer.close()
                assert time.monotonic() - began < 0.1
                assert await asyncio.to_thread(recognizer._done.wait, 2)
            assert recognizer(PCM) is None
            assert len(recognizer._buffer) == len(recognizer._updates) == 0
            assert ended.is_set()

    asyncio.run(scenario())


@pytest.mark.parametrize("business_error", [None, 45000001, 45000002])
def test_probe_waits_after_ack_and_classifies_terminal_response(business_error):
    async def scenario():
        async def handler(socket):
            await socket.recv()
            await socket.send(server_frame({}))
            async for frame in socket:
                if frame[1] & 2:
                    await socket.send(server_frame({}, final=True, error=business_error))
                    return

        async with serve(handler, "127.0.0.1", 0) as server:
            result = await probe_websocket(config(server.sockets[0].getsockname()[1]))
        assert PRIVATE not in str(result)
        if business_error == 45000001:
            assert result["ok"] is False
            assert result["error_code"] == "PROVIDER_ERROR"
            assert result["provider_error_code"] == business_error
        else:
            assert result["ok"] is True
            assert result.get("probe_empty_audio", False) is (business_error == 45000002)

    asyncio.run(scenario())


def test_probe_does_not_follow_redirect_or_send_key_to_target():
    async def scenario():
        targets = []

        async def target(socket):
            targets.append(socket.request.headers)

        async with serve(target, "127.0.0.1", 0) as destination:

            async def redirect(connection, request):
                response = connection.respond(HTTPStatus.FOUND, PRIVATE)
                response.headers["Location"] = (
                    f"ws://127.0.0.1:{destination.sockets[0].getsockname()[1]}/asr"
                )
                return response

            async with serve(target, "127.0.0.1", 0, process_request=redirect) as source:
                result = await probe_websocket(config(source.sockets[0].getsockname()[1]))
        assert result["ok"] is False
        assert result["http_status"] == 302
        assert targets == []
        assert PRIVATE not in str(result)

    asyncio.run(scenario())


def test_silence_without_changed_results_does_not_trigger_speech_deadline():
    async def scenario():
        async def handler(socket):
            await socket.recv()
            await socket.send(server_frame({}))
            try:
                async for _ in socket:
                    pass
            except ConnectionClosed:
                pass

        async with serve(handler, "127.0.0.1", 0) as server:
            recognizer = WebSocketStreamingRecognizer(
                config(server.sockets[0].getsockname()[1], timeout_seconds=0.15), 16000
            )
            try:
                for _ in range(22):
                    assert recognizer(bytes(640)) is None
                    await asyncio.sleep(0.02)
                assert recognizer._failure is None
            finally:
                recognizer.close()
                assert await asyncio.to_thread(recognizer._done.wait, 2)

    asyncio.run(scenario())


def test_unresponsive_cloud_after_voiced_audio_times_out_to_warm_local():
    async def scenario():
        async def handler(socket):
            await socket.recv()
            await socket.send(server_frame({}))
            try:
                async for _ in socket:
                    pass
            except ConnectionClosed:
                pass

        async with serve(handler, "127.0.0.1", 0) as server:
            provider = WebSocketStreamingProvider(
                config(server.sockets[0].getsockname()[1], timeout_seconds=0.15),
                opt_in=True,
                sample_rate=16000,
            )
            local = SpeechStream(recognizer=lambda pcm: RecognitionUpdate("大家好", segment_id=0))
            remote = provider.create_stream(vad=None, turn_token=8)
            recognizer = remote._recognizer
            stream = FallbackSpeechStream(remote, local)
            try:
                for _ in range(35):
                    stream.accept_pcm(PCM)
                    if stream.fallback_active:
                        break
                    await asyncio.sleep(0.02)
                assert stream.fallback_active
                assert stream.effective_provider == "local"
                assert not local._closed
                assert recognizer._failure.error_code == "TIMEOUT"
            finally:
                stream.close()
                assert await asyncio.to_thread(recognizer._done.wait, 2)

    asyncio.run(scenario())


def test_pcm_backlog_is_bounded_and_close_cancels_incomplete_handshake():
    async def scenario():
        blocker = asyncio.Event()

        async def delay(connection, request):
            await blocker.wait()

        async def handler(socket):
            await socket.wait_closed()

        async with serve(handler, "127.0.0.1", 0, process_request=delay) as server:
            recognizer = WebSocketStreamingRecognizer(
                config(server.sockets[0].getsockname()[1]), 16000
            )
            try:
                recognizer(bytes(32000))
                with pytest.raises(WebSocketSpeechError):
                    recognizer(bytes(2))
                assert len(recognizer._buffer) == 0
            finally:
                recognizer.close()
                blocker.set()
                assert await asyncio.to_thread(recognizer._done.wait, 2)

    asyncio.run(scenario())


def test_fallback_without_new_text_is_published_without_changing_domain_positions():
    class QuietSwitch:
        effective_provider = "cloud"
        fallback_active = False

        def is_speech(self, pcm):
            return False

        def accept_pcm(self, pcm):
            self.effective_provider = "local"
            self.fallback_active = True
            return None

    async def scenario():
        engine = SpeechSession("script", "大家好，今天开始。", 3000)
        engine.handle("start", 0)
        runtime = SessionRuntime(engine, "test", SimpleNamespace(), "cloud")
        runtime.stream = QuietSwitch()
        runtime.audio_ready = True
        runtime.publish = AsyncMock()
        before = (engine.reading_position, engine.prompt_position, engine.generation, engine.state)
        await runtime.audio(bytes(640), engine.generation)
        runtime.publish.assert_awaited_once()
        assert runtime.snapshot()["stt_fallback"] is True
        assert runtime.snapshot()["stt_provider_active"] == "local"
        assert before == (
            engine.reading_position,
            engine.prompt_position,
            engine.generation,
            engine.state,
        )

    asyncio.run(scenario())
