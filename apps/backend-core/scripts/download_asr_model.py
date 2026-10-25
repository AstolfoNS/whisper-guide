"""Download the recommended bilingual streaming sherpa-onnx model."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import stat
import tarfile
import urllib.request
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError

MODEL_NAME = "sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20"
MODEL_URL = (
    f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{MODEL_NAME}.tar.bz2"
)
DECODER_NAME = "decoder-epoch-99-avg-1.onnx"
DECODER_URL = f"https://huggingface.co/csukuangfj/{MODEL_NAME}/resolve/main/{DECODER_NAME}"
DECODER_SIZE = 13_876_452
DECODER_SHA256 = "2e3b5ec371f8899ee6acd829fd753ba45772df57a91bdf37cde3136354e7db7d"
DECODER_PARTIAL_NAME = f".{DECODER_NAME}.part"
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
        "--decoder-url",
        default=os.environ.get("WHISPER_GUIDE_DECODER_URL", DECODER_URL),
        help="FP32 decoder URL；也可通过 WHISPER_GUIDE_DECODER_URL 指定可信镜像。",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="单次 HTTP 连接和读取超时（秒）。",
    )
    args = parser.parse_args()
    requested_dir = args.models_dir.expanduser()
    if requested_dir.is_symlink():
        raise RuntimeError(f"Refusing a symbolic link at model download path: {requested_dir}")
    models_dir = requested_dir.resolve()
    target = models_dir / MODEL_NAME
    temporary = models_dir / PARTIAL_NAME
    archive = models_dir / f"{MODEL_NAME}.tar.bz2"
    staging = models_dir / f".{MODEL_NAME}.extracting"
    _validate_work_paths(models_dir)
    if _model_is_complete(target):
        _ensure_fp32_decoder(target, args.decoder_url, timeout=args.timeout)
        _slim_model(target)
        _clear_downloads(models_dir)
        print(f"Model already present: {target}")
        return
    models_dir.mkdir(parents=True, exist_ok=True)
    _adopt_legacy_partial(models_dir, temporary)
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
            raise RuntimeError(f"模型归档已解压，但缺少 tokens/encoder/decoder/joiner：{extracted}")
        # Finish precision upgrades inside staging before replacing any target.
        _ensure_fp32_decoder(extracted, args.decoder_url, timeout=args.timeout)
        _slim_model(extracted)
        if target.exists():
            shutil.rmtree(target)
        extracted.replace(target)
    except (EOFError, OSError, RuntimeError, tarfile.TarError) as error:
        # Keep the complete HTTP response so a later invocation can resume it.
        archive.replace(temporary)
        raise RuntimeError(
            f"模型归档或 FP32 decoder 准备失败，已保留断点文件：{temporary}"
        ) from error
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    _slim_model(target)
    _clear_downloads(models_dir)
    print(f"Model ready: {target}")


def _model_files(target: Path) -> dict[str, Path] | None:
    """Match runtime: INT8 encoder/joiner, FP32 decoder, then filename order."""
    if target.is_symlink():
        return None
    tokens = target / "tokens.txt"
    if tokens.is_symlink() or not tokens.is_file() or tokens.stat().st_size == 0:
        return None
    result = {"tokens": tokens}
    for component in ("encoder", "decoder", "joiner"):
        paths = list(target.glob(f"{component}*.onnx"))
        # Runtime accepts links; reject the directory rather than select a different
        # fallback file and accidentally report an external weight as ready.
        if any(path.is_symlink() for path in paths):
            return None
        candidates = [path for path in paths if path.is_file() and path.stat().st_size > 0]
        if not candidates:
            return None
        candidates.sort(key=lambda path: _precision_key(path.name, component))
        result[component] = candidates[0]
    return result


def _precision_key(name: str, component: str) -> tuple[bool, str]:
    prefer_other = "int8" in name if component == "decoder" else "int8" not in name
    return prefer_other, name


def _model_is_complete(target: Path) -> bool:
    return _model_files(target) is not None


def _validate_work_paths(models_dir: Path) -> None:
    """Never traverse a preexisting link at a download, staging or model path."""
    for name in (
        MODEL_NAME,
        PARTIAL_NAME,
        LEGACY_PARTIAL_NAME,
        f"{MODEL_NAME}.tar.bz2",
        f".{MODEL_NAME}.extracting",
    ):
        path = models_dir / name
        if path.is_symlink():
            raise RuntimeError(f"Refusing a symbolic link at model download path: {path}")
        if path.exists() and name in (MODEL_NAME, f".{MODEL_NAME}.extracting"):
            if not path.is_dir():
                raise RuntimeError(f"Expected a model directory: {path}")
        elif path.exists() and not path.is_file():
            raise RuntimeError(f"Expected a model archive file: {path}")

    for directory in (
        models_dir / MODEL_NAME,
        models_dir / f".{MODEL_NAME}.extracting" / MODEL_NAME,
    ):
        if directory.is_symlink():
            raise RuntimeError(f"Refusing a symbolic link at model download path: {directory}")
        for name in (DECODER_NAME, DECODER_PARTIAL_NAME):
            _regular_file_size(directory / name)
        if directory.is_dir():
            for path in (*directory.glob("*.onnx"), directory / "tokens.txt"):
                if path.is_symlink():
                    raise RuntimeError(f"Refusing a symbolic link at model download path: {path}")


def _regular_file_size(path: Path) -> int:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return 0
    if stat.S_ISLNK(metadata.st_mode):
        raise RuntimeError(f"Refusing a symbolic link at model download path: {path}")
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError(f"Expected a regular model download file: {path}")
    return metadata.st_size


def _verify_decoder(path: Path) -> None:
    if _regular_file_size(path) != DECODER_SIZE:
        raise RuntimeError("FP32 decoder size does not match the verified model")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise RuntimeError("Expected a regular FP32 decoder file")
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != DECODER_SHA256:
        raise RuntimeError("FP32 decoder SHA256 does not match the verified model")


def _ensure_fp32_decoder(target: Path, url: str, *, timeout: int) -> None:
    if target.is_symlink():
        raise RuntimeError(f"Refusing a symbolic link at model download path: {target}")
    destination = target / DECODER_NAME
    temporary = target / DECODER_PARTIAL_NAME
    _regular_file_size(destination)
    _regular_file_size(temporary)
    selected = _model_files(target)
    if selected is None:
        raise RuntimeError(f"Cannot upgrade an incomplete model: {target}")
    if "int8" not in selected["decoder"].name:
        return
    print("Downloading the verified FP32 decoder only")
    _download_with_resume(url, temporary, timeout=timeout, max_bytes=DECODER_SIZE)
    try:
        _verify_decoder(temporary)
    except (OSError, RuntimeError):
        # Corrupt completed responses are not useful resume anchors. Interrupted
        # HTTP downloads remain available to the existing Range-resume helper.
        if temporary.is_file() and not temporary.is_symlink():
            temporary.unlink()
        raise
    # Recheck both fixed paths before the atomic replacement. The original INT8
    # model remains usable until verification has succeeded.
    _regular_file_size(destination)
    _regular_file_size(temporary)
    temporary.replace(destination)


def _slim_model(target: Path) -> None:
    """Only discard known redundant artifacts once all four runtime files exist."""
    selected = _model_files(target)
    if selected is None:
        raise RuntimeError(f"Cannot slim an incomplete model: {target}")
    retained = set(selected.values())
    for path in (*target.glob("*.onnx"), target / "bpe.model", target / "bpe.vocab"):
        if path not in retained and not path.is_symlink() and path.is_file():
            path.unlink()
    samples = target / "test_wavs"
    if not samples.is_symlink() and samples.is_dir():
        shutil.rmtree(samples)


def _clear_downloads(models_dir: Path) -> None:
    # Fixed names belong to this model only; other models/downloads are untouched.
    paths = [
        *(
            models_dir / name
            for name in (PARTIAL_NAME, LEGACY_PARTIAL_NAME, f"{MODEL_NAME}.tar.bz2")
        ),
        models_dir / MODEL_NAME / DECODER_PARTIAL_NAME,
    ]
    for path in paths:
        if not path.is_symlink() and path.is_file():
            path.unlink()


def _adopt_legacy_partial(models_dir: Path, temporary: Path) -> None:
    legacy = models_dir / LEGACY_PARTIAL_NAME
    if not temporary.exists() and legacy.is_file():
        legacy.replace(temporary)
        print(f"Adopted legacy partial archive: {legacy.name}")


def _download_with_resume(
    url: str, destination: Path, *, timeout: int, max_bytes: int | None = None
) -> None:
    existing = _regular_file_size(destination)
    if max_bytes is not None and existing > max_bytes:
        destination.unlink()
        existing = 0
    headers = {"Range": f"bytes={existing}-"} if existing else {}
    request = urllib.request.Request(url, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except HTTPError as error:
        if error.code != 416 or not existing:
            raise
        # The partial file is longer than the remote object. Restart safely.
        _regular_file_size(destination)
        destination.unlink()
        response = urllib.request.urlopen(urllib.request.Request(url), timeout=timeout)
        existing = 0
    with response:
        status = getattr(response, "status", None)
        append = existing > 0 and status == 206
        mode = "ab" if append else "wb"
        if existing and not append:
            print("Server did not honor Range; restarting the archive download.")
        _regular_file_size(destination)
        flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if append else os.O_TRUNC)
        descriptor = os.open(destination, flags | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, mode) as output:
            if not stat.S_ISREG(os.fstat(output.fileno()).st_mode):
                raise RuntimeError("Expected a regular model download file")
            if max_bytes is None:
                shutil.copyfileobj(response, output, length=1024 * 1024)
            else:
                total = existing if append else 0
                while chunk := response.read(min(1024 * 1024, max_bytes - total + 1)):
                    total += len(chunk)
                    if total > max_bytes:
                        raise RuntimeError("FP32 decoder download exceeds the expected size")
                    output.write(chunk)
    print(f"Downloaded {_regular_file_size(destination)} bytes")


def _safe_extract(bundle: tarfile.TarFile, destination: Path) -> None:
    """Validate the whole archive, then extract only the runtime's selected set."""
    root = destination.resolve()
    members: dict[str, tarfile.TarInfo] = {}
    for member in bundle.getmembers():
        name = PurePosixPath(member.name)
        output = (destination / member.name).resolve()
        if (
            name.is_absolute()
            or ".." in name.parts
            or (root != output and root not in output.parents)
        ):
            raise RuntimeError(f"Archive contains an unsafe path: {member.name}")
        if not member.isdir() and not member.isfile():
            raise RuntimeError(f"Archive contains a link or special file: {member.name}")
        if member.isfile() and name.parent == PurePosixPath(MODEL_NAME):
            if name.name in members:
                raise RuntimeError(f"Archive contains a duplicate model file: {member.name}")
            members[name.name] = member
    tokens = members.get("tokens.txt")
    if tokens is None or tokens.size == 0:
        raise RuntimeError("Archive is missing nonempty tokens.txt")
    selected = [tokens]
    for component in ("encoder", "decoder", "joiner"):
        candidates = [
            member
            for name, member in members.items()
            if name.startswith(component) and name.endswith(".onnx") and member.size > 0
        ]
        if not candidates:
            raise RuntimeError(f"Archive is missing a nonempty {component} model")
        candidates.sort(
            key=lambda member: _precision_key(PurePosixPath(member.name).name, component)
        )
        selected.append(candidates[0])
    model_dir = destination / MODEL_NAME
    if destination.is_symlink() or model_dir.is_symlink():
        raise RuntimeError("Refusing a symbolic link at model extraction path")
    model_dir.mkdir(parents=True, exist_ok=True)
    for member in selected:
        output = model_dir / PurePosixPath(member.name).name
        if output.is_symlink():
            raise RuntimeError(f"Refusing a symbolic link at model extraction path: {output}")
        source = bundle.extractfile(member)
        if source is None:
            raise RuntimeError(f"Cannot read model archive member: {member.name}")
        with source, output.open("wb") as stream:
            shutil.copyfileobj(source, stream, length=1024 * 1024)
        if output.stat().st_size != member.size:
            raise RuntimeError(f"Model archive member is incomplete: {member.name}")


if __name__ == "__main__":
    main()
