from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_health_is_available_after_startup_with_sqlite(tmp_path: Path) -> None:
    settings = Settings(environment="test", data_dir=tmp_path, _env_file=None)

    with TestClient(create_app(settings)) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "service": "whisper-guide-backend",
            "version": "0.1.0",
        }
        assert (tmp_path / "whisper-guide.db").is_file()


def test_configured_frontend_origin_can_call_the_api(tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        data_dir=tmp_path,
        cors_origins=["http://localhost:5173"],
        _env_file=None,
    )

    with TestClient(create_app(settings)) as client:
        response = client.options(
            "/api/health",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
