"""Private, versioned model profiles; saving a draft never activates it."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.config import MODEL_SETTINGS_FIELDS, Settings, clone_model_settings

DEFAULT_PROFILE_ID = "local-default"
MAX_PROFILES = 50


class ModelConfigError(ValueError):
    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def model_values(settings: Settings) -> dict[str, Any]:
    return {key: getattr(settings, key) for key in MODEL_SETTINGS_FIELDS}


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


class ModelConfigRepository:
    """One process owns this repository and the shared activation/session lock."""

    def __init__(self, settings: Settings) -> None:
        self.base_settings = settings
        self.path = settings.model_configs_path
        self.lock = threading.RLock()
        self.load_failed = False
        current = model_values(clone_model_settings(settings, {}))
        local = model_values(
            clone_model_settings(
                settings,
                {
                    "stt_provider": "local",
                    "cloud_opt_in": False,
                    "cloud_stt_endpoint": "",
                    "cloud_stt_app_id": "",
                    "cloud_stt_access_token": "",
                    "llm_align_mode": "off",
                    "llm_provider_preset": "custom",
                    "llm_base_url": "",
                    "llm_model": "",
                    "llm_api_key": "",
                },
            )
        )
        self._data = {
            "schema_version": 1,
            "configs": [
                self._new_profile(DEFAULT_PROFILE_ID, "默认本地离线模式", "", local, True),
                self._new_profile("existing-config", "现有配置", "由原配置迁移", current),
            ],
            "active": {
                "id": "existing-config",
                "name": "现有配置",
                "revision": 1,
                "settings": current,
            },
        }
        if self.path.exists() or self.path.is_symlink():
            try:
                if self.path.is_symlink() or self.path.stat().st_size > 512 * 1024:
                    raise ValueError("Invalid model configuration file")
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                self._validate(loaded)
                self._data = loaded
            except (OSError, ValueError, TypeError, KeyError):
                # Never overwrite a broken credential store as an incidental save.
                self.load_failed = True

    @staticmethod
    def _new_profile(
        identifier: str,
        name: str,
        description: str,
        values: dict[str, Any],
        is_default: bool = False,
    ) -> dict[str, Any]:
        timestamp = _timestamp()
        return {
            "id": identifier,
            "name": name,
            "description": description,
            "revision": 1,
            "created_at": timestamp,
            "updated_at": timestamp,
            "is_default": is_default,
            "settings": deepcopy(values),
        }

    def _validate(self, data: Any) -> None:
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ValueError("Unsupported model configuration schema")
        profiles = data["configs"]
        if not isinstance(profiles, list) or not 1 <= len(profiles) <= MAX_PROFILES:
            raise ValueError("Invalid model profiles")
        identifiers: set[str] = set()
        for item in [*profiles, data["active"]]:
            if not isinstance(item, dict) or not isinstance(item["id"], str):
                raise ValueError("Invalid model profile")
            if type(item["revision"]) is not int or item["revision"] < 1:
                raise ValueError("Invalid model revision")
            if not isinstance(item["name"], str) or not item["name"].strip():
                raise ValueError("Invalid model name")
            if not isinstance(item["settings"], dict):
                raise ValueError("Invalid model settings")
            item["settings"] = model_values(
                clone_model_settings(self.base_settings, item["settings"])
            )
        for item in profiles:
            if (
                not isinstance(item.get("description"), str)
                or not isinstance(item.get("created_at"), str)
                or not isinstance(item.get("updated_at"), str)
                or type(item.get("is_default")) is not bool
            ):
                raise ValueError("Invalid profile metadata")
            if item["id"] in identifiers:
                raise ValueError("Duplicate model profile")
            identifiers.add(item["id"])
        if DEFAULT_PROFILE_ID not in identifiers or data["active"]["id"] not in identifiers:
            raise ValueError("Invalid active/default profile")
        active_profile = self._find(data, data["active"]["id"])
        if data["active"]["revision"] > active_profile["revision"]:
            raise ValueError("Invalid active revision")

    def _save(self, candidate: dict[str, Any]) -> None:
        if self.load_failed or self.path.is_symlink():
            raise OSError("Model configuration store needs repair")
        serialized = json.dumps(candidate, ensure_ascii=False)
        if len(serialized.encode("utf-8")) > 512 * 1024:
            raise ModelConfigError("PROFILE_STORE_LIMIT", "配置内容超过 512 KiB 上限。")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: str | None = None
        try:
            descriptor, temporary = tempfile.mkstemp(
                prefix=".model-configs-", suffix=".tmp", dir=self.path.parent
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                os.fchmod(output.fileno(), 0o600)
                output.write(serialized)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            temporary = None
            self.path.chmod(0o600)
        finally:
            if temporary is not None:
                os.unlink(temporary)
        self._data = candidate

    def active_snapshot(self) -> dict[str, Any]:
        with self.lock:
            return deepcopy(self._data["active"])

    def list(self) -> list[dict[str, Any]]:
        with self.lock:
            return deepcopy(self._data["configs"])

    def get(self, identifier: str) -> dict[str, Any]:
        with self.lock:
            return deepcopy(self._find(self._data, identifier))

    @staticmethod
    def _find(data: dict[str, Any], identifier: str) -> dict[str, Any]:
        for profile in data["configs"]:
            if profile["id"] == identifier:
                return profile
        raise ModelConfigError("PROFILE_NOT_FOUND", "配置不存在。", 404)

    @staticmethod
    def _check_revision(profile: dict[str, Any], revision: int | None) -> None:
        if revision is not None and revision != profile["revision"]:
            raise ModelConfigError("REVISION_CONFLICT", "配置已被其他页面修改，请刷新后重试。")

    def create(self, name: str, description: str, settings: Settings) -> dict[str, Any]:
        with self.lock:
            candidate = deepcopy(self._data)
            if len(candidate["configs"]) >= MAX_PROFILES:
                raise ModelConfigError("PROFILE_LIMIT", "最多保存 50 套配置。")
            profile = self._new_profile(str(uuid4()), name, description, model_values(settings))
            candidate["configs"].append(profile)
            self._save(candidate)
            return deepcopy(profile)

    def update(
        self,
        identifier: str,
        name: str | None,
        description: str | None,
        settings: Settings,
        revision: int | None,
    ) -> dict[str, Any]:
        with self.lock:
            candidate = deepcopy(self._data)
            profile = self._find(candidate, identifier)
            self._check_revision(profile, revision)
            if profile["id"] == DEFAULT_PROFILE_ID:
                raise ModelConfigError("PROFILE_PROTECTED", "默认本地配置不可修改，请复制后编辑。")
            if name is not None:
                profile["name"] = name
            if description is not None:
                profile["description"] = description
            profile.update(
                settings=model_values(settings),
                revision=profile["revision"] + 1,
                updated_at=_timestamp(),
            )
            self._save(candidate)
            return deepcopy(profile)

    def duplicate(self, identifier: str, name: str | None = None) -> dict[str, Any]:
        with self.lock:
            original = self.get(identifier)
            values = {**original["settings"], "llm_api_key": "", "cloud_stt_access_token": ""}
            return self.create(
                name or f"{original['name'][:74]}（副本）",
                original["description"],
                clone_model_settings(self.base_settings, values),
            )

    def delete(self, identifier: str) -> None:
        with self.lock:
            profile = self.get(identifier)
            if profile["id"] in {DEFAULT_PROFILE_ID, self._data["active"]["id"]}:
                raise ModelConfigError("PROFILE_PROTECTED", "不能删除默认配置或当前生效配置。")
            candidate = deepcopy(self._data)
            candidate["configs"] = [p for p in candidate["configs"] if p["id"] != identifier]
            self._save(candidate)

    def activate(self, identifier: str, revision: int | None = None) -> dict[str, Any]:
        with self.lock:
            profile = self.get(identifier)
            self._check_revision(profile, revision)
            candidate = deepcopy(self._data)
            candidate["active"] = {
                key: deepcopy(profile[key]) for key in ("id", "name", "revision", "settings")
            }
            self._save(candidate)
            return self.active_snapshot()

    def legacy_update(self, settings: Settings) -> dict[str, Any]:
        """The old PUT endpoint explicitly saves and activates one revision."""
        with self.lock:
            candidate = deepcopy(self._data)
            identifier = candidate["active"]["id"]
            if identifier == DEFAULT_PROFILE_ID:
                if len(candidate["configs"]) >= MAX_PROFILES:
                    raise ModelConfigError("PROFILE_LIMIT", "最多保存 50 套配置。")
                identifier = str(uuid4())
                candidate["configs"].append(
                    self._new_profile(identifier, "兼容接口配置", "", model_values(settings))
                )
            profile = self._find(candidate, identifier)
            profile.update(
                settings=model_values(settings),
                revision=profile["revision"] + 1,
                updated_at=_timestamp(),
            )
            candidate["active"] = {
                key: deepcopy(profile[key]) for key in ("id", "name", "revision", "settings")
            }
            self._save(candidate)
            return self.active_snapshot()
