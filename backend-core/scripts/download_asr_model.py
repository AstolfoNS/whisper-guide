"""Download the recommended bilingual streaming sherpa-onnx model."""

from __future__ import annotations

import argparse
import os
import shutil
import tarfile
import urllib.request
from urllib.error import HTTPError
from pathlib import Path

MODEL_NAME = "sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20"
MODEL_URL = (
    f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{MODEL_NAME}.tar.bz2"
)
ROOT = Path(__file__).resolve().parents[1]
PARTIAL_NAME = f".{MODEL_NAME}.tar.bz2.part"
LEGACY_PARTIAL_NAME = "asr-model.tar.bz2"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-dir", type=Path, default=ROOT / "models")
    parser.add_argument(
        "--url",
        default=os.environ.get("WHISPER_GUIDE_MODEL_URL", MODEL_URL),
        help="模型归档 URL；也可通过 WHISPER_GUIDE_MODEL_URL 设置镜像地址。",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="单次 HTTP 连接和读取超时（秒）。",
    )
    args = parser.parse_args()
    models_dir = args.models_dir.expanduser().resolve()
    target = models_dir / MODEL_NAME
    if _model_is_complete(target):
        print(f"Model already present: {target}")
        return
    models_dir.mkdir(parents=True, exist_ok=True)
    temporary = models_dir / PARTIAL_NAME
    _adopt_legacy_partial(models_dir, temporary)
    archive = models_dir / f"{MODEL_NAME}.tar.bz2"
    staging = models_dir / f".{MODEL_NAME}.extracting"
    print(f"Downloading {args.url}")
    _download_with_resume(args.url, temporary, timeout=args.timeout)
    temporary.replace(archive)
    try:
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        with tarfile.open(archive, mode="r:bz2") as bundle:
            _safe_extract(bundle, staging)
        extracted = staging / MODEL_NAME
        if not _model_is_complete(extracted):
            raise RuntimeError(
                f"模型归档已解压，但缺少 tokens/encoder/decoder/joiner：{extracted}"
            )
        if target.exists():
            shutil.rmtree(target)
        extracted.replace(target)
    except (EOFError, OSError, RuntimeError, tarfile.TarError) as error:
        # Keep the complete HTTP response so a later invocation can resume it.
        archive.replace(temporary)
        raise RuntimeError(
            f"模型归档不完整或损坏，已保留断点文件：{temporary}"
        ) from error
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    archive.unlink(missing_ok=True)
    print(f"Model ready: {target}")


def _model_is_complete(target: Path) -> bool:
    tokens = target / "tokens.txt"
    if not tokens.is_file() or tokens.stat().st_size == 0:
        return False
    return all(
        any(path.is_file() and path.stat().st_size > 0 for path in target.glob(f"{part}*.onnx"))
        for part in ("encoder", "decoder", "joiner")
    )


def _adopt_legacy_partial(models_dir: Path, temporary: Path) -> None:
    legacy = models_dir / LEGACY_PARTIAL_NAME
    if not temporary.exists() and legacy.is_file():
        legacy.replace(temporary)
        print(f"Adopted legacy partial archive: {legacy.name}")


def _download_with_resume(url: str, destination: Path, *, timeout: int) -> None:
    existing = destination.stat().st_size if destination.exists() else 0
    headers = {"Range": f"bytes={existing}-"} if existing else {}
    request = urllib.request.Request(url, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except HTTPError as error:
        if error.code != 416 or not existing:
            raise
        # The partial file is longer than the remote object. Restart safely.
        destination.unlink()
        response = urllib.request.urlopen(urllib.request.Request(url), timeout=timeout)
        existing = 0
    with response:
        status = getattr(response, "status", None)
        append = existing > 0 and status == 206
        mode = "ab" if append else "wb"
        if existing and not append:
            print("Server did not honor Range; restarting the archive download.")
        with destination.open(mode) as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)
    print(f"Downloaded {destination.stat().st_size} bytes")


def _safe_extract(bundle: tarfile.TarFile, destination: Path) -> None:
    root = destination.resolve()
    for member in bundle.getmembers():
        output = (destination / member.name).resolve()
        if root != output and root not in output.parents:
            raise RuntimeError(f"Archive contains an unsafe path: {member.name}")
    bundle.extractall(destination, filter="data")


if __name__ == "__main__":
    main()
