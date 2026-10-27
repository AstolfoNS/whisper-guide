"""Tag-driven offline release tools; Python 3.12 stdlib only."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
PLATFORM = "linux/amd64"
BUNDLE_FILES = (
    "VERSION",
    "release.json",
    "docker-compose.yml",
    ".env.example",
    "whisper-guide-image.tar.gz",
    "DEPLOY.md",
    "scripts/backup.py",
    "deploy.sh",
    "verify_release.py",
)


def run(*args: str, cwd: Path | None = None, env: dict | None = None) -> str:
    return subprocess.check_output(args, cwd=cwd or ROOT, env=env, text=True).strip()


def version_value(value: str) -> str:
    if not VERSION_RE.fullmatch(value):
        raise ValueError("Version must be stable X.Y.Z (no v prefix or prerelease).")
    return value


def clean_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("WHISPER_GUIDE_", "COMPOSE_"))
    }


def check_versions(version: str) -> None:
    frontend = json.loads((ROOT / "apps/frontend/package.json").read_text())["version"]
    backend = tomllib.loads((ROOT / "apps/backend-core/pyproject.toml").read_text())["project"][
        "version"
    ]
    lock = tomllib.loads((ROOT / "apps/backend-core/uv.lock").read_text())
    locked = [p["version"] for p in lock["package"] if p["name"] == "backend-core"]
    if frontend != version or backend != version or locked != [version]:
        raise ValueError("Tag and manifest versions differ; run prepare before tagging.")


def metadata(tag: str | None) -> dict:
    if not tag:
        tags = run("git", "tag", "--points-at", "HEAD").splitlines()
        tags = [t for t in tags if t.startswith("v") and VERSION_RE.fullmatch(t[1:])]
        if len(tags) != 1:
            raise ValueError("HEAD must have exactly one stable version tag, or pass --tag.")
        tag = tags[0]
    if not tag.startswith("v"):
        raise ValueError("Release tag must start with v.")
    version = version_value(tag[1:])
    revision = run("git", "rev-parse", "HEAD")
    if run("git", "rev-parse", f"refs/tags/{tag}^{{commit}}") != revision:
        raise ValueError("Release tag must point to HEAD.")
    if run("git", "status", "--porcelain", "--untracked-files=all"):
        raise ValueError(
            "Release requires a clean worktree, including untracked non-ignored files."
        )
    check_versions(version)
    return {
        "schema_version": 1,
        "version": version,
        "tag": tag,
        "revision": revision,
        "platform": PLATFORM,
    }


def prepare(version: str) -> None:
    version_value(version)
    if run("git", "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Commit or save current changes before preparing a version.")
    paths = [
        ROOT / "apps/frontend/package.json",
        ROOT / "apps/backend-core/pyproject.toml",
        ROOT / "apps/backend-core/uv.lock",
    ]
    originals = {p: p.read_bytes() for p in paths}
    try:
        package = json.loads(paths[0].read_text())
        package["version"] = version
        paths[0].write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n")
        text, count = re.subn(
            r'(?m)^version = "[^"]+"$',
            f'version = "{version}"',
            paths[1].read_text(),
            count=1,
        )
        if count != 1:
            raise ValueError("Cannot locate backend project version.")
        paths[1].write_text(text)
        # Only our virtual project version changes; retain all resolved dependencies.
        lock_text = paths[2].read_text()
        blocks = lock_text.split("[[package]]")
        updated = 0
        for index, block in enumerate(blocks[1:], 1):
            if re.search(r'^name = "backend-core"$', block, re.MULTILINE):
                blocks[index], count = re.subn(
                    r'^version = "[^"]+"$',
                    f'version = "{version}"',
                    block,
                    count=1,
                    flags=re.MULTILINE,
                )
                updated += count
        if updated != 1:
            raise ValueError("Cannot locate the virtual project version in uv.lock.")
        paths[2].write_text("[[package]]".join(blocks))
        subprocess.run(["uv", "lock", "--locked", "--offline"], cwd=paths[1].parent, check=True)
        check_versions(version)
    except BaseException:
        for path, content in originals.items():
            path.write_bytes(content)
        raise
    print(f"Prepared {version}; review and commit the manifests and lockfile, then tag v{version}.")


def render_compose(version: str, output: Path) -> None:
    version_value(version)
    value = json.loads(
        run(
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "-f",
            str(ROOT / "docker-compose.yml"),
            "config",
            "--no-interpolate",
            "--no-path-resolution",
            "--format",
            "json",
            env=clean_environment(),
        )
    )
    if set(value["services"]) != {"backend", "frontend"}:
        raise ValueError("Unexpected Compose services.")
    for service in ("backend", "frontend"):
        config = value["services"][service]
        config.pop("build", None)
        config["image"] = f"whisper-guide-{service}:{version}"
        config["platform"] = PLATFORM
    output.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def manifest(meta: dict, output: Path) -> None:
    refs = [f"whisper-guide-{service}:{meta['version']}" for service in ("backend", "frontend")]
    images = json.loads(run("docker", "image", "inspect", *refs))
    result = {**meta, "images": {}}
    for service, reference, image in zip(("backend", "frontend"), refs, images, strict=True):
        labels = image.get("Config", {}).get("Labels") or {}
        platform = f"{image['Os']}/{image['Architecture']}"
        if (
            platform != PLATFORM
            or labels.get("org.opencontainers.image.version") != meta["version"]
            or labels.get("org.opencontainers.image.revision") != meta["revision"]
        ):
            raise ValueError(f"Image provenance/platform mismatch: {reference}")
        result["images"][service] = {
            "reference": reference,
            "id": image["Id"],
            "platform": platform,
            "labels": {
                key: labels[key]
                for key in (
                    "org.opencontainers.image.version",
                    "org.opencontainers.image.revision",
                )
            },
        }
    output.write_text(json.dumps(result, indent=2) + "\n")


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def package(tag: str | None, platform: str) -> None:
    if platform != PLATFORM:
        raise ValueError("Only verified linux/amd64 releases are supported.")
    meta = metadata(tag)
    directory = ROOT / "release" / meta["version"] / "linux-amd64"
    if directory.exists():
        raise ValueError(f"Output already exists; refusing to overwrite: {directory}")
    run("docker", "info", "--format", "{{.OSType}}/{{.Architecture}}")
    refs = [f"whisper-guide-{service}:{meta['version']}" for service in ("backend", "frontend")]
    for reference in refs:
        existing = subprocess.run(
            ["docker", "image", "inspect", reference],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if existing.returncode == 0:
            raise ValueError(
                f"Version image already exists; do not overwrite released tags: {reference}"
            )
    env = clean_environment()
    env.update(
        WHISPER_GUIDE_VERSION=meta["version"],
        WHISPER_GUIDE_REVISION=meta["revision"],
        DOCKER_DEFAULT_PLATFORM=PLATFORM,
    )
    subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "-f",
            str(ROOT / "docker-compose.yml"),
            "build",
        ],
        cwd=ROOT,
        env=env,
        check=True,
    )
    staging_parent = ROOT / "release"
    staging_parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".bundle-", dir=staging_parent) as tmp:
        stage = Path(tmp)
        bundle = stage / "bundle"
        (bundle / "scripts").mkdir(parents=True)
        for source, target in (
            (".env.example", ".env.example"),
            ("DEPLOY.md", "DEPLOY.md"),
            ("deploy/deploy.sh", "deploy.sh"),
            ("deploy/verify_release.py", "verify_release.py"),
            ("scripts/backup.py", "scripts/backup.py"),
        ):
            shutil.copy2(ROOT / source, bundle / target)
        (bundle / "VERSION").write_text(meta["version"] + "\n")
        render_compose(meta["version"], bundle / "docker-compose.yml")
        manifest(meta, bundle / "release.json")
        image_archive = bundle / "whisper-guide-image.tar.gz"
        with image_archive.open("wb") as stream:
            save = subprocess.Popen(["docker", "save", *refs], stdout=subprocess.PIPE)
            assert save.stdout is not None
            compress = subprocess.Popen(["gzip", "-n"], stdin=save.stdout, stdout=stream)
            save.stdout.close()
            compressed = compress.wait()
            saved = save.wait()
        if compressed or saved:
            raise RuntimeError("Image export failed; no completed release was written.")
        checksums = "".join(f"{sha256(bundle / name)}  {name}\n" for name in BUNDLE_FILES)
        (bundle / "SHA256SUMS").write_text(checksums)
        filename = f"whisper-guide-release-{meta['version']}-linux-amd64.tar"
        archive = stage / filename
        with tarfile.open(archive, "w") as tar:
            for name in (*BUNDLE_FILES, "SHA256SUMS"):
                tar.add(bundle / name, arcname=name, recursive=False)
        (stage / (filename + ".sha256")).write_text(f"{sha256(archive)}  {filename}\n")
        shutil.rmtree(bundle)
        directory.parent.mkdir(parents=True, exist_ok=True)
        stage.rename(directory)
        print(directory / filename)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("prepare")
    command.add_argument("version")
    command = commands.add_parser("metadata")
    command.add_argument("--tag")
    command = commands.add_parser("render-compose")
    command.add_argument("--version", required=True)
    command.add_argument("--output", type=Path, required=True)
    command = commands.add_parser("manifest")
    command.add_argument("--tag")
    command.add_argument("--output", type=Path, required=True)
    command = commands.add_parser("package")
    command.add_argument("--tag")
    command.add_argument("--platform", default=PLATFORM)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            prepare(args.version)
        elif args.command == "metadata":
            print(json.dumps(metadata(args.tag)))
        elif args.command == "render-compose":
            render_compose(args.version, args.output)
        elif args.command == "manifest":
            manifest(metadata(args.tag), args.output)
        else:
            package(args.tag, args.platform)
    except (ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Release error: {error}\n")


if __name__ == "__main__":
    main()
