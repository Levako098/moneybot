from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import requests

from bot.version import __version__


logger = logging.getLogger("moneybot.updates")
ROOT = Path(__file__).resolve().parents[2]
UPDATE_STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "updates.json"
LATEST_RELEASE_URL = "https://api.github.com/repos/Levako098/moneybot/releases/latest"
CHECK_INTERVAL_SECONDS = 1800


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag: str
    name: str
    url: str
    notes: str


@dataclass(frozen=True)
class UpdateCheckResult:
    current_version: str
    latest: ReleaseInfo | None = None
    update_available: bool = False
    error: str = ""


@dataclass(frozen=True)
class UpdateInstallResult:
    status: str
    version: str = ""
    error: str = ""


def _version_tuple(value: str) -> tuple[int, ...]:
    numbers = [int(item) for item in re.findall(r"\d+", value)[:4]]
    return tuple(numbers + [0] * (4 - len(numbers)))


class UpdateService:
    def __init__(self, path: Path = UPDATE_STATE_PATH) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._install_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._worker_started = False
        self._last_check: UpdateCheckResult | None = None
        self._state = self._load()

    def _load(self) -> dict[str, Any]:
        try:
            with self.path.open("r", encoding="utf-8-sig") as state_file:
                raw = json.load(state_file)
        except FileNotFoundError:
            raw = {}
        except (OSError, json.JSONDecodeError):
            logger.exception("Не удалось прочитать состояние обновлений")
            raw = {}
        state = {
            "last_notified_version": str(raw.get("last_notified_version") or "")
            if isinstance(raw, dict)
            else "",
        }
        self._save(state)
        return state

    def _save(self, state: dict[str, Any] | None = None) -> None:
        current = state if state is not None else self._state
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8") as state_file:
            json.dump(current, state_file, ensure_ascii=False, indent=2)
            state_file.write("\n")
        temporary.replace(self.path)

    def check(self) -> UpdateCheckResult:
        try:
            response = requests.get(
                LATEST_RELEASE_URL,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": f"MoneyBot/{__version__}",
                },
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
            tag = str(payload.get("tag_name") or "").strip()
            if not re.fullmatch(r"[vV]?\d+(?:\.\d+){1,3}", tag):
                raise ValueError("GitHub не вернул корректный тег релиза")
            version = tag.lstrip("vV")
            release = ReleaseInfo(
                version=version,
                tag=tag,
                name=str(payload.get("name") or tag).strip(),
                url=str(payload.get("html_url") or "").strip(),
                notes=str(payload.get("body") or "").strip(),
            )
            result = UpdateCheckResult(
                __version__,
                release,
                _version_tuple(version) > _version_tuple(__version__),
            )
        except Exception as error:
            reason = str(error).splitlines()[0].strip()
            result = UpdateCheckResult(
                __version__, error=reason[:300] if reason else type(error).__name__
            )
            logger.exception("Не удалось проверить обновления")
        with self._lock:
            self._last_check = result
        return result

    def should_notify(self, release: ReleaseInfo) -> bool:
        with self._lock:
            return self._state["last_notified_version"] != release.version

    def mark_notified(self, release: ReleaseInfo) -> None:
        with self._lock:
            self._state["last_notified_version"] = release.version
            self._save()

    def start(self, callback: Callable[[ReleaseInfo], None]) -> None:
        with self._lock:
            if self._worker_started:
                return
            self._worker_started = True

        def worker() -> None:
            while not self._stop_event.is_set():
                result = self.check()
                if (
                    result.update_available
                    and result.latest is not None
                    and self.should_notify(result.latest)
                ):
                    callback(result.latest)
                if self._stop_event.wait(CHECK_INTERVAL_SECONDS):
                    break

        threading.Thread(
            target=worker,
            daemon=True,
            name="moneybot-update-checker",
        ).start()

    @staticmethod
    def _run(command: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )

    def install(self) -> UpdateInstallResult:
        if not self._install_lock.acquire(blocking=False):
            return UpdateInstallResult("busy")
        try:
            if not (ROOT / ".git").is_dir():
                return UpdateInstallResult(
                    "error", error="Обновление доступно только для Git-установки"
                )

            status = self._run(
                ["git", "status", "--porcelain", "--untracked-files=no"]
            )
            if status.returncode != 0:
                return UpdateInstallResult("error", error=self._command_error(status))
            if status.stdout.strip():
                return UpdateInstallResult(
                    "dirty",
                    error="Есть локальные изменения в отслеживаемых файлах",
                )

            branch = self._run(["git", "branch", "--show-current"])
            if branch.returncode != 0 or branch.stdout.strip() != "main":
                return UpdateInstallResult(
                    "error", error="Для автообновления нужна ветка main"
                )

            check = self.check()
            if check.error:
                return UpdateInstallResult("error", error=check.error)
            if not check.update_available or check.latest is None:
                return UpdateInstallResult("current", version=__version__)

            fetch = self._run(["git", "fetch", "--tags", "origin"])
            if fetch.returncode != 0:
                return UpdateInstallResult("error", error=self._command_error(fetch))

            merge = self._run(["git", "merge", "--ff-only", check.latest.tag])
            if merge.returncode != 0:
                return UpdateInstallResult("error", error=self._command_error(merge))

            dependencies = self._run(
                [sys.executable, "-m", "pip", "install", "-r", "bot/requirements.txt"],
                timeout=600,
            )
            if dependencies.returncode != 0:
                return UpdateInstallResult(
                    "error", error="Код обновлён, но зависимости не установлены: " + self._command_error(dependencies)
                )

            version = self._read_installed_version()
            return UpdateInstallResult("installed", version=version)
        except (OSError, subprocess.SubprocessError) as error:
            reason = str(error).splitlines()[0].strip()
            logger.exception("Не удалось установить обновление")
            return UpdateInstallResult(
                "error", error=reason[:300] if reason else type(error).__name__
            )
        finally:
            self._install_lock.release()

    @staticmethod
    def _command_error(result: subprocess.CompletedProcess[str]) -> str:
        text = (result.stderr or result.stdout or "Команда завершилась с ошибкой").strip()
        return text.splitlines()[0][:300]

    @staticmethod
    def _read_installed_version() -> str:
        result = UpdateService._run(
            [sys.executable, "-c", "from bot.version import __version__; print(__version__)"]
        )
        return result.stdout.strip() if result.returncode == 0 else "новая версия"

    @staticmethod
    def schedule_restart() -> None:
        if os.name == "nt":
            root = repr(str(ROOT))
            restart_code = (
                "import subprocess,sys,time; "
                "time.sleep(3); "
                f"subprocess.Popen([sys.executable, '-m', 'bot.main'], cwd={root}, "
                "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
                "stderr=subprocess.DEVNULL, "
                "creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | "
                "subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW, "
                "close_fds=True)"
            )
            subprocess.Popen(
                [sys.executable, "-c", restart_code],
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
                    | subprocess.CREATE_NO_WINDOW
                ),
                close_fds=True,
            )
            return

        if os.environ.get("INVOCATION_ID"):
            return
        subprocess.Popen(
            ["sh", "-c", "sleep 3; exec ./start.sh"],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
