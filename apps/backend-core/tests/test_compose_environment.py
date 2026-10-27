"""Compose interpolation without private environment access or container startup."""

import gzip
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "release_environment_tools", ROOT / "scripts/release.py"
)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
DOLLAR = chr(36)


@pytest.fixture
def isolated_compose(tmp_path, monkeypatch):
    if (
        not shutil.which("docker")
        or subprocess.run(
            ["docker", "compose", "version"], capture_output=True, check=False
        ).returncode
    ):
        pytest.skip("Compose CLI required; no Docker daemon is used.")
    for name in ("docker-compose.yml", ".env.example"):
        shutil.copy2(ROOT / name, tmp_path / name)
    monkeypatch.setattr(release, "ROOT", tmp_path)
    return tmp_path


def resolve(root, compose_file="docker-compose.yml", env_file=".env", overrides=None):
    environment = release.clean_environment()
    environment.update(overrides or {})
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--project-directory",
            str(root),
            "--env-file",
            str(root / env_file),
            "-f",
            str(root / compose_file),
            "config",
            "--format",
            "json",
            "--no-path-resolution",
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)["services"]


def test_every_example_key_is_forwarded_or_explicitly_host_only():
    keys = set(re.findall(r"^(WHISPER_GUIDE_[A-Z_]+)=", (ROOT / ".env.example").read_text(), re.M))
    services = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]
    backend = services["backend"]["environment"]
    host_only = {"WHISPER_GUIDE_VERSION", "WHISPER_GUIDE_REVISION", "WHISPER_GUIDE_HTTP_PORT"}
    assert keys - host_only == set(backend)
    for name in keys - host_only:
        assert DOLLAR + "{" + name in backend[name]
    assert "WHISPER_GUIDE_VERSION" in services["backend"]["image"]
    assert "WHISPER_GUIDE_REVISION" in services["backend"]["build"]["args"]["VCS_REF"]
    assert "WHISPER_GUIDE_HTTP_PORT" in services["frontend"]["ports"][0]


def test_local_example_covers_supported_backend_settings_without_unknown_fields():
    from app.config import Settings

    keys = set(
        re.findall(
            r"^(WHISPER_GUIDE_[A-Z_]+)=",
            (ROOT / "apps/backend-core/.env.example").read_text(),
            re.M,
        )
    )
    expected = {
        "WHISPER_GUIDE_" + name.upper() for name in Settings.model_fields if name != "environment"
    }
    assert keys == expected


def test_runtime_values_reach_backend_without_moving_host_data(isolated_compose):
    root = isolated_compose
    (root / ".env").write_text(
        "WHISPER_GUIDE_DEFAULT_SILENCE_TIMEOUT_MS=1500\nWHISPER_GUIDE_APP_TITLE=环境标题\n"
        "WHISPER_GUIDE_HTTP_PORT=18080\nWHISPER_GUIDE_DATA_DIR=/runtime/data\n"
        "WHISPER_GUIDE_BACKUP_DIR=/runtime/backups\n"
        "WHISPER_GUIDE_SPEECH_MODEL_DIR=/runtime/models/model\n"
        "WHISPER_GUIDE_CLOUD_ENDPOINT=https://legacy.invalid/stt\n"
        "WHISPER_GUIDE_CLOUD_STT_ENDPOINT=https://canonical.invalid/stt\n"
    )
    services = resolve(root)
    backend = services["backend"]
    environment = backend["environment"]
    assert environment["WHISPER_GUIDE_DEFAULT_SILENCE_TIMEOUT_MS"] == "1500"
    assert environment["WHISPER_GUIDE_APP_TITLE"] == "环境标题"
    assert environment["WHISPER_GUIDE_DATA_DIR"] == "/runtime/data"
    assert environment["WHISPER_GUIDE_BACKUP_DIR"] == "/runtime/backups"
    assert environment["WHISPER_GUIDE_SPEECH_MODEL_DIR"] == "/runtime/models/model"
    assert environment["WHISPER_GUIDE_CLOUD_ENDPOINT"] == "https://legacy.invalid/stt"
    assert environment["WHISPER_GUIDE_CLOUD_STT_ENDPOINT"] == "https://canonical.invalid/stt"
    assert {item["target"]: item["source"] for item in backend["volumes"]} == {
        "/runtime/data": "./data/sqlite",
        "/runtime/backups": "./data/backups",
    }
    assert services["frontend"]["ports"][0]["published"] == "18080"
    assert not (root / "data").exists()


def test_shell_overrides_env_and_empty_values_use_defaults(isolated_compose):
    root = isolated_compose
    (root / ".env").write_text("WHISPER_GUIDE_DEFAULT_SILENCE_TIMEOUT_MS=1500\n")
    services = resolve(root, overrides={"WHISPER_GUIDE_DEFAULT_SILENCE_TIMEOUT_MS": "2000"})
    assert services["backend"]["environment"]["WHISPER_GUIDE_DEFAULT_SILENCE_TIMEOUT_MS"] == "2000"
    services = resolve(
        root,
        overrides={"WHISPER_GUIDE_DEFAULT_SILENCE_TIMEOUT_MS": "", "WHISPER_GUIDE_APP_TITLE": ""},
    )
    assert services["backend"]["environment"]["WHISPER_GUIDE_DEFAULT_SILENCE_TIMEOUT_MS"] == "3000"
    assert services["backend"]["environment"]["WHISPER_GUIDE_APP_TITLE"] == "whisper-guide"


def test_release_pins_images_but_preserves_runtime_interpolation(isolated_compose, monkeypatch):
    root = isolated_compose
    (root / ".env").write_text(
        "WHISPER_GUIDE_VERSION=old-file-version\nWHISPER_GUIDE_DEFAULT_SILENCE_TIMEOUT_MS=1500\n"
        "WHISPER_GUIDE_LLM_API_KEY=private-fixture-value\n"
    )
    monkeypatch.setenv("WHISPER_GUIDE_DEFAULT_SILENCE_TIMEOUT_MS", "9876")
    monkeypatch.setenv("WHISPER_GUIDE_LLM_API_KEY", "shell-fixture-value")
    output = root / "release-compose.json"
    release.render_compose("0.1.2", output)
    text = output.read_text()
    assert (
        "private-fixture-value" not in text
        and "shell-fixture-value" not in text
        and "9876" not in text
    )
    raw = json.loads(text)["services"]
    assert (
        raw["backend"]["environment"]["WHISPER_GUIDE_DEFAULT_SILENCE_TIMEOUT_MS"]
        == DOLLAR + "{WHISPER_GUIDE_DEFAULT_SILENCE_TIMEOUT_MS:-3000}"
    )
    services = resolve(
        root, "release-compose.json", overrides={"WHISPER_GUIDE_VERSION": "wrong-shell-version"}
    )
    assert services["backend"]["environment"]["WHISPER_GUIDE_DEFAULT_SILENCE_TIMEOUT_MS"] == "1500"
    for name, service in services.items():
        assert service["image"] == f"whisper-guide-{name}:0.1.2"
        assert service["platform"] == "linux/amd64"
        assert "build" not in service


def test_defaults_keep_original_persistent_mounts(isolated_compose):
    backend = resolve(isolated_compose, env_file=".env.example")["backend"]
    assert backend["environment"]["WHISPER_GUIDE_DEFAULT_SILENCE_TIMEOUT_MS"] == "3000"
    assert {item["target"]: item["source"] for item in backend["volumes"]} == {
        "/data": "./data/sqlite",
        "/backups": "./data/backups",
    }


@pytest.mark.parametrize("existing", [True, False], ids=["preserve-env", "create-env"])
def test_deploy_uses_bundle_environment_and_recreates_without_sourcing_private_values(
    tmp_path, existing
):
    bundle = tmp_path / "bundle"
    (bundle / "scripts").mkdir(parents=True)
    for name in release.BUNDLE_FILES:
        (bundle / name).write_bytes(b"public fixture")
    shutil.copy2(ROOT / "deploy/deploy.sh", bundle / "deploy.sh")
    (bundle / "VERSION").write_text("0.1.2\n")
    (bundle / "whisper-guide-image.tar.gz").write_bytes(gzip.compress(b"fixture-image"))
    (bundle / ".env.example").write_text("WHISPER_GUIDE_DEFAULT_SILENCE_TIMEOUT_MS=1500\n")
    sentinel = tmp_path / "must-not-execute"
    private = "PRIVATE=do-not-print\nDO_NOT_SOURCE=" + DOLLAR + "(touch " + str(sentinel) + ")\n"
    if existing:
        (bundle / ".env").write_text(private)
    (bundle / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256((bundle / name).read_bytes()).hexdigest()}  {name}\n"
            for name in release.BUNDLE_FILES
        )
    )
    (bundle / "data/sqlite").mkdir(parents=True)
    database = bundle / "data/sqlite/keep.db"
    database.write_bytes(b"existing-data")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    docker = binaries / "docker"
    docker.write_text(
        "#!"
        + sys.executable
        + "\n"
        + """
import json,os,sys
args=sys.argv[1:]
with open(os.environ['CALL_LOG'],'a') as log: log.write(json.dumps(args)+chr(10))
if args[0]=='info': print('{}')
elif args[0]=='image': print('[]')
elif args[0] in ('load','run'): sys.stdin.buffer.read()
elif args[0]=='compose': pass
else: raise SystemExit(91)
"""
    )
    docker.chmod(0o755)
    for name, content in (("id", "#!/bin/sh\necho 0\n"), ("chown", "#!/bin/sh\nexit 0\n")):
        target = binaries / name
        target.write_text(content)
        target.chmod(0o755)
    caller = tmp_path / "caller"
    caller.mkdir()
    (caller / ".env").write_text("WHISPER_GUIDE_DEFAULT_SILENCE_TIMEOUT_MS=9999\n")
    log = tmp_path / "docker-calls"
    result = subprocess.run(
        ["bash", str(bundle / "deploy.sh")],
        cwd=caller,
        env={**os.environ, "PATH": str(binaries) + ":" + os.environ["PATH"], "CALL_LOG": str(log)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "do-not-print" not in result.stdout + result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    compose = [args for args in calls if args[0] == "compose"]
    assert len(compose) == 3
    for args in compose:
        assert args[args.index("--project-directory") + 1] == str(bundle)
        assert args[args.index("-f") + 1] == str(bundle / "docker-compose.yml")
    expected_config = ".env" if existing else ".env.example"
    assert compose[0][compose[0].index("--env-file") + 1] == str(bundle / expected_config)
    for args in compose[1:]:
        assert args[args.index("--env-file") + 1] == str(bundle / ".env")
    up = compose[1]
    for flag in ("--force-recreate", "--no-build", "--wait"):
        assert flag in up
    assert up[up.index("--pull") + 1] == "never"
    assert not sentinel.exists()
    assert database.read_bytes() == b"existing-data"
    assert (bundle / ".env").read_text() == (
        private if existing else (bundle / ".env.example").read_text()
    )
    assert (caller / ".env").read_text().endswith("9999\n")


def test_inert_environment_marker_is_not_advertised_or_forwarded():
    for name in (".env.example", "apps/backend-core/.env.example", "docker-compose.yml"):
        assert "WHISPER_GUIDE_ENVIRONMENT" not in (ROOT / name).read_text()


def test_proxy_does_not_override_configurable_upload_limits():
    source = (ROOT / "deploy/nginx/default.conf").read_text()
    source = re.sub(r"#[^\n]*", "", source)
    limits = re.findall(r"\bclient_max_body_size\s+([^;]+);", source)
    assert limits == ["0"]
