"""Detached session recovery is explicit, private and ownership-safe."""

import asyncio
import threading
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.recovery import terminate_disconnected_session
from app.api.session import SessionRuntime
from app.config import Settings
from app.domain.session import SessionState, SpeechSession
from app.main import create_app


def make_app(tmp_path):
    return create_app(Settings(environment="test", data_dir=tmp_path, _env_file=None))


def upload(client):
    result = client.post(
        "/api/scripts/upload",
        files={"files": ("恢复测试.txt", "不能显示的秘密正文。".encode(), "text/plain")},
    )
    assert result.status_code == 201
    return result.json()[0]["id"]


def test_recovery_lists_only_detached_unfinished_metadata_and_ends_explicitly(tmp_path):
    app = make_app(tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/sessions/recoverable").json() == {"sessions": []}
        script_id = upload(client)
        with client.websocket_connect("/api/session") as socket:
            socket.send_json({"type": "prepare", "script_id": script_id})
            snapshot = socket.receive_json()["session"]
            identifier = snapshot["id"]
            assert client.get("/api/sessions/recoverable").json() == {"sessions": []}
            rejected = client.post(f"/api/sessions/{identifier}/terminate")
            assert rejected.status_code == 409
            assert rejected.json()["detail"]["code"] == "SESSION_CONNECTED"
            assert app.state.sessions[identifier].session.state == SessionState.READY
        response = client.get("/api/sessions/recoverable")
        assert response.status_code == 200
        assert "秘密正文" not in response.text
        assert "raw_transcript" not in response.text
        recovered = response.json()["sessions"]
        assert len(recovered) == 1
        assert set(recovered[0]) == {"session_id", "script_name", "state", "config_name"}
        assert recovered[0]["session_id"] == identifier
        runtime = app.state.sessions[identifier]
        generation = runtime.session.generation
        ended = client.post(f"/api/sessions/{identifier}/terminate")
        assert ended.status_code == 200
        assert ended.json() == {"session_id": identifier, "state": "TERMINATED"}
        assert runtime.session.generation == generation + 1
        assert runtime.stream is None
        assert runtime.refinement is None
        assert runtime.synthesis is None
        assert identifier not in app.state.sessions
        assert client.get("/api/sessions/recoverable").json() == {"sessions": []}
        assert client.post(f"/api/sessions/{identifier}/terminate").status_code == 404
        assert client.post("/api/settings/model-configs/local-default/activate").status_code == 200


def test_recovery_missing_session_is_not_found(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        response = client.post("/api/sessions/missing/terminate")
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "SESSION_NOT_FOUND"


def test_recovery_rechecks_new_socket_owner_after_waiting_for_runtime_lock():
    async def scenario():
        engine = SpeechSession("script", "大家好。")
        runtime = SessionRuntime(engine, "测试稿", object())
        state = SimpleNamespace(sessions={engine.id: runtime}, model_config_lock=threading.RLock())
        request = SimpleNamespace(app=SimpleNamespace(state=state))
        await runtime.lock.acquire()
        termination = asyncio.create_task(terminate_disconnected_session(request, engine.id))
        await asyncio.sleep(0)
        runtime.socket = object()
        runtime.lock.release()
        with pytest.raises(HTTPException) as error:
            await termination
        assert error.value.status_code == 409
        assert state.sessions[engine.id] is runtime
        assert engine.state == SessionState.READY
        runtime.socket = None
        assert (await terminate_disconnected_session(request, engine.id))["state"] == "TERMINATED"

    asyncio.run(scenario())


def test_recovery_rechecks_registry_identity_after_waiting_for_runtime_lock():
    async def scenario():
        engine = SpeechSession("script", "大家好。")
        runtime = SessionRuntime(engine, "测试稿", object())
        replacement = SessionRuntime(SpeechSession("other", "谢谢。"), "其他稿", object())
        state = SimpleNamespace(sessions={engine.id: runtime}, model_config_lock=threading.RLock())
        request = SimpleNamespace(app=SimpleNamespace(state=state))
        await runtime.lock.acquire()
        termination = asyncio.create_task(terminate_disconnected_session(request, engine.id))
        await asyncio.sleep(0)
        state.sessions[engine.id] = replacement
        runtime.lock.release()
        with pytest.raises(HTTPException) as error:
            await termination
        assert error.value.status_code == 404
        assert state.sessions[engine.id] is replacement
        assert engine.state == SessionState.READY

    asyncio.run(scenario())


def test_disconnected_termination_cancels_pending_work_and_drops_memory_audio():
    async def scenario():
        engine = SpeechSession("script", "大家好。")
        engine.handle("start", 0)
        engine.handle("manual_prompt", 1)
        runtime = SessionRuntime(engine, "测试稿", object())
        stream = SimpleNamespace(closed=False)
        stream.close = lambda: setattr(stream, "closed", True)
        runtime.stream = stream
        synthesis = asyncio.create_task(asyncio.sleep(60))
        refinement = asyncio.create_task(asyncio.sleep(60))
        runtime.synthesis = synthesis
        runtime.refinement = refinement
        runtime.audio_queue.put_nowait((engine.generation, bytes(640)))
        runtime.audio_pending = True
        state = SimpleNamespace(sessions={engine.id: runtime}, model_config_lock=threading.RLock())
        request = SimpleNamespace(app=SimpleNamespace(state=state))
        generation = engine.generation
        await terminate_disconnected_session(request, engine.id)
        await asyncio.gather(synthesis, refinement, return_exceptions=True)
        assert synthesis.cancelled()
        assert refinement.cancelled()
        assert stream.closed is True
        assert runtime.audio_queue.empty()
        assert runtime.audio_pending is False
        assert runtime.stream is None
        assert engine.generation == generation + 1
        assert engine.state == SessionState.TERMINATED

    asyncio.run(scenario())
