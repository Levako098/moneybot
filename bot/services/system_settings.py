from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import psutil


logger = logging.getLogger("moneybot.system")
ROOT = Path(__file__).resolve().parents[2]
SYSTEM_SETTINGS_PATH = Path(__file__).resolve().parent.parent / "data" / "system.json"
DEFAULT_SETTINGS = {
    "logs_enabled": True,
    "max_log_size_mb": 10,
}
RESOURCE_CHECK_INTERVAL_SECONDS = 300
RESOURCE_WARNING_PERCENT = 90.0
CACHE_DIRECTORY_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
EXCLUDED_DIRECTORY_NAMES = {".git", ".venv", "venv"}


@dataclass(frozen=True)
class CleanupResult:
    files_cleaned: int
    bytes_freed: int
    files_failed: int


@dataclass(frozen=True)
class TemporaryCleanupResult:
    cache_directories_cleaned: int
    cache_files_cleaned: int
    log_files_cleaned: int
    bytes_freed: int
    files_failed: int


@dataclass(frozen=True)
class ResourceWarning:
    memory_percent: float
    memory_available: int
    disk_percent: float
    disk_free: int
    disk_path: str
    reasons: tuple[str, ...]


class SystemSettingsService:
    def __init__(self, path: Path = SYSTEM_SETTINGS_PATH) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._worker_started = False
        self._resource_worker_started = False
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

    def cleanup_temporary_files(self) -> TemporaryCleanupResult:
        cache_directories = 0
        cache_files = 0
        freed = 0
        failed = 0

        for current, directories, _ in os.walk(ROOT, topdown=True):
            directories[:] = [
                name for name in directories if name not in EXCLUDED_DIRECTORY_NAMES
            ]
            for name in list(directories):
                if name not in CACHE_DIRECTORY_NAMES:
                    continue
                path = Path(current) / name
                try:
                    files = [item for item in path.rglob("*") if item.is_file()]
                    size = sum(item.stat().st_size for item in files)
                    if path.is_symlink():
                        path.unlink()
                    else:
                        shutil.rmtree(path)
                    directories.remove(name)
                    cache_directories += 1
                    cache_files += len(files)
                    freed += size
                except OSError:
                    failed += 1
                    logger.exception("Не удалось удалить временный каталог %s", path)

        log_result = self.cleanup_logs(force=True)
        return TemporaryCleanupResult(
            cache_directories,
            cache_files,
            log_result.files_cleaned,
            freed + log_result.bytes_freed,
            failed + log_result.files_failed,
        )

    @staticmethod
    def get_resource_warning() -> ResourceWarning | None:
        memory = psutil.virtual_memory()
        disk_path = ROOT.anchor or "/"
        disk = psutil.disk_usage(disk_path)
        reasons = []
        if float(memory.percent) >= RESOURCE_WARNING_PERCENT:
            reasons.append("оперативная память")
        if float(disk.percent) >= RESOURCE_WARNING_PERCENT:
            reasons.append("диск")
        if not reasons:
            return None
        return ResourceWarning(
            float(memory.percent),
            int(memory.available),
            float(disk.percent),
            int(disk.free),
            str(disk_path),
            tuple(reasons),
        )

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

    def start_resource_monitor(
        self, callback: Callable[[ResourceWarning], None]
    ) -> None:
        with self._lock:
            if self._resource_worker_started:
                return
            self._resource_worker_started = True

        def worker() -> None:
            warning_active = False
            while not self._stop_event.is_set():
                try:
                    warning = self.get_resource_warning()
                    if warning is None:
                        warning_active = False
                    elif not warning_active:
                        callback(warning)
                        warning_active = True
                except Exception:
                    logger.exception("Не удалось проверить ресурсы системы")
                if self._stop_event.wait(RESOURCE_CHECK_INTERVAL_SECONDS):
                    break

        threading.Thread(
            target=worker,
            daemon=True,
            name="moneybot-resource-monitor",
        ).start()
