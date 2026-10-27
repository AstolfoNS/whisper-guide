"""Environment defaults, compatibility aliases, and active-profile precedence."""

import json
from types import SimpleNamespace

import pytest

from app.config import Settings, clone_model_settings, load_model_settings
from app.infrastructure.speech import ProviderSpeechServices
from app.main import create_app
from app.repositories.model_configs import ModelConfigRepository


def settings(tmp_path, **values):
    return Settings(environment="test", data_dir=tmp_path, _env_file=None, **values)


def test_legacy_persisted_cloud_fields_override_environment_defaults(tmp_path):
    base = settings(
        tmp_path,
        stt_provider="cloud",
        cloud_opt_in=True,
        cloud_stt_endpoint="https://environment.example/asr",
        cloud_stt_app_id="environment-app",
        cloud_stt_access_token="synthetic-env-token",
    )
    (tmp_path / ".model-settings.json").write_text(
        json.dumps(
            {
                "cloud_endpoint": "https://saved.example/asr",
                "cloud_app_id": "saved-app",
                "cloud_access_token": "synthetic-saved-token",
            }
        )
    )
    loaded = load_model_settings(base)
    assert loaded.cloud_stt_endpoint == loaded.cloud_endpoint == "https://saved.example/asr"
    assert loaded.cloud_stt_app_id == loaded.cloud_app_id == "saved-app"
    assert loaded.cloud_stt_access_token == loaded.cloud_access_token == "synthetic-saved-token"
    assert loaded.stt_provider == "cloud"
    assert base.cloud_stt_endpoint == "https://environment.example/asr"


@pytest.mark.parametrize("suffix", ["endpoint", "app_id", "access_token"])
@pytest.mark.parametrize("value", ["", "canonical-value"])
def test_canonical_cloud_update_including_clear_wins_legacy_alias(tmp_path, suffix, value):
    canonical, legacy = f"cloud_stt_{suffix}", f"cloud_{suffix}"
    result = clone_model_settings(settings(tmp_path), {canonical: value, legacy: "legacy-value"})
    assert getattr(result, canonical) == getattr(result, legacy) == value


def test_saved_profile_cloud_aliases_migrate_without_rewriting_store(tmp_path):
    base = settings(tmp_path, cloud_stt_endpoint="https://environment.example/asr")
    repository = ModelConfigRepository(base)
    repository.duplicate("existing-config", "Persist initial store")
    raw = json.loads(repository.path.read_text())
    for item in [*raw["configs"], raw["active"]]:
        item["settings"].pop("cloud_stt_endpoint")
        item["settings"]["cloud_endpoint"] = "https://saved.example/asr"
    serialized = json.dumps(raw).encode()
    repository.path.write_bytes(serialized)
    reopened = ModelConfigRepository(base)
    assert not reopened.load_failed
    assert (
        reopened.active_snapshot()["settings"]["cloud_stt_endpoint"] == "https://saved.example/asr"
    )
    assert reopened.path.read_bytes() == serialized


def test_active_model_snapshot_overrides_model_env_but_preserves_nonmodel_defaults(tmp_path):
    base = settings(
        tmp_path,
        llm_model="first-env-model",
        default_silence_timeout_ms=1500,
        tts_voice="first-voice",
        app_title="First title",
    )
    repository = ModelConfigRepository(base)
    profile = repository.create(
        "Saved profile", "", clone_model_settings(base, {"llm_model": "saved-model"})
    )
    repository.activate(profile["id"])
    before = repository.path.read_bytes()
    restarted = create_app(
        settings(
            tmp_path,
            llm_model="changed-env-model",
            default_silence_timeout_ms=1700,
            tts_voice="changed-voice",
            app_title="Changed title",
        )
    )
    active = restarted.state.settings
    assert active.llm_model == "saved-model"
    assert active.default_silence_timeout_ms == 1700
    assert active.tts_voice == "changed-voice"
    assert active.app_title == "Changed title"
    assert repository.path.read_bytes() == before


def registry_settings(**values):
    return SimpleNamespace(
        speech_model_dir=None,
        stt_provider="hybrid",
        cloud_opt_in=True,
        stt_provider_configs=[
            {"id": "incomplete", "endpoint": "https://first.example/asr"},
            {
                "id": "usable",
                "endpoint": "https://second.example/asr",
                "access_token": "synthetic-token",
            },
        ],
        **values,
    )


def test_hybrid_uses_first_configured_registry_entry_without_network():
    services = ProviderSpeechServices(registry_settings())
    assert services.registry.first_remote().config.id == "usable"
    assert services.registry.provider("cloud").config.id == "usable"
    assert services._effective_provider() == "usable"
    # Explicit selection stays explicit: an unavailable requested entry is not
    # silently replaced with a different registered endpoint.
    assert services._effective_provider("incomplete") == "local"


def test_hybrid_keeps_global_privacy_guard_for_registered_providers():
    source = registry_settings()
    source.cloud_opt_in = False
    services = ProviderSpeechServices(source)
    assert services._effective_provider() == "local"
    assert not any(provider.configured for provider in services.registry.remote.values())


def test_provider_registry_environment_catalog_is_not_profile_persisted(tmp_path):
    catalog = registry_settings().stt_provider_configs
    base = settings(
        tmp_path, stt_provider_configs=catalog, stt_provider="usable", cloud_opt_in=True
    )
    repository = ModelConfigRepository(base)
    repository.duplicate("existing-config")
    persisted = json.loads(repository.path.read_text())
    assert "stt_provider_configs" not in persisted["active"]["settings"]
    changed = settings(tmp_path, stt_provider_configs=[catalog[1]])
    reopened = create_app(changed)
    assert reopened.state.settings.stt_provider == "usable"
    assert reopened.state.settings.cloud_opt_in
    assert reopened.state.settings.stt_provider_configs == [catalog[1]]


def test_preset_id_alone_does_not_replace_explicit_llm_connection_settings(tmp_path):
    from app.infrastructure.llm import LLMSemanticRefiner

    base = settings(tmp_path, llm_provider_preset="qwen", llm_align_mode="fallback")
    assert not LLMSemanticRefiner(base).enabled
    configured = clone_model_settings(
        base,
        {
            "llm_base_url": "https://explicit.example/v1",
            "llm_model": "explicit-model",
        },
    )
    refiner = LLMSemanticRefiner(configured)
    assert refiner.enabled
    assert refiner.base_url == "https://explicit.example/v1"
    assert refiner.model == "explicit-model"


@pytest.mark.parametrize("value", ["0", "1001"])
def test_upload_count_environment_rejects_values_outside_parser_capacity(monkeypatch, value):
    from pydantic import ValidationError

    monkeypatch.setenv("WHISPER_GUIDE_SCRIPT_MAX_UPLOAD_FILES", value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_upload_count_environment_accepts_parser_capacity(monkeypatch):
    monkeypatch.setenv("WHISPER_GUIDE_SCRIPT_MAX_UPLOAD_FILES", "1000")
    assert Settings(_env_file=None).script_max_upload_files == 1000


def test_api_honors_increased_upload_size_and_count_environment(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("WHISPER_GUIDE_SCRIPT_MAX_UPLOAD_FILES", "15")
    monkeypatch.setenv("WHISPER_GUIDE_SCRIPT_MAX_FILE_BYTES", "4194304")
    application = create_app(Settings(data_dir=tmp_path, _env_file=None))
    with TestClient(application) as client:
        files = [("files", (f"item-{index}.txt", b"text", "text/plain")) for index in range(11)]
        files[0] = ("files", ("large.txt", b"a" * (3 * 1024 * 1024), "text/plain"))
        response = client.post("/api/scripts/upload", files=files)
        assert response.status_code == 201
        assert len(response.json()) == 11
        assert response.json()[0]["character_count"] == 3 * 1024 * 1024
