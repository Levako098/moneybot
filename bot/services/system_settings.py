from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any


logger = logging.getLogger("moneybot.system")
ROOT = Path(__file__).resolve().parents[2]
SYSTEM_SETTINGS_PATH = Path(__file__).resolve().parent.parent / "data" / "system.json"
DEFAULT_SETTINGS = {
    "logs_enabled": True,
    "max_log_size_mb": 10,
}


@dataclass(frozen=True)
class CleanupResult:
    files_cleaned: int
    bytes_freed: int
    files_failed: int


class SystemSettingsService:
    def __init__(self, path: Path = SYSTEM_SETTINGS_PATH) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._worker_started = False
        self._stop_event = threading.Event()
        self._settings = self._load()

    def _load(self) -> dict[str, Any]:
        try:
            with self.path.open("r", encoding="utf-8-sig") as settings_file:
                raw = json.load(settings_file)
        except FileNotFoundError:
            raw = {}
        except (OSError, json.JSONDecodeError):
            logger.exception("Не удалось прочитать системные настройки")
            raw = {}
        settings = dict(DEFAULT_SETTINGS)
        if isinstance(raw, dict):
            if isinstance(raw.get("logs_enabled"), bool):
                settings["logs_enabled"] = raw["logs_enabled"]
            limit = raw.get("max_log_size_mb")
            if isinstance(limit, int) and not isinstance(limit, bool):
                settings["max_log_size_mb"] = min(max(limit, 1), 1024)
        self._save(settings)
        return settings

    def _save(self, settings: dict[str, Any] | None = None) -> None:
        current = settings if settings is not None else self._settings
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8") as settings_file:
            json.dump(current, settings_file, ensure_ascii=False, indent=2)
            settings_file.write("\n")
        temporary.replace(self.path)

    def get_settings(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._settings)

    def toggle_logs(self) -> bool:
        with self._lock:
            enabled = not self._settings["logs_enabled"]
            self._settings["logs_enabled"] = enabled
            self._save()
        self.apply_logging()
        return enabled

    def set_max_log_size(self, megabytes: int) -> None:
        with self._lock:
            self._settings["max_log_size_mb"] = min(max(megabytes, 1), 1024)
            self._save()

    def apply_logging(self) -> None:
        enabled = bool(self.get_settings()["logs_enabled"])
        logging.disable(logging.NOTSET if enabled else logging.CRITICAL)

    def cleanup_logs(self, force: bool = False) -> CleanupResult:
        limit = int(self.get_settings()["max_log_size_mb"]) * 1024 * 1024
        cleaned = 0
        freed = 0
        failed = 0
        for path in self._log_files():
            try:
                size = path.stat().st_size
                if size == 0 or (not force and size <= limit):
                    continue
                with path.open("w", encoding="utf-8"):
                    pass
                cleaned += 1
                freed += size
            except OSError:
                failed += 1
                logger.exception("Не удалось очистить лог %s", path)
        return CleanupResult(cleaned, freed, failed)

    def get_log_files(self) -> list[Path]:
        return self._log_files()

    def _log_files(self) -> list[Path]:
        result = set()
        for directory in (ROOT / "bot", ROOT / "plugins", ROOT / "data"):
            if not directory.exists():
                continue
            for path in directory.rglob("*.log"):
                if ".venv" not in path.parts and path.is_file():
                    result.add(path)
        return sorted(result)

    def start_cleanup_worker(self) -> None:
        with self._lock:
            if self._worker_started:
                return
            self._worker_started = True

        def worker() -> None:
            while not self._stop_event.wait(3600):
                self.cleanup_logs()

        threading.Thread(
            target=worker,
            daemon=True,
            name="moneybot-log-cleanup",
        ).start()
