"""Verify an offline release using its loaded image inspection data; stdlib only."""

import argparse
import json
import re
import sys
from pathlib import Path


def verify(directory: Path, evidence: dict) -> None:
    manifest = json.loads((directory / "release.json").read_text())
    version = (directory / "VERSION").read_text().strip()
    if not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version):
        raise ValueError("Invalid release version.")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("version") != version
        or manifest.get("tag") != "v" + version
        or not re.fullmatch(r"[0-9a-f]{40}", manifest.get("revision", ""))
        or manifest.get("platform") != "linux/amd64"
    ):
        raise ValueError("Invalid release provenance.")
    engine = evidence["engine"]
    if engine.get("OSType") != "linux" or engine.get("Architecture") not in {
        "amd64",
        "x86_64",
    }:
        raise ValueError("This release requires a linux/amd64 Docker engine.")
    compose = json.loads((directory / "docker-compose.yml").read_text())
    services = compose.get("services", {})
    if set(services) != {"backend", "frontend"} or set(manifest.get("images", {})) != set(services):
        raise ValueError("Unexpected services in bundle.")
    images = evidence["images"]
    if len(images) != 2:
        raise ValueError("Expected exactly two inspected images.")
    for service, actual in zip(("backend", "frontend"), images, strict=True):
        expected = manifest["images"][service]
        reference = f"whisper-guide-{service}:{version}"
        config = services[service]
        if (
            config.get("image") != reference
            or config.get("platform") != "linux/amd64"
            or "build" in config
            or expected.get("reference") != reference
            or expected.get("platform") != "linux/amd64"
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", expected.get("id", ""))
            or actual.get("Id") != expected["id"]
            or reference not in (actual.get("RepoTags") or [])
            or actual.get("Os") != "linux"
            or actual.get("Architecture") != "amd64"
        ):
            raise ValueError(f"Image identity/platform mismatch: {service}")
        labels = (actual.get("Config") or {}).get("Labels") or {}
        for key, value in {
            "org.opencontainers.image.version": version,
            "org.opencontainers.image.revision": manifest["revision"],
        }.items():
            if labels.get(key) != value or expected.get("labels", {}).get(key) != value:
                raise ValueError(f"Image provenance label mismatch: {service}")
    print(f"Verified release {version} at {manifest['revision']} (linux/amd64).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, default=Path("/release"))
    args = parser.parse_args()
    try:
        verify(args.release_dir, json.load(sys.stdin))
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(1, f"Release verification failed: {error}\n")


if __name__ == "__main__":
    main()
