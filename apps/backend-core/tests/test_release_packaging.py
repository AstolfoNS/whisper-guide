"""Release provenance and offline verification without publishing or deploying."""

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release = load("release_tools", ROOT / "scripts/release.py")
verifier = load("release_verifier", ROOT / "deploy/verify_release.py")


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


@pytest.fixture
def repository(tmp_path, monkeypatch):
    for name in ("apps/frontend", "apps/backend-core", "deploy", "scripts"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    (tmp_path / "apps/frontend/package.json").write_text('{"version":"0.1.2"}')
    (tmp_path / "apps/backend-core/pyproject.toml").write_text(
        '[project]\nname = "backend-core"\nversion = "0.1.2"\n'
    )
    (tmp_path / "apps/backend-core/uv.lock").write_text(
        '[[package]]\nname = "backend-core"\nversion = "0.1.2"\n'
    )
    (tmp_path / ".gitignore").write_text("release/\n.env\n")
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Release test")
    git(tmp_path, "config", "user.email", "release@example.invalid")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "fixture")
    git(tmp_path, "tag", "-a", "v0.1.2", "-m", "fixture")
    monkeypatch.setattr(release, "ROOT", tmp_path)
    return tmp_path


def image(service, revision="a" * 40):
    return {
        "Id": "sha256:" + ("b" if service == "backend" else "c") * 64,
        "RepoTags": [f"whisper-guide-{service}:0.1.2"],
        "Os": "linux",
        "Architecture": "amd64",
        "Config": {
            "Labels": {
                "org.opencontainers.image.version": "0.1.2",
                "org.opencontainers.image.revision": revision,
            }
        },
    }


def verified_bundle(tmp_path):
    images = [image(name) for name in ("backend", "frontend")]
    manifest = {
        "schema_version": 1,
        "version": "0.1.2",
        "tag": "v0.1.2",
        "revision": "a" * 40,
        "platform": "linux/amd64",
        "images": {},
    }
    services = {}
    for service, actual in zip(("backend", "frontend"), images, strict=True):
        reference = f"whisper-guide-{service}:0.1.2"
        manifest["images"][service] = {
            "reference": reference,
            "id": actual["Id"],
            "platform": "linux/amd64",
            "labels": actual["Config"]["Labels"],
        }
        services[service] = {"image": reference, "platform": "linux/amd64"}
    (tmp_path / "VERSION").write_text("0.1.2\n")
    (tmp_path / "release.json").write_text(json.dumps(manifest))
    (tmp_path / "docker-compose.yml").write_text(json.dumps({"services": services}))
    return {"engine": {"OSType": "linux", "Architecture": "x86_64"}, "images": images}


def test_exact_tag_and_clean_sources_match_application_manifests(repository):
    meta = release.metadata(None)
    assert meta["tag"] == "v0.1.2"
    assert meta["version"] == "0.1.2"
    assert meta["revision"] == git(repository, "rev-parse", "HEAD")
    (repository / ".env").write_text("PRIVATE_DO_NOT_PACKAGE=secret")
    assert release.metadata("v0.1.2") == meta


@pytest.mark.parametrize(
    "value",
    ["../0.1.2", "v0.1.2", "01.2.3", "1.2", "1.2.3-rc.1", "1.2.3+meta", "1١.2.3", "1.2.3\n"],
)
def test_version_rejects_unsafe_or_unsupported_formats(value):
    with pytest.raises(ValueError):
        release.version_value(value)


def test_dirty_or_untracked_worktree_cannot_be_released(repository):
    (repository / "untracked").write_text("new source")
    with pytest.raises(ValueError, match="clean worktree"):
        release.metadata("v0.1.2")


def test_wrong_tag_revision_is_rejected(repository):
    (repository / "new").write_text("new source")
    git(repository, "add", ".")
    git(repository, "commit", "-qm", "next")
    with pytest.raises(ValueError, match="point to HEAD"):
        release.metadata("v0.1.2")


def test_manifest_mismatch_is_rejected(repository):
    (repository / "apps/frontend/package.json").write_text('{"version":"0.1.3"}')
    git(repository, "add", ".")
    git(repository, "commit", "-qm", "mismatch")
    git(repository, "tag", "v0.1.3")
    with pytest.raises(ValueError, match="versions differ"):
        release.metadata("v0.1.3")


def test_prepare_rolls_back_version_files_on_failed_uv(repository, monkeypatch):
    paths = [
        repository / name
        for name in (
            "apps/frontend/package.json",
            "apps/backend-core/pyproject.toml",
            "apps/backend-core/uv.lock",
        )
    ]
    originals = [path.read_bytes() for path in paths]
    monkeypatch.setattr(release, "run", lambda *a, **k: "")

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "uv")

    monkeypatch.setattr(release.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        release.prepare("0.1.3")
    assert [path.read_bytes() for path in paths] == originals


def test_prepare_updates_manifests_and_lock(repository, monkeypatch):
    monkeypatch.setattr(release, "run", lambda *a, **k: "")

    def lock(*args, **kwargs):
        path = repository / "apps/backend-core/uv.lock"
        path.write_text(path.read_text().replace("0.1.2", "0.1.3"))

    monkeypatch.setattr(release.subprocess, "run", lock)
    release.prepare("0.1.3")
    release.check_versions("0.1.3")


def test_render_fixes_versions_without_baking_private_environment(repository, monkeypatch):
    source = {
        "services": {
            name: {"build": {"context": "."}, "environment": {"TOKEN": "${TOKEN:-}"}}
            for name in ("backend", "frontend")
        }
    }
    monkeypatch.setenv("WHISPER_GUIDE_LLM_API_KEY", "never-export-this")

    def compose(*args, **kwargs):
        assert "--no-interpolate" in args and "--no-path-resolution" in args
        assert "WHISPER_GUIDE_LLM_API_KEY" not in kwargs["env"]
        return json.dumps(source)

    monkeypatch.setattr(release, "run", compose)
    output = repository / "rendered.json"
    release.render_compose("0.1.2", output)
    rendered = json.loads(output.read_text())
    assert "never-export-this" not in output.read_text()
    for service, config in rendered["services"].items():
        assert config["image"] == f"whisper-guide-{service}:0.1.2"
        assert config["platform"] == "linux/amd64"
        assert "build" not in config
        assert config["environment"]["TOKEN"] == "${TOKEN:-}"


def test_manifest_requires_matching_image_ids_platform_and_labels(repository, monkeypatch):
    meta = release.metadata("v0.1.2")
    images = [image(name, revision=meta["revision"]) for name in ("backend", "frontend")]
    monkeypatch.setattr(release, "run", lambda *a, **k: json.dumps(images))
    output = repository / "manifest.json"
    release.manifest(meta, output)
    assert json.loads(output.read_text())["images"]["backend"]["id"] == images[0]["Id"]
    images[0]["Config"]["Labels"]["org.opencontainers.image.revision"] = "d" * 40
    with pytest.raises(ValueError, match="provenance"):
        release.manifest(meta, output)


def test_valid_offline_manifest_matches_loaded_images(tmp_path):
    verifier.verify(tmp_path, verified_bundle(tmp_path))


@pytest.mark.parametrize(
    "mutation", ["id", "version", "revision", "platform", "reference", "build", "host"]
)
def test_offline_mismatches_fail_before_deployment(tmp_path, mutation):
    evidence = verified_bundle(tmp_path)
    if mutation == "id":
        evidence["images"][0]["Id"] = "sha256:" + "f" * 64
    elif mutation in {"version", "revision"}:
        evidence["images"][0]["Config"]["Labels"]["org.opencontainers.image." + mutation] = "wrong"
    elif mutation == "platform":
        evidence["images"][0]["Architecture"] = "arm64"
    elif mutation == "host":
        evidence["engine"]["Architecture"] = "aarch64"
    else:
        compose = json.loads((tmp_path / "docker-compose.yml").read_text())
        compose["services"]["backend"]["build" if mutation == "build" else "image"] = "wrong"
        (tmp_path / "docker-compose.yml").write_text(json.dumps(compose))
    with pytest.raises(ValueError):
        verifier.verify(tmp_path, evidence)


def test_release_workflow_orders_checks_packaging_and_complete_draft_upload():
    flow = yaml.load((ROOT / ".github/workflows/release.yml").read_text(), Loader=yaml.BaseLoader)
    assert flow["jobs"]["checks"]["uses"] == "./.github/workflows/ci.yml"
    assert "checks" in flow["jobs"]["package"]["needs"]
    assert flow["jobs"]["publish"]["needs"] == ["validate", "package"]
    script = flow["jobs"]["publish"]["steps"][-1]["run"]
    assert (
        script.index("--draft --title")
        < script.index("gh release upload")
        < script.index("--draft=false")
    )
    assert "--clobber" not in script


def test_package_exports_only_allowlisted_files_and_never_overwrites(repository, monkeypatch):
    import gzip
    import hashlib
    import shutil
    import tarfile
    from types import SimpleNamespace

    for source in (
        ".env.example",
        "DEPLOY.md",
        "deploy/deploy.sh",
        "deploy/verify_release.py",
        "scripts/backup.py",
    ):
        shutil.copy2(ROOT / source, repository / source)
    (repository / "docker-compose.yml").write_text("source template")
    git(repository, "add", ".")
    git(repository, "commit", "-qm", "bundle sources")
    git(repository, "tag", "v0.1.3")
    for path in (
        "apps/frontend/package.json",
        "apps/backend-core/pyproject.toml",
        "apps/backend-core/uv.lock",
    ):
        target = repository / path
        target.write_text(target.read_text().replace("0.1.2", "0.1.3"))
    git(repository, "add", ".")
    git(repository, "commit", "-qm", "version")
    git(repository, "tag", "v0.1.4")
    # Use a correctly versioned tag at the same commit in this fixture.
    for path in (
        "apps/frontend/package.json",
        "apps/backend-core/pyproject.toml",
        "apps/backend-core/uv.lock",
    ):
        target = repository / path
        target.write_text(target.read_text().replace("0.1.3", "0.1.5"))
    git(repository, "add", ".")
    git(repository, "commit", "-qm", "release")
    git(repository, "tag", "v0.1.5")
    (repository / ".env").write_text("SECRET=private")
    original_run = release.run

    def inspect_or_git(*args, **kwargs):
        if args[0] == "git":
            return original_run(*args, **kwargs)
        return "linux/amd64"

    monkeypatch.setattr(release, "run", inspect_or_git)
    builds = []
    real_run = subprocess.run

    def docker_run(args, **kwargs):
        if args[0] == "git":
            return real_run(args, **kwargs)
        if args[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(returncode=1)
        assert args[:2] == ["docker", "compose"]
        assert kwargs["env"]["WHISPER_GUIDE_VERSION"] == "0.1.5"
        builds.append(args)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(release.subprocess, "run", docker_run)
    monkeypatch.setattr(
        release, "render_compose", lambda v, p: p.write_text(json.dumps({"version": v}))
    )
    monkeypatch.setattr(release, "manifest", lambda m, p: p.write_text(json.dumps(m)))
    real_popen = subprocess.Popen

    def docker_save(args, **kwargs):
        if args[:2] == ["docker", "save"]:
            return real_popen(["printf", "verified-image-stream"], **kwargs)
        return real_popen(args, **kwargs)

    monkeypatch.setattr(release.subprocess, "Popen", docker_save)
    release.package("v0.1.5", "linux/amd64")
    directory = repository / "release/0.1.5/linux-amd64"
    archive = directory / "whisper-guide-release-0.1.5-linux-amd64.tar"
    with tarfile.open(archive) as tar:
        assert set(tar.getnames()) == set(release.BUNDLE_FILES) | {"SHA256SUMS"}
        assert tar.extractfile("VERSION").read() == b"0.1.5\n"
        assert (
            gzip.decompress(tar.extractfile("whisper-guide-image.tar.gz").read())
            == b"verified-image-stream"
        )
        sums = tar.extractfile("SHA256SUMS").read().decode().splitlines()
        for line in sums:
            checksum, name = line.split("  ")
            assert hashlib.sha256(tar.extractfile(name).read()).hexdigest() == checksum
    assert (directory / (archive.name + ".sha256")).read_text().startswith(release.sha256(archive))
    assert len(builds) == 1
    with pytest.raises(ValueError, match="Output already exists"):
        release.package("v0.1.5", "linux/amd64")
    assert len(builds) == 1


def test_deploy_rejects_missing_or_traversing_checksums_before_any_docker_call(tmp_path):
    import hashlib
    import os
    import shutil

    root = tmp_path / "bundle"
    (root / "scripts").mkdir(parents=True)
    for name in release.BUNDLE_FILES:
        path = root / name
        path.write_bytes(b"placeholder")
    shutil.copy2(ROOT / "deploy/deploy.sh", root / "deploy.sh")
    sums = "".join(
        f"{hashlib.sha256((root / name).read_bytes()).hexdigest()}  {name}\n"
        for name in release.BUNDLE_FILES
    )
    binary = tmp_path / "bin"
    binary.mkdir()
    docker = binary / "docker"
    docker.write_text("#!/bin/sh\necho forbidden-docker-call\nexit 99\n")
    docker.chmod(0o755)
    env = {**os.environ, "PATH": str(binary) + ":" + os.environ["PATH"]}
    for invalid in (sums.replace("VERSION\n", "../VERSION\n"), sums.splitlines()[0] + "\n"):
        (root / "SHA256SUMS").write_text(invalid)
        result = subprocess.run(
            ["bash", str(root / "deploy.sh")], env=env, capture_output=True, text=True, check=False
        )
        assert result.returncode != 0
        assert "forbidden-docker-call" not in result.stdout


def test_checksum_validated_bundle_does_not_initialize_data_if_image_verification_fails(tmp_path):
    import gzip
    import hashlib
    import os
    import shutil

    root = tmp_path / "bundle"
    (root / "scripts").mkdir(parents=True)
    for name in release.BUNDLE_FILES:
        (root / name).write_bytes(b"placeholder")
    (root / "VERSION").write_text("0.1.2\n")
    (root / "whisper-guide-image.tar.gz").write_bytes(gzip.compress(b"image"))
    shutil.copy2(ROOT / "deploy/deploy.sh", root / "deploy.sh")
    (root / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256((root / name).read_bytes()).hexdigest()}  {name}\n"
            for name in release.BUNDLE_FILES
        )
    )
    binary = tmp_path / "bin"
    binary.mkdir()
    docker = binary / "docker"
    docker.write_text(
        '#!/bin/sh\ncase "$1" in\ninfo) echo "{}";;\nload) cat >/dev/null;;\nimage) echo "[]";;\nrun) cat >/dev/null; exit 42;;\n*) echo forbidden-compose-call; exit 99;;\nesac\n'  # noqa: E501
    )
    docker.chmod(0o755)
    env = {**os.environ, "PATH": str(binary) + ":" + os.environ["PATH"]}
    result = subprocess.run(
        ["bash", str(root / "deploy.sh")], env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode != 0
    assert "forbidden-compose-call" not in result.stdout
    assert not (root / ".env").exists()
    assert not (root / "data").exists()


def test_successful_deploy_preserves_existing_private_configuration_and_data(tmp_path):
    import gzip
    import hashlib
    import os
    import shutil

    bundle = tmp_path / "bundle"
    (bundle / "scripts").mkdir(parents=True)
    for name in release.BUNDLE_FILES:
        (bundle / name).write_bytes(b"public placeholder")
    (bundle / "VERSION").write_text("0.1.2\n")
    (bundle / "whisper-guide-image.tar.gz").write_bytes(gzip.compress(b"image"))
    shutil.copy2(ROOT / "deploy/deploy.sh", bundle / "deploy.sh")
    (bundle / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256((bundle / name).read_bytes()).hexdigest()}  {name}\n"
            for name in release.BUNDLE_FILES
        )
    )
    (bundle / ".env").write_text("SECRET=do-not-change\nWHISPER_GUIDE_VERSION=0.1.0\n")
    (bundle / "data/sqlite").mkdir(parents=True)
    database = bundle / "data/sqlite/keep.db"
    database.write_bytes(b"existing user data")
    binary = tmp_path / "bin"
    binary.mkdir()
    log = tmp_path / "calls"
    docker = binary / "docker"
    docker.write_text("""#!/bin/sh
printf '%s\\n' "$*" >> "$CALL_LOG"
case "$1" in
info) echo '{}';;
load) cat >/dev/null;;
image) echo '[]';;
run) cat >/dev/null; exit 0;;
compose) exit 0;;
*) exit 99;;
esac
""")
    for name, body in (("id", "#!/bin/sh\necho 0\n"), ("chown", "#!/bin/sh\nexit 0\n")):
        script = binary / name
        script.write_text(body)
        script.chmod(0o755)
    docker.chmod(0o755)
    env = {**os.environ, "PATH": str(binary) + ":" + os.environ["PATH"], "CALL_LOG": str(log)}
    result = subprocess.run(
        ["bash", str(bundle / "deploy.sh")], env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr + result.stdout
    calls = log.read_text()
    assert "up -d --no-build --pull never --wait --wait-timeout 180" in calls
    assert "--network none --read-only" in calls
    assert "/.env,dst=" not in calls and "/data,dst=" not in calls
    assert (bundle / ".env").read_text().startswith("SECRET=do-not-change")
    assert database.read_bytes() == b"existing user data"
