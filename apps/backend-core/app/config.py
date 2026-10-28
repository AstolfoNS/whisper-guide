"""Environment defaults and private, persisted runtime model settings."""

from __future__ import annotations

import json
import os
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[1]
MODEL_SETTINGS_FIELDS = (
    "stt_provider",
    "cloud_opt_in",
    "cloud_stt_endpoint",
    "cloud_stt_protocol",
    "cloud_stt_auth_mode",
    "cloud_stt_resource_id",
    "cloud_stt_timeout_ms",
    "cloud_stt_enable_nonstream",
    "cloud_stt_app_id",
    "cloud_stt_access_token",
    "llm_align_mode",
    "llm_provider_preset",
    "llm_base_url",
    "llm_model",
    "llm_api_key",
    "llm_timeout_ms",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WHISPER_GUIDE_",
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_title: str = "whisper-guide"
    environment: Literal["development", "test", "production"] = "development"
    data_dir: Path = BACKEND_ROOT / "data"
    backup_dir: Path = BACKEND_ROOT / "backups"
    script_max_file_bytes: int = Field(default=2 * 1024 * 1024, gt=0)
    # FastAPI's multipart parser accepts at most 1000 files per request.
    script_max_upload_files: int = Field(default=10, gt=0, le=1000)
    default_silence_timeout_ms: int = Field(default=3000, ge=1000)
    default_prompt_mode: Literal["silence", "progress"] = "silence"
    default_progress_timeout_ms: int = Field(default=4000, ge=2000, le=10000)
    default_continuous_prompt: bool = False
    speech_model_dir: Path = (
        BACKEND_ROOT / "models" / "sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20"
    )
    # Browser transport, acoustic evidence, and session PCM all use 16 kHz.
    speech_sample_rate: Literal[16_000] = 16_000
    tts_voice: str = "zh-CN-XiaoxiaoNeural"
    stt_provider: str = "local"
    stt_provider_configs: list[dict[str, Any]] = Field(default_factory=list)
    # Legacy cloud aliases remain supported by existing installations.
    cloud_endpoint: str = ""
    cloud_app_id: str = ""
    cloud_access_token: str = ""
    cloud_opt_in: bool = False
    cloud_stt_endpoint: str = ""
    cloud_stt_protocol: Literal["http-json", "volcengine-ws"] = "http-json"
    cloud_stt_auth_mode: Literal["api-key", "app-token"] = "api-key"
    cloud_stt_resource_id: str = "volc.seedasr.sauc.duration"
    cloud_stt_timeout_ms: int = Field(default=5000, ge=200, le=30000)
    cloud_stt_enable_nonstream: bool = True
    cloud_stt_app_id: str = ""
    cloud_stt_access_token: str = ""
    llm_align_mode: Literal["off", "fallback", "hybrid"] = "off"
    llm_provider_preset: str = "custom"
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: str = ""
    llm_timeout_ms: int = Field(default=1500, gt=0)
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    @field_validator("speech_sample_rate", mode="before")
    @classmethod
    def validate_speech_sample_rate(cls, value: Any) -> int:
        # Environment files supply strings; normalize only the supported value.
        if (type(value) is int and value == 16_000) or (
            isinstance(value, str) and value.strip() == "16000"
        ):
            return 16_000
        raise ValueError(
            "会话音频协议固定为 16000 Hz（16 kHz）；请将 "
            "WHISPER_GUIDE_SPEECH_SAMPLE_RATE 设为 16000。"
            "麦克风可使用设备原生采样率，浏览器会转换为 16 kHz；"
            "不要将后端设置改成设备的 44100/48000 Hz。"
        )

    @field_validator("app_title")
    @classmethod
    def normalize_app_title(cls, value: str) -> str:
        return value.strip() or "whisper-guide"

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

    @property
    def model_settings_path(self) -> Path:
        return self.resolved_data_dir / ".model-settings.json"

    @property
    def model_configs_path(self) -> Path:
        return self.resolved_data_dir / ".model-configs.json"


def clone_model_settings(settings: Settings, updates: dict[str, Any]) -> Settings:
    """Validate a new settings instance without mutating the active instance."""

    # Older persisted files used cloud_* names. Normalize before applying the
    # model-field allowlist; an explicitly supplied canonical value, including
    # an empty string used to clear a field, always wins over its legacy alias.
    updates = dict(updates)
    for suffix in ("endpoint", "app_id", "access_token"):
        canonical, legacy = f"cloud_stt_{suffix}", f"cloud_{suffix}"
        if canonical not in updates and legacy in updates:
            updates[canonical] = updates[legacy]
    values = settings.model_dump()
    values.update({key: value for key, value in updates.items() if key in MODEL_SETTINGS_FIELDS})
    for suffix in ("endpoint", "app_id", "access_token"):
        canonical = f"cloud_stt_{suffix}"
        legacy = f"cloud_{suffix}"
        values[canonical] = (
            updates[canonical]
            if canonical in updates
            else values.get(canonical) or values.get(legacy, "")
        )
        values[legacy] = values[canonical]
    # Opt-in is an explicit precondition for any remote speech source.
    if not values.get("cloud_opt_in") and values.get("stt_provider") != "local":
        values["stt_provider"] = "local"
    return Settings(**values, _env_file=None)


def persist_private_bytes(destination: Path, data: bytes) -> None:
    """Atomically replace an owner-only private settings file."""
    directory = destination.parent
    directory.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise OSError("Runtime model settings must be a regular file.")
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=".model-settings-", suffix=".tmp", dir=directory
        )
        with os.fdopen(descriptor, "wb") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def persist_model_settings(settings: Settings) -> None:
    """Atomically write owner-only model configuration next to the SQLite DB."""
    values = {key: getattr(settings, key) for key in MODEL_SETTINGS_FIELDS}
    persist_private_bytes(
        settings.model_settings_path, json.dumps(values, ensure_ascii=False).encode("utf-8")
    )


def load_model_settings(settings: Settings) -> Settings:
    """Load saved model fields; invalid files leave environment defaults intact."""

    path = settings.model_settings_path
    if not path.is_file() or path.is_symlink():
        return clone_model_settings(settings, {})
    try:
        if path.stat().st_size > 64 * 1024:
            return clone_model_settings(settings, {})
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return clone_model_settings(settings, {})
        return clone_model_settings(settings, payload)
    except (OSError, ValueError, TypeError, ValidationError):
        return clone_model_settings(settings, {})


@lru_cache
def get_settings() -> Settings:
    return Settings()
