"""Deleting library records preserves active snapshots and serializes preparation."""

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_session_api import TestSpeech as SessionTestSpeech
from test_session_api import receive_state

import app.api.scripts as scripts_api
import app.api.session as session_api
from app.api.session import SessionRuntime, runtimes
from app.config import Settings
from app.domain.session import SpeechSession
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(environment="test", data_dir=tmp_path, _env_file=None))
    app.state.speech_services = SessionTestSpeech()
    with TestClient(app) as test_client:
        yield test_client


def upload(client, name="script.txt", text="First sentence. Next sentence."):
    response = client.post(
        "/api/scripts/upload", files=[("files", (name, text.encode(), "text/plain"))]
    )
    assert response.status_code == 201
    return response.json()[0]


class ConnectedSocket:
    async def close(self, code=1001):
        pass


def register_session(client, script, state, *, connected=False):
    session = SpeechSession(script["id"], script["content"], silence_timeout_ms=1000)
    if state != "READY":
        session.handle("start", 0)
    if state == "PAUSED":
        session.handle("pause", 1)
    elif state == "PROMPTING":
        session.handle("tick", 1000)
    elif state == "TERMINATED":
        session.handle("end", 1)
    runtime = SessionRuntime(session, script["name"], SimpleNamespace())
    runtime.socket = ConnectedSocket() if connected else None
    with client.app.state.model_config_lock:
        runtimes(client.app)[session.id] = runtime
    return runtime


def test_delete_returns_empty_204_and_removes_only_the_requested_record(client):
    first = upload(client, "same.txt", "First record.")
    second = upload(client, "same.txt", "Second record.")
    response = client.delete(f"/api/scripts/{first['id']}")
    assert response.status_code == 204
    assert response.content == b""
    assert client.get(f"/api/scripts/{first['id']}").status_code == 404
    assert client.get(f"/api/scripts/{second['id']}").json()["content"] == "Second record."
    assert [item["id"] for item in client.get("/api/scripts").json()] == [second["id"]]
    missing = client.delete(f"/api/scripts/{first['id']}")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "SCRIPT_NOT_FOUND"


@pytest.mark.parametrize("state", ["READY", "LISTENING", "PROMPTING", "PAUSED"])
@pytest.mark.parametrize("connected", [True, False])
def test_unfinished_session_blocks_deletion_without_changing_snapshot(client, state, connected):
    script = upload(client)
    runtime = register_session(client, script, state, connected=connected)
    before = runtime.snapshot()
    response = client.delete(f"/api/scripts/{script['id']}")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "SCRIPT_IN_USE"
    assert response.json()["detail"]["message"]
    assert runtime.snapshot() == before
    assert client.get(f"/api/scripts/{script['id']}").json() == script


def test_any_unfinished_reference_blocks_deletion_even_after_another_session_ends(client):
    script = upload(client)
    register_session(client, script, "TERMINATED")
    register_session(client, script, "READY")
    assert client.delete(f"/api/scripts/{script['id']}").status_code == 409


def test_unrelated_session_does_not_block_deletion_or_change_its_snapshot(client):
    protected = upload(client, "protected.txt")
    removable = upload(client, "removable.txt")
    runtime = register_session(client, protected, "LISTENING", connected=True)
    before = runtime.snapshot()
    assert client.delete(f"/api/scripts/{removable['id']}").status_code == 204
    assert runtime.snapshot() == before
    assert client.get(f"/api/scripts/{protected['id']}").status_code == 200


def test_terminated_session_allows_deletion_and_keeps_its_captured_content(client):
    script = upload(client)
    runtime = register_session(client, script, "TERMINATED", connected=True)
    before = runtime.snapshot()
    assert client.delete(f"/api/scripts/{script['id']}").status_code == 204
    assert runtime.snapshot() == before


def test_deleted_record_stays_deleted_after_restart(tmp_path):
    settings = Settings(environment="test", data_dir=tmp_path, _env_file=None)
    with TestClient(create_app(settings)) as first_client:
        deleted = upload(first_client)
        kept = upload(first_client, "kept.txt")
        assert first_client.delete(f"/api/scripts/{deleted['id']}").status_code == 204
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(f"/api/scripts/{deleted['id']}").status_code == 404
        assert restarted.get(f"/api/scripts/{kept['id']}").status_code == 200


def test_prepare_cannot_capture_a_previously_deleted_record(client):
    script = upload(client)
    assert client.delete(f"/api/scripts/{script['id']}").status_code == 204
    with client.websocket_connect("/api/session") as websocket:
        websocket.send_json({"type": "prepare", "script_id": script["id"]})
        assert websocket.receive_json()["type"] == "error"
    assert not runtimes(client.app)


class ObservedLock:
    """Observe another thread attempting the shared lock during a held lookup."""

    def __init__(self, inner):
        self.inner = inner
        self.holder_ident = None
        self.contender_attempted = threading.Event()
        self.contender_acquired = threading.Event()

    def __enter__(self):
        competitor = self.holder_ident is not None and threading.get_ident() != self.holder_ident
        if competitor:
            self.contender_attempted.set()
        result = self.inner.__enter__()
        if competitor:
            self.contender_acquired.set()
        return result

    def __exit__(self, *args):
        return self.inner.__exit__(*args)


def test_delete_waits_for_inflight_prepare_and_returns_conflict(client, monkeypatch):
    script = upload(client)
    lock = ObservedLock(client.app.state.model_config_lock)
    client.app.state.model_config_lock = lock
    lookup_started, finish_lookup = threading.Event(), threading.Event()
    real_find = session_api.find_script

    def blocked_lookup(database, identifier):
        result = real_find(database, identifier)
        lock.holder_ident = threading.get_ident()
        lookup_started.set()
        assert finish_lookup.wait(5), "test lookup was not released"
        return result

    monkeypatch.setattr(session_api, "find_script", blocked_lookup)
    other_client = TestClient(client.app)
    try:
        with client.websocket_connect("/api/session") as websocket:

            def prepare():
                websocket.send_json({"type": "prepare", "script_id": script["id"]})
                return receive_state(websocket, "READY")

            with ThreadPoolExecutor(max_workers=2) as pool:
                prepared = pool.submit(prepare)
                try:
                    assert lookup_started.wait(2)
                    deleted = pool.submit(other_client.delete, f"/api/scripts/{script['id']}")
                    assert lock.contender_attempted.wait(2)
                    assert not lock.contender_acquired.wait(0.05)
                finally:
                    finish_lookup.set()
                ready = prepared.result(timeout=5)
                response = deleted.result(timeout=5)
            assert response.status_code == 409
            assert response.json()["detail"]["code"] == "SCRIPT_IN_USE"
            assert ready["script_id"] == script["id"]
            assert client.get(f"/api/scripts/{script['id']}").status_code == 200
            websocket.send_json({"type": "end"})
            receive_state(websocket, "TERMINATED")
    finally:
        finish_lookup.set()
        other_client.close()


def test_prepare_waits_for_inflight_delete_then_rejects_missing_record(client, monkeypatch):
    script = upload(client)
    lock = ObservedLock(client.app.state.model_config_lock)
    client.app.state.model_config_lock = lock
    delete_started, finish_delete = threading.Event(), threading.Event()
    real_remove = scripts_api.remove_script

    def blocked_remove(database, selected):
        lock.holder_ident = threading.get_ident()
        delete_started.set()
        assert finish_delete.wait(5), "test deletion was not released"
        real_remove(database, selected)

    monkeypatch.setattr(scripts_api, "remove_script", blocked_remove)
    other_client = TestClient(client.app)
    try:
        with client.websocket_connect("/api/session") as websocket:

            def prepare():
                websocket.send_json({"type": "prepare", "script_id": script["id"]})
                return websocket.receive_json()

            with ThreadPoolExecutor(max_workers=2) as pool:
                deleted = pool.submit(other_client.delete, f"/api/scripts/{script['id']}")
                try:
                    assert delete_started.wait(2)
                    prepared = pool.submit(prepare)
                    assert lock.contender_attempted.wait(2)
                    assert not lock.contender_acquired.wait(0.05)
                finally:
                    finish_delete.set()
                response = deleted.result(timeout=5)
                message = prepared.result(timeout=5)
            assert response.status_code == 204
            assert message["type"] == "error"
            assert not runtimes(client.app)
            assert client.get(f"/api/scripts/{script['id']}").status_code == 404
    finally:
        finish_delete.set()
        other_client.close()
