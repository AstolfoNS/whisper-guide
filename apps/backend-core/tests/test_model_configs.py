"""Model profile lifecycle, persistence, isolation and protocol-level probes."""

import json
import stat
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.api.settings as settings_api
from app.config import Settings
from app.domain.session import SessionState
from app.infrastructure.llm import LLMSemanticRefiner
from app.main import create_app


def make_app(tmp_path, **values):
    return create_app(Settings(environment="test", data_dir=tmp_path, _env_file=None, **values))


def create_profile(client, **settings):
    response = client.post(
        "/api/settings/model-configs", json={"name": "测试方案", "settings": settings}
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_profiles_migrate_old_file_and_never_expose_secrets(tmp_path):
    (tmp_path / ".model-settings.json").write_text(
        json.dumps({"llm_model": "old-model", "llm_api_key": "old-private-token"}),
        encoding="utf-8",
    )
    app = make_app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/api/settings/model-configs")
        assert response.status_code == 200
        assert "old-private-token" not in response.text
        payload = response.json()
        assert len(payload["configs"]) == 2
        assert payload["active_id"] == "existing-config"
        active = next(item for item in payload["configs"] if item["is_active"])
        assert active["settings"]["llm"]["model"] == "old-model"
        assert active["settings"]["llm"]["api_key"] == "****"
        local = next(item for item in payload["configs"] if item["is_default"])
        assert local["settings"]["llm"]["has_api_key"] is False
        assert local["settings"]["stt"]["provider"] == "local"
        assert app.state.settings.llm_api_key == "old-private-token"


def test_save_is_not_activation_and_active_drafts_survive_restart(tmp_path):
    app = make_app(tmp_path, llm_model="old-model")
    with TestClient(app) as client:
        profile = create_profile(client, llm_model="new-model", llm_api_key="private-key")
        assert profile["is_active"] is False
        assert app.state.settings.llm_model == "old-model"
        activated = client.post(f"/api/settings/model-configs/{profile['id']}/activate").json()
        assert activated["is_active"] is True
        assert app.state.settings.llm_model == "new-model"
        response = client.put(
            f"/api/settings/model-configs/{profile['id']}",
            json={"revision": 1, "settings": {"llm_model": "draft-model"}},
        )
        assert response.status_code == 200
        assert response.json()["pending_activation"] is True
        assert response.json()["active_revision"] == 1
        assert app.state.settings.llm_model == "new-model"
    restarted = make_app(tmp_path)
    with TestClient(restarted) as client:
        assert restarted.state.settings.llm_model == "new-model"
        assert restarted.state.settings.llm_api_key == "private-key"
        assert client.get(f"/api/settings/model-configs/{profile['id']}").json()["revision"] == 2
        assert stat.S_IMODE(restarted.state.settings.model_configs_path.stat().st_mode) == 0o600
        assert list(tmp_path.glob(".model-configs-*.tmp")) == []


def test_profile_secrets_keep_replace_clear_and_duplicate(tmp_path):
    app = make_app(tmp_path, llm_api_key="active-secret")
    with TestClient(app) as client:
        profile = create_profile(
            client, llm_api_key="first-secret", cloud_stt_access_token="speech-secret"
        )
        assert "active-secret" not in json.dumps(profile)
        identifier = profile["id"]
        for revision, fields in (
            (1, {"llm_api_key": "****", "cloud_stt_access_token": ""}),
            (2, {"llm_api_key": "second-secret"}),
        ):
            response = client.put(
                f"/api/settings/model-configs/{identifier}",
                json={"revision": revision, "settings": fields},
            )
            assert response.status_code == 200
        stored = app.state.model_configs.get(identifier)["settings"]
        assert stored["llm_api_key"] == "second-secret"
        assert stored["cloud_stt_access_token"] == "speech-secret"
        duplicate = client.post(f"/api/settings/model-configs/{identifier}/duplicate", json={})
        assert duplicate.status_code == 201
        assert duplicate.json()["settings"]["has_api_key"] is False
        assert duplicate.json()["settings"]["has_access_token"] is False
        cleared = client.put(
            f"/api/settings/model-configs/{identifier}",
            json={
                "revision": 3,
                "settings": {
                    "clear_llm_api_key": True,
                    "stt": {"clear_access_token": True},
                },
            },
        )
        assert cleared.status_code == 200
        assert cleared.json()["settings"]["has_api_key"] is False
        assert cleared.json()["settings"]["has_access_token"] is False
        assert (
            client.delete(f"/api/settings/model-configs/{duplicate.json()['id']}").status_code
            == 204
        )


def test_new_profile_does_not_inherit_active_credentials(tmp_path):
    with TestClient(
        make_app(tmp_path, llm_api_key="private-key", cloud_access_token="secret")
    ) as client:
        profile = create_profile(client)
        assert profile["settings"]["has_api_key"] is False
        assert profile["settings"]["has_access_token"] is False


@pytest.mark.parametrize("state", list(SessionState))
def test_activation_and_legacy_update_guard_all_unfinished_states(tmp_path, state):
    app = make_app(tmp_path)
    with TestClient(app) as client:
        profile = create_profile(client)
        app.state.sessions = {"test": SimpleNamespace(session=SimpleNamespace(state=state))}
        try:
            response = client.post(f"/api/settings/model-configs/{profile['id']}/activate")
            legacy = client.put("/api/settings/models", json={"llm_model": "updated"})
            if state == SessionState.TERMINATED:
                assert response.status_code == legacy.status_code == 200
            else:
                assert response.status_code == legacy.status_code == 409
                assert response.json()["detail"]["code"] == "SESSION_ACTIVE"
                assert app.state.settings.llm_model == ""
        finally:
            app.state.sessions = {}


def test_default_and_active_profiles_are_protected(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        for identifier in ("local-default", "existing-config"):
            assert client.delete(f"/api/settings/model-configs/{identifier}").status_code == 409
        assert (
            client.put(
                "/api/settings/model-configs/local-default", json={"revision": 1, "name": "更改"}
            ).status_code
            == 409
        )
        assert client.get("/api/settings/model-configs/missing").status_code == 404


def test_revision_conflict_and_concurrent_edits_have_one_winner(tmp_path):
    app = make_app(tmp_path)
    with TestClient(app) as client:
        profile = create_profile(client)
        endpoint = f"/api/settings/model-configs/{profile['id']}"

        def save(name):
            return client.put(endpoint, json={"revision": 1, "name": name}).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(save, ["one", "two"])) == [200, 409]
        conflict = client.post(endpoint + "/activate", json={"revision": 1})
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "REVISION_CONFLICT"
        assert client.put(endpoint, json={"name": "missing revision"}).status_code == 422


def test_test_endpoint_uses_unsaved_draft_and_does_not_persist(tmp_path, monkeypatch):
    captured = []

    async def fake_test(refiner):
        captured.append((refiner.model, refiner.api_key))
        return {"ok": True, "latency_ms": 9, "private": "secret"}

    monkeypatch.setattr(LLMSemanticRefiner, "test_connection", fake_test)
    app = make_app(tmp_path)
    with TestClient(app) as client:
        profile = create_profile(client, llm_model="saved", llm_api_key="stored-secret")
        before = app.state.settings.model_configs_path.read_bytes()
        response = client.post(
            "/api/settings/model-configs/test",
            json={
                "id": profile["id"],
                "target": "llm",
                "settings": {"llm_model": "draft"},
            },
        )
        assert response.status_code == 200
        assert response.json()["verified"] == "network"
        assert response.json()["latency_ms"] == 9
        assert "secret" not in response.text
        assert captured == [("draft", "stored-secret")]
        assert app.state.settings.model_configs_path.read_bytes() == before
        assert app.state.settings.llm_model == ""


def test_cloud_stt_probe_requires_actual_gateway_response(tmp_path, monkeypatch):
    requests = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"text": "", "is_final": True}

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            requests.append((url, kwargs))
            return Response()

    monkeypatch.setattr(settings_api.httpx, "AsyncClient", Client)
    with TestClient(make_app(tmp_path)) as client:
        response = client.post(
            "/api/settings/model-configs/test",
            json={
                "target": "stt",
                "settings": {
                    "stt_provider": "cloud",
                    "cloud_opt_in": True,
                    "cloud_stt_endpoint": "https://speech.example/stt",
                    "cloud_stt_access_token": "private-speech-token",
                },
            },
        )
        assert response.json()["ok"] is True
        assert response.json()["verified"] == "network"
        assert "private-speech-token" not in response.text
        assert requests[0][1]["headers"]["Authorization"] == "Bearer private-speech-token"
        assert "audio" in requests[0][1]["json"]
        monkeypatch.setattr(Response, "json", lambda self: {"status": "ok"})
        failed = client.post(
            "/api/settings/model-configs/test",
            json={
                "target": "stt",
                "settings": {
                    "stt_provider": "cloud",
                    "cloud_opt_in": True,
                    "cloud_stt_endpoint": "https://speech.example/stt",
                    "cloud_stt_access_token": "private-speech-token",
                },
            },
        )
        assert failed.json()["ok"] is False
        assert failed.json()["verified"] == "none"


def test_model_profile_validation_sanitizes_private_inputs(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        response = client.post(
            "/api/settings/model-configs",
            json={
                "name": "test",
                "settings": {"llm_base_url": "https://user:secret@example.test"},
            },
        )
        assert response.status_code == 422
        assert "secret" not in response.text
        assert client.post("/api/settings/model-configs", json={"name": "   "}).status_code == 422


def test_failed_store_write_leaves_active_and_saved_profile_untouched(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    with TestClient(app) as client:
        profile = create_profile(client, llm_model="saved")
        before = app.state.model_configs.active_snapshot()

        def fail_save(candidate):
            raise OSError("private-failure-message")

        monkeypatch.setattr(app.state.model_configs, "_save", fail_save)
        response = client.post(f"/api/settings/model-configs/{profile['id']}/activate")
        assert response.status_code == 503
        assert "private-failure-message" not in response.text
        assert app.state.model_configs.active_snapshot() == before
        assert app.state.settings.llm_model == ""
        update = client.put(
            f"/api/settings/model-configs/{profile['id']}",
            json={"revision": 1, "settings": {"llm_model": "unsaved"}},
        )
        assert update.status_code == 503
        assert app.state.model_configs.get(profile["id"])["settings"]["llm_model"] == "saved"


def test_corrupt_store_is_not_overwritten_or_activated(tmp_path):
    path = tmp_path / ".model-configs.json"
    path.write_text("{broken", encoding="utf-8")
    with TestClient(make_app(tmp_path)) as client:
        assert client.get("/api/settings/model-configs").json()["store_available"] is False
        assert client.post("/api/settings/model-configs", json={"name": "new"}).status_code == 503
        assert path.read_text(encoding="utf-8") == "{broken"


def test_legacy_update_synchronizes_active_profile(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        assert client.put("/api/settings/models", json={"llm_model": "compat"}).status_code == 200
        listing = client.get("/api/settings/model-configs").json()
        active = next(item for item in listing["configs"] if item["is_active"])
        assert active["settings"]["llm_model"] == "compat"
        assert active["pending_activation"] is False


def test_legacy_urls_do_not_leak_embedded_credentials(tmp_path):
    with TestClient(
        make_app(
            tmp_path,
            llm_base_url="https://user:embedded-secret@example.test/v1",
            cloud_stt_endpoint="https://example.test/stt?token=query-secret",
        )
    ) as client:
        for endpoint in ("/api/settings/models", "/api/settings/model-configs"):
            response = client.get(endpoint)
            assert response.status_code == 200
            assert "embedded-secret" not in response.text
            assert "query-secret" not in response.text


def test_incomplete_profile_store_is_rejected_without_overwrite(tmp_path):
    app = make_app(tmp_path)
    with TestClient(app) as client:
        create_profile(client)
    path = tmp_path / ".model-configs.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["configs"][0]["description"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with TestClient(make_app(tmp_path)) as client:
        assert client.get("/api/settings/model-configs").json()["store_available"] is False
        assert client.post("/api/settings/model-configs", json={"name": "new"}).status_code == 503


def test_profile_store_limit_preserves_previous_valid_data(tmp_path):
    app = make_app(tmp_path)
    with TestClient(app) as client:
        create_profile(client)
        previous = app.state.settings.model_configs_path.read_bytes()
        response = client.post(
            "/api/settings/model-configs",
            json={
                "name": "huge",
                "settings": {"llm_api_key": "x" * (512 * 1024)},
            },
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "PROFILE_STORE_LIMIT"
        assert app.state.settings.model_configs_path.read_bytes() == previous


@pytest.mark.parametrize("had_legacy", [False, True])
def test_legacy_facade_restores_old_file_if_profile_save_fails(tmp_path, monkeypatch, had_legacy):
    path = tmp_path / ".model-settings.json"
    previous = json.dumps({"llm_model": "previous"}).encode("utf-8")
    if had_legacy:
        path.write_bytes(previous)
    app = make_app(tmp_path)
    with TestClient(app) as client:

        def fail_save(candidate):
            raise OSError("disk-full")

        monkeypatch.setattr(app.state.model_configs, "_save", fail_save)
        response = client.put("/api/settings/models", json={"llm_model": "failed-save"})
        assert response.status_code == 503
        if had_legacy:
            assert path.read_bytes() == previous
        else:
            assert not path.exists()
    restarted = make_app(tmp_path)
    assert restarted.state.settings.llm_model != "failed-save"


def test_list_active_snapshot_uses_activated_settings_instead_of_draft(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        profile = create_profile(client, llm_model="active", llm_api_key="secret")
        client.post(f"/api/settings/model-configs/{profile['id']}/activate")
        client.put(
            f"/api/settings/model-configs/{profile['id']}",
            json={
                "revision": 1,
                "settings": {"llm_model": "draft"},
            },
        )
        response = client.get("/api/settings/model-configs")
        assert response.json()["active_profile"]["settings"]["llm_model"] == "active"
        assert "secret" not in response.text
