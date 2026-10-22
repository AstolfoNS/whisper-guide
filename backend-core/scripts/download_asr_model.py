"""Download the recommended bilingual streaming sherpa-onnx model."""

from __future__ import annotations

import argparse
import shutil
import tarfile
import urllib.request
from pathlib import Path

MODEL_NAME = "sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20"
MODEL_URL = (
    f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{MODEL_NAME}.tar.bz2"
)
ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-dir", type=Path, default=ROOT / "models")
    args = parser.parse_args()
    models_dir = args.models_dir.expanduser().resolve()
    target = models_dir / MODEL_NAME
    required = ["tokens.txt"]
    complete = all((target / name).is_file() for name in required)
    complete = complete and all(
        any(target.glob(f"{part}*.onnx")) for part in ("encoder", "decoder", "joiner")
    )
    if complete:
        print(f"Model already present: {target}")
        return
    models_dir.mkdir(parents=True, exist_ok=True)
    archive = models_dir / f"{MODEL_NAME}.tar.bz2"
    temporary = models_dir / f".{MODEL_NAME}.part"
    try:
        print(f"Downloading {MODEL_URL}")
        with (
            urllib.request.urlopen(MODEL_URL, timeout=60) as response,
            temporary.open("wb") as output,
        ):
            shutil.copyfileobj(response, output)
        temporary.replace(archive)
        with tarfile.open(archive, mode="r:bz2") as bundle:
            _safe_extract(bundle, models_dir)
    finally:
        temporary.unlink(missing_ok=True)
        archive.unlink(missing_ok=True)
    print(f"Model ready: {target}")


def _safe_extract(bundle: tarfile.TarFile, destination: Path) -> None:
    root = destination.resolve()
    for member in bundle.getmembers():
        output = (destination / member.name).resolve()
        if root != output and root not in output.parents:
            raise RuntimeError(f"Archive contains an unsafe path: {member.name}")
    bundle.extractall(destination, filter="data")


if __name__ == "__main__":
    main()
