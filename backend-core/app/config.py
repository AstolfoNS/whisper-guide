"""Application configuration loaded from environment variables or a local .env file."""

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WHISPER_GUIDE_",
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    data_dir: Path = BACKEND_ROOT / "data"
    backup_dir: Path = BACKEND_ROOT / "backups"
    script_max_file_bytes: int = Field(default=2 * 1024 * 1024, gt=0)
    script_max_upload_files: int = Field(default=10, gt=0)
    default_silence_timeout_ms: int = Field(default=3000, gt=0)
    speech_model_dir: Path = (
        BACKEND_ROOT / "models" / "sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20"
    )
    speech_sample_rate: int = 16_000
    tts_voice: str = "zh-CN-XiaoxiaoNeural"
    server_host: str = "127.0.0.1"
    server_port: int = Field(default=8000, gt=0, le=65535)
    stt_provider: str = "local"
    # Remote providers are backend-owned JSON configuration. Secrets are never returned.
    stt_provider_configs: list[dict[str, Any]] = Field(default_factory=list)
    cloud_endpoint: str = ""
    cloud_app_id: str = ""
    cloud_access_token: str = ""
    cloud_opt_in: bool = False
    # Optional semantic LLM alignment settings. Secrets remain server-side.
    llm_align_mode: Literal["off", "fallback", "hybrid"] = "off"
    llm_provider_preset: str = "custom"
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: str = ""
    llm_timeout_ms: int = Field(default=1500, gt=0)
    # Explicit names used by the runtime settings API; legacy cloud_* fields remain supported.
    cloud_stt_endpoint: str = ""
    cloud_stt_app_id: str = ""
    cloud_stt_access_token: str = ""
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    @property
    def resolved_data_dir(self) -> Path:
        path = self.data_dir.expanduser()
        return path if path.is_absolute() else BACKEND_ROOT / path

    @property
    def database_url(self) -> str:
        database_path = self.resolved_data_dir / "whisper-guide.db"
        return f"sqlite+pysqlite:///{database_path.as_posix()}"

    @property
    def resolved_backup_dir(self) -> Path:
        path = self.backup_dir.expanduser()
        return path if path.is_absolute() else BACKEND_ROOT / path

    @property
    def resolved_speech_model_dir(self) -> Path:
        path = self.speech_model_dir.expanduser()
        return path if path.is_absolute() else BACKEND_ROOT / path


@lru_cache
def get_settings() -> Settings:
    return Settings()


