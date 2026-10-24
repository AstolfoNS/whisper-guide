"""Offline model-download, selective extraction and safe cleanup regressions."""

import io
import sys
import tarfile
from pathlib import Path
from urllib.error import HTTPError

import pytest

from app.infrastructure.speech import _model_files as runtime_model_files
from scripts import download_asr_model as downloader


def model_files(*, int8: bool = True) -> dict[str, bytes]:
    suffix = ".int8.onnx" if int8 else ".onnx"
    return {
        "tokens.txt": b"a 1\nb 2\n",
        **{
            f"{part}-epoch-99-avg-1{suffix}": part.encode()
            for part in ("encoder", "decoder", "joiner")
        },
    }


def write_model(target: Path, files: dict[str, bytes]) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (target / name).write_bytes(content)


def write_archive(path: Path, files: dict[str, bytes], *, extra=None) -> None:
    with tarfile.open(path, "w:bz2") as bundle:
        for name, content in files.items():
            member = tarfile.TarInfo(f"{downloader.MODEL_NAME}/{name}")
            member.size = len(content)
            bundle.addfile(member, io.BytesIO(content))
        if extra is not None:
            bundle.addfile(extra, io.BytesIO(b"x") if extra.isfile() else None)


@pytest.mark.parametrize("int8", [True, False])
def test_slim_keeps_exactly_the_files_selected_by_runtime(tmp_path, int8):
    target = tmp_path / downloader.MODEL_NAME
    files = model_files(int8=int8)
    if int8:
        files.update(model_files(int8=False))
    files["encoder-unused.onnx"] = b"unused"
    files["README.md"] = b"model provenance"
    files["LICENSE"] = b"model license"
    files["bpe.model"] = b"unused bpe"
    files["bpe.vocab"] = b"unused vocabulary"
    write_model(target, files)
    (target / "test_wavs").mkdir()
    (target / "test_wavs" / "example.wav").write_bytes(b"sample")
    selected = runtime_model_files(target)
    assert selected is not None
    expected = {path.name for path in selected.values()} | {"README.md", "LICENSE"}

    downloader._slim_model(target)

    assert {path.name for path in target.iterdir()} == expected
    assert runtime_model_files(target) == selected
    assert downloader._model_is_complete(target)


def test_slim_incomplete_model_leaves_all_files_untouched(tmp_path):
    target = tmp_path / downloader.MODEL_NAME
    files = model_files()
    del files["joiner-epoch-99-avg-1.int8.onnx"]
    files["encoder-unused.onnx"] = b"not expendable before completeness"
    files["bpe.model"] = b"keep until complete"
    write_model(target, files)

    with pytest.raises(RuntimeError, match="incomplete"):
        downloader._slim_model(target)

    assert {path.name: path.read_bytes() for path in target.iterdir()} == files


@pytest.mark.parametrize("int8", [True, False])
def test_archive_extracts_only_four_selected_core_files(tmp_path, int8):
    files = model_files(int8=int8)
    if int8:
        files.update(model_files(int8=False))
    files.update(
        {"test_wavs/example.wav": b"audio", "bpe.model": b"unused", "README.md": b"readme"}
    )
    archive = tmp_path / "archive.tar.bz2"
    write_archive(archive, files)
    destination = tmp_path / "extract"

    with tarfile.open(archive, "r:bz2") as bundle:
        downloader._safe_extract(bundle, destination)

    target = destination / downloader.MODEL_NAME
    assert {path.name for path in target.iterdir()} == set(model_files(int8=int8))
    assert downloader._model_files(target) == runtime_model_files(target)


@pytest.mark.parametrize("missing", ["tokens.txt", "encoder", "decoder", "joiner"])
def test_incomplete_archive_does_not_extract_even_partial_core_set(tmp_path, missing):
    files = {name: value for name, value in model_files().items() if not name.startswith(missing)}
    archive = tmp_path / "archive.tar.bz2"
    write_archive(archive, files)
    destination = tmp_path / "extract"

    with tarfile.open(archive, "r:bz2") as bundle, pytest.raises(RuntimeError, match="missing"):
        downloader._safe_extract(bundle, destination)

    assert not destination.exists()


@pytest.mark.parametrize(
    "unsafe_name", ["../outside", "/tmp/outside", f"{downloader.MODEL_NAME}/../../outside"]
)
def test_archive_rejects_unsafe_paths_even_for_unselected_members(tmp_path, unsafe_name):
    archive = tmp_path / "archive.tar.bz2"
    unsafe = tarfile.TarInfo(unsafe_name)
    unsafe.size = 1
    write_archive(archive, model_files(), extra=unsafe)

    with tarfile.open(archive, "r:bz2") as bundle, pytest.raises(RuntimeError, match="unsafe path"):
        downloader._safe_extract(bundle, tmp_path / "extract")

    assert not (tmp_path / "extract").exists()


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE])
def test_archive_rejects_links_and_special_members(tmp_path, kind):
    archive = tmp_path / "archive.tar.bz2"
    unsafe = tarfile.TarInfo(f"{downloader.MODEL_NAME}/test_wavs/linked")
    unsafe.type = kind
    unsafe.linkname = "../../outside"
    write_archive(archive, model_files(), extra=unsafe)

    with (
        tarfile.open(archive, "r:bz2") as bundle,
        pytest.raises(RuntimeError, match="link or special"),
    ):
        downloader._safe_extract(bundle, tmp_path / "extract")

    assert not (tmp_path / "extract").exists()


def test_ready_model_does_not_download_and_removes_only_its_fixed_residues(tmp_path, monkeypatch):
    target = tmp_path / downloader.MODEL_NAME
    write_model(target, model_files() | model_files(int8=False))
    leftovers = [
        downloader.PARTIAL_NAME,
        downloader.LEGACY_PARTIAL_NAME,
        f"{downloader.MODEL_NAME}.tar.bz2",
    ]
    for name in leftovers:
        (tmp_path / name).write_bytes(b"old download")
    (tmp_path / "another-model.tar.bz2").write_bytes(b"other download")
    other = tmp_path / "another-model"
    write_model(other, {"encoder.onnx": b"unrelated"})
    monkeypatch.setattr(sys, "argv", ["download_asr_model.py", "--models-dir", str(tmp_path)])
    monkeypatch.setattr(
        downloader, "_download_with_resume", lambda *args, **kwargs: pytest.fail("network")
    )

    downloader.main()

    assert not any((tmp_path / name).exists() for name in leftovers)
    assert (tmp_path / "another-model.tar.bz2").read_bytes() == b"other download"
    assert (other / "encoder.onnx").read_bytes() == b"unrelated"
    assert {path.name for path in target.iterdir()} == set(model_files())


def test_download_success_selectively_extracts_and_removes_archive(tmp_path, monkeypatch):
    source = tmp_path / "source.tar.bz2"
    write_archive(source, model_files() | model_files(int8=False) | {"test_wavs/a.wav": b"audio"})
    models = tmp_path / "models"
    monkeypatch.setattr(sys, "argv", ["download_asr_model.py", "--models-dir", str(models)])

    def fake_download(url, destination, *, timeout):
        destination.write_bytes(source.read_bytes())

    monkeypatch.setattr(downloader, "_download_with_resume", fake_download)
    downloader.main()

    assert {path.name for path in (models / downloader.MODEL_NAME).iterdir()} == set(model_files())
    assert {path.name for path in models.iterdir()} == {downloader.MODEL_NAME}


def test_failed_archive_preserves_partial_and_existing_incomplete_model(tmp_path, monkeypatch):
    models = tmp_path / "models"
    target = models / downloader.MODEL_NAME
    write_model(target, {"encoder-original.onnx": b"previous partial model"})
    source = tmp_path / "source.tar.bz2"
    write_archive(source, {"tokens.txt": b"a 1"})
    monkeypatch.setattr(sys, "argv", ["download_asr_model.py", "--models-dir", str(models)])
    monkeypatch.setattr(
        downloader,
        "_download_with_resume",
        lambda url, destination, **kwargs: destination.write_bytes(source.read_bytes()),
    )

    with pytest.raises(RuntimeError, match="断点文件"):
        downloader.main()

    assert (models / downloader.PARTIAL_NAME).read_bytes() == source.read_bytes()
    assert (target / "encoder-original.onnx").read_bytes() == b"previous partial model"
    assert not (models / f".{downloader.MODEL_NAME}.extracting").exists()


@pytest.mark.parametrize(
    "name", [downloader.MODEL_NAME, downloader.PARTIAL_NAME, f".{downloader.MODEL_NAME}.extracting"]
)
def test_main_rejects_symlink_work_paths_without_touching_target(tmp_path, monkeypatch, name):
    models = tmp_path / "models"
    models.mkdir()
    external = tmp_path / "outside"
    external.mkdir()
    (external / "important").write_bytes(b"keep")
    (models / name).symlink_to(external, target_is_directory=True)
    monkeypatch.setattr(sys, "argv", ["download_asr_model.py", "--models-dir", str(models)])

    with pytest.raises(RuntimeError, match="symbolic link"):
        downloader.main()

    assert (external / "important").read_bytes() == b"keep"
    assert (models / name).is_symlink()


def test_slim_never_follows_a_samples_symlink(tmp_path):
    target = tmp_path / downloader.MODEL_NAME
    write_model(target, model_files())
    external = tmp_path / "external"
    external.mkdir()
    (external / "sample.wav").write_bytes(b"keep")
    (target / "test_wavs").symlink_to(external, target_is_directory=True)

    downloader._slim_model(target)

    assert (external / "sample.wav").read_bytes() == b"keep"
    assert (target / "test_wavs").is_symlink()


def test_model_readiness_rejects_symlink_core_files(tmp_path):
    target = tmp_path / downloader.MODEL_NAME
    write_model(target, model_files())
    tokens = target / "tokens.txt"
    content = tokens.read_bytes()
    tokens.unlink()
    external = tmp_path / "external-tokens"
    external.write_bytes(content)
    tokens.symlink_to(external)

    assert not downloader._model_is_complete(target)
    with pytest.raises(RuntimeError, match="incomplete"):
        downloader._slim_model(target)
    assert len(list(target.glob("*.onnx"))) == 3


@pytest.mark.parametrize("status, expected", [(206, b"oldnew"), (200, b"new")])
def test_download_preserves_resume_or_restarts_when_range_ignored(
    tmp_path, monkeypatch, status, expected
):
    destination = tmp_path / "model.part"
    destination.write_bytes(b"old")
    response = io.BytesIO(b"new")
    response.status = status

    def open_request(request, *, timeout):
        assert request.headers["Range"] == "bytes=3-"
        assert timeout == 12
        return response

    monkeypatch.setattr(downloader.urllib.request, "urlopen", open_request)
    downloader._download_with_resume("https://example.invalid/model", destination, timeout=12)

    assert destination.read_bytes() == expected


def test_download_restarts_after_range_416(tmp_path, monkeypatch):
    destination = tmp_path / "model.part"
    destination.write_bytes(b"too long")
    requests = []

    def open_request(request, *, timeout):
        requests.append(request)
        if len(requests) == 1:
            raise HTTPError(request.full_url, 416, "range", {}, None)
        response = io.BytesIO(b"complete")
        response.status = 200
        return response

    monkeypatch.setattr(downloader.urllib.request, "urlopen", open_request)
    downloader._download_with_resume("https://example.invalid/model", destination, timeout=12)

    assert destination.read_bytes() == b"complete"
    assert "Range" not in requests[1].headers


@pytest.mark.parametrize("component", ["encoder", "decoder", "joiner"])
def test_linked_int8_cannot_hide_behind_a_regular_fp32_fallback(tmp_path, component):
    target = tmp_path / downloader.MODEL_NAME
    write_model(target, model_files(int8=False))
    external = tmp_path / "outside.onnx"
    external.write_bytes(b"external model")
    (target / f"{component}.int8.onnx").symlink_to(external)

    assert not downloader._model_is_complete(target)
    with pytest.raises(RuntimeError, match="incomplete"):
        downloader._slim_model(target)
    assert external.read_bytes() == b"external model"
    assert len(list(target.glob("*.onnx"))) == 4


def test_model_selection_handles_zero_length_and_multiple_candidates_like_runtime(tmp_path):
    target = tmp_path / downloader.MODEL_NAME
    write_model(target, model_files(int8=False))
    write_model(
        target,
        {
            "encoder-a.int8.onnx": b"first int8",
            "encoder-b.int8.onnx": b"second int8",
            "encoder-0.int8.onnx": b"",
            "decoder-a.int8.onnx": b"",
        },
    )
    expected = runtime_model_files(target)
    assert downloader._model_files(target) == expected

    downloader._slim_model(target)

    assert expected is not None
    assert {path.name for path in target.iterdir()} == {path.name for path in expected.values()}
    assert expected["encoder"].name == "encoder-a.int8.onnx"
    assert expected["decoder"].name == "decoder-epoch-99-avg-1.onnx"


def test_zero_length_tokens_never_make_model_ready_or_trigger_cleanup(tmp_path):
    target = tmp_path / downloader.MODEL_NAME
    write_model(target, model_files() | {"tokens.txt": b"", "encoder-unused.onnx": b"extra"})

    assert not downloader._model_is_complete(target)
    with pytest.raises(RuntimeError, match="incomplete"):
        downloader._slim_model(target)
    assert (target / "encoder-unused.onnx").read_bytes() == b"extra"


def test_archive_refuses_duplicate_core_member_before_writing(tmp_path):
    archive = tmp_path / "archive.tar.bz2"
    duplicate = tarfile.TarInfo(f"{downloader.MODEL_NAME}/tokens.txt")
    duplicate.size = 1
    write_archive(archive, model_files(), extra=duplicate)

    with tarfile.open(archive, "r:bz2") as bundle, pytest.raises(RuntimeError, match="duplicate"):
        downloader._safe_extract(bundle, tmp_path / "extract")

    assert not (tmp_path / "extract").exists()


def test_samples_directory_cleanup_does_not_follow_nested_links(tmp_path):
    target = tmp_path / downloader.MODEL_NAME
    write_model(target, model_files())
    samples = target / "test_wavs"
    samples.mkdir()
    external = tmp_path / "outside"
    external.mkdir()
    (external / "keep.wav").write_bytes(b"keep")
    (samples / "external").symlink_to(external, target_is_directory=True)

    downloader._slim_model(target)

    assert not samples.exists()
    assert (external / "keep.wav").read_bytes() == b"keep"
