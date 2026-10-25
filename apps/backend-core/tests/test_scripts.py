from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(environment="test", data_dir=tmp_path, _env_file=None)
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def upload_file(client: TestClient, filename: str, content: bytes):
    return client.post("/api/scripts/upload", files=[("files", (filename, content, "text/plain"))])


def test_upload_multiple_scripts_and_read_content(client: TestClient) -> None:
    first_text = "大家好，欢迎参加 AI 2026 大会。\n谢谢！"
    response = client.post(
        "/api/scripts/upload",
        files=[
            ("files", ("开场.txt", first_text.encode(), "text/plain")),
            ("files", ("总结.text", "谢谢大家。".encode(), "text/plain")),
        ],
    )
    assert response.status_code == 201
    created = response.json()
    assert len(created) == 2
    assert created[0]["name"] == "开场.txt"
    assert created[0]["content"] == first_text
    assert created[0]["character_count"] == len(first_text)
    assert created[0]["created_at"].endswith("Z")
    assert created[0]["id"] != created[1]["id"]

    listing = client.get("/api/scripts").json()
    assert {script["id"] for script in listing} == {script["id"] for script in created}
    assert all("content" not in script for script in listing)
    selected = client.get(f"/api/scripts/{created[0]['id']}")
    assert selected.status_code == 200
    assert selected.json()["content"] == first_text


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "gb18030"])
def test_chinese_text_encodings_are_preserved(client: TestClient, encoding: str) -> None:
    text = "欢迎参加大会，今天讨论语音追踪。"
    response = upload_file(client, "讲稿.txt", text.encode(encoding))
    assert response.status_code == 201
    assert response.json()[0]["content"] == text


@pytest.mark.parametrize("content", [b"", b" \t\r\n", b"hello\x00world", b"\x00\x01"])
def test_empty_or_binary_text_is_rejected(client: TestClient, content: bytes) -> None:
    response = upload_file(client, "无效.txt", content)
    assert response.status_code == 422
    assert client.get("/api/scripts").json() == []


def test_invalid_batch_saves_no_partial_uploads(client: TestClient) -> None:
    response = client.post(
        "/api/scripts/upload",
        files=[
            ("files", ("有效.txt", b"Valid script.", "text/plain")),
            ("files", ("空白.txt", b"   ", "text/plain")),
        ],
    )
    assert response.status_code == 422
    assert client.get("/api/scripts").json() == []


def test_identical_filenames_create_separate_scripts(client: TestClient) -> None:
    first = upload_file(client, "讲稿.txt", b"First script.").json()[0]
    second = upload_file(client, "讲稿.txt", b"Second script.").json()[0]
    assert first["id"] != second["id"]
    assert client.get(f"/api/scripts/{first['id']}").json()["content"] == "First script."
    assert client.get(f"/api/scripts/{second['id']}").json()["content"] == "Second script."


def test_unsupported_file_type_is_rejected(client: TestClient) -> None:
    response = upload_file(client, "讲稿.docx", b"Not a supported file.")
    assert response.status_code == 422


def test_missing_script_returns_not_found(client: TestClient) -> None:
    assert client.get("/api/scripts/missing-script").status_code == 404


def test_upload_limits_are_applied_without_partial_writes(tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        data_dir=tmp_path,
        script_max_file_bytes=10,
        script_max_upload_files=1,
        _env_file=None,
    )
    with TestClient(create_app(settings)) as limited_client:
        too_large = upload_file(limited_client, "large.txt", b"a" * 11)
        assert too_large.status_code == 413
        too_many = limited_client.post(
            "/api/scripts/upload",
            files=[
                ("files", ("first.txt", b"first", "text/plain")),
                ("files", ("second.txt", b"second", "text/plain")),
            ],
        )
        assert too_many.status_code == 422
        assert limited_client.get("/api/scripts").json() == []


def test_script_survives_application_restart(tmp_path: Path) -> None:
    settings = Settings(environment="test", data_dir=tmp_path, _env_file=None)
    with TestClient(create_app(settings)) as first_client:
        script = upload_file(first_client, "persistent.txt", b"Saved script.").json()[0]
    with TestClient(create_app(settings)) as second_client:
        response = second_client.get(f"/api/scripts/{script['id']}")
        assert response.json()["content"] == "Saved script."
        assert response.json()["created_at"].endswith("Z")
    assert {path.name for path in tmp_path.iterdir()} == {"whisper-guide.db"}
