from __future__ import annotations

import copy
import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator

from bot.services.auto_delivery import AutoDeliveryService
from bot.services.automation import render_template
from bot.services.tickets import TicketClient


logger = logging.getLogger("moneybot.auto_tickets")
AUTO_TICKETS_PATH = Path(__file__).resolve().parent.parent / "data" / "auto_tickets.json"
AUTO_TICKETS_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "moneybot.sqlite3"

DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": False,
    "scope": "all",
    "delay_hours": 24,
    "check_interval_minutes": 60,
    "max_orders_per_ticket": 100,
    "message_template": "Пожалуйста, подтвердите заказы: {order_ids}",
}


@dataclass(frozen=True)
class AutoTicketResult:
    status: str
    order_ids: tuple[str, ...] = ()
    ticket_id: str = ""
    error: str = ""
    confirmation_id: str = ""


class AutoTicketService:
    def __init__(
        self,
        account: Any,
        ticket_client: TicketClient,
        auto_delivery: AutoDeliveryService,
        path: Path = AUTO_TICKETS_PATH,
        db_path: Path = AUTO_TICKETS_DB_PATH,
    ) -> None:
        self.account = account
        self.ticket_client = ticket_client
        self.auto_delivery = auto_delivery
        self.path = path
        self.db_path = db_path
        self._lock = threading.RLock()
        self._check_lock = threading.Lock()
        self._confirmation_lock = threading.Lock()
        self._wake_event = threading.Event()
        self._stop_event = threading.Event()
        self._worker_started = False
        self._result_callback: Callable[[AutoTicketResult], None] | None = None
        self._data = self._load()
        self._init_database()
        self._migrate_json_history()

    def _load(self) -> dict[str, Any]:
        try:
            with self.path.open("r", encoding="utf-8-sig") as data_file:
                raw = json.load(data_file)
        except FileNotFoundError:
            raw = {}
        except (OSError, json.JSONDecodeError):
            logger.exception("Не удалось прочитать настройки автотикетов")
            raw = {}
        settings = copy.deepcopy(DEFAULT_SETTINGS)
        raw_settings = raw.get("settings") if isinstance(raw, dict) else None
        if isinstance(raw_settings, dict):
            if isinstance(raw_settings.get("enabled"), bool):
                settings["enabled"] = raw_settings["enabled"]
            if raw_settings.get("scope") in {"all", "automatic"}:
                settings["scope"] = raw_settings["scope"]
            for key, minimum, maximum in (
                ("delay_hours", 1, 720),
                ("check_interval_minutes", 10, 1440),
                ("max_orders_per_ticket", 1, 100),
            ):
                value = raw_settings.get(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    settings[key] = min(max(value, minimum), maximum)
            template = raw_settings.get("message_template")
            if isinstance(template, str) and "{order_ids}" in template:
                settings["message_template"] = template.strip()
        submitted = raw.get("submitted_orders") if isinstance(raw, dict) else []
        data = {
            "settings": settings,
            "submitted_orders": submitted if isinstance(submitted, list) else [],
            "order_sources": (
                raw.get("order_sources", {}) if isinstance(raw, dict) else {}
            ),
            "source_scan_cursor": (
                raw.get("source_scan_cursor", 0) if isinstance(raw, dict) else 0
            ),
            "last_check_at": raw.get("last_check_at", 0) if isinstance(raw, dict) else 0,
        }
        if not isinstance(data["order_sources"], dict):
            data["order_sources"] = {}
        if not isinstance(data["source_scan_cursor"], int):
            data["source_scan_cursor"] = 0
        self._save(data)
        return data

    def _save(self, data: dict[str, Any] | None = None) -> None:
        current = data if data is not None else self._data
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8") as data_file:
            json.dump(current, data_file, ensure_ascii=False, indent=2)
            data_file.write("\n")
        temporary.replace(self.path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _init_database(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS auto_ticket_batches (
                    id TEXT PRIMARY KEY,
                    order_ids TEXT NOT NULL,
                    message TEXT NOT NULL,
                    status TEXT NOT NULL,
                    ticket_id TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    resolved_at INTEGER
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS auto_ticket_orders (
                    order_id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    ticket_id TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    resolved_at INTEGER
                )
                """
            )
            connection.execute(
                """
                UPDATE auto_ticket_batches
                SET status = 'pending', error = 'Отправка прервана перезапуском'
                WHERE status = 'sending'
                """
            )
            connection.execute(
                """
                UPDATE auto_ticket_orders SET status = 'pending'
                WHERE status = 'sending'
                """
            )

    def _migrate_json_history(self) -> None:
        with self._lock:
            submitted = list(self._data.get("submitted_orders") or [])
        if not submitted:
            return
        now = int(time.time())
        with self._connect() as connection:
            for item in submitted:
                if not isinstance(item, dict) or not item.get("order_id"):
                    continue
                order_id = str(item["order_id"]).lstrip("#").upper()
                batch_id = f"legacy-{order_id}"
                ticket_id = str(item.get("ticket_id") or "")
                created_at = int(item.get("submitted_at") or now)
                connection.execute(
                    """
                    INSERT OR IGNORE INTO auto_ticket_batches
                    (id, order_ids, message, status, ticket_id, created_at, resolved_at)
                    VALUES (?, ?, ?, 'sent', ?, ?, ?)
                    """,
                    (batch_id, json.dumps([order_id]), "Миграция из JSON", ticket_id, created_at, created_at),
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO auto_ticket_orders
                    (order_id, batch_id, status, ticket_id, created_at, resolved_at)
                    VALUES (?, ?, 'sent', ?, ?, ?)
                    """,
                    (order_id, batch_id, ticket_id, created_at, created_at),
                )
        with self._lock:
            self._data["submitted_orders"] = []
            self._save()

    def list_pending(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, order_ids, message, created_at
                FROM auto_ticket_batches
                WHERE status = 'pending'
                ORDER BY created_at ASC
                LIMIT 20
                """
            ).fetchall()
        return [
            {
                "id": str(row["id"]),
                "order_ids": tuple(json.loads(row["order_ids"])),
                "message": str(row["message"]),
                "created_at": int(row["created_at"]),
            }
            for row in rows
        ]

    def get_database_stats(self) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM auto_ticket_batches GROUP BY status"
            ).fetchall()
        stats = {"pending": 0, "sent": 0, "cancelled": 0, "error": 0}
        for row in rows:
            stats[str(row["status"])] = int(row["count"])
        return stats

    def get_settings(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data["settings"])

    def get_last_check_at(self) -> int:
        with self._lock:
            return int(self._data.get("last_check_at") or 0)

    def toggle(self) -> bool:
        with self._lock:
            enabled = not self._data["settings"]["enabled"]
            self._data["settings"]["enabled"] = enabled
            self._save()
        if enabled:
            self._wake_event.set()
        return enabled

    def toggle_scope(self) -> str:
        with self._lock:
            current = self._data["settings"]["scope"]
            scope = "automatic" if current == "all" else "all"
            self._data["settings"]["scope"] = scope
            self._save()
            return scope

    def set_delay_hours(self, hours: int) -> None:
        with self._lock:
            self._data["settings"]["delay_hours"] = min(max(hours, 1), 720)
            self._save()

    def set_interval_minutes(self, minutes: int) -> None:
        with self._lock:
            self._data["settings"]["check_interval_minutes"] = min(
                max(minutes, 10), 1440
            )
            self._save()
        self._wake_event.set()

    def set_max_orders(self, limit: int) -> None:
        with self._lock:
            self._data["settings"]["max_orders_per_ticket"] = min(
                max(limit, 1), 100
            )
            self._save()

    def set_message_template(self, template: str) -> None:
        if "{order_ids}" not in template:
            raise ValueError("шаблон должен содержать {order_ids}")
        with self._lock:
            self._data["settings"]["message_template"] = template.strip()
            self._save()

    def set_result_callback(
        self, callback: Callable[[AutoTicketResult], None] | None
    ) -> None:
        self._result_callback = callback

    def start(self) -> None:
        with self._lock:
            if self._worker_started:
                return
            self._worker_started = True

        def worker() -> None:
            while not self._stop_event.is_set():
                interval = self.get_settings()["check_interval_minutes"] * 60
                self._wake_event.wait(interval)
                self._wake_event.clear()
                if self._stop_event.is_set():
                    break
                if not self.get_settings()["enabled"]:
                    continue
                result = self.run_check()
                if result.status in {"pending", "error"} and self._result_callback:
                    self._result_callback(result)

        threading.Thread(
            target=worker,
            daemon=True,
            name="moneybot-auto-tickets",
        ).start()

    def run_check(self) -> AutoTicketResult:
        if not self.get_settings()["enabled"]:
            return AutoTicketResult("disabled")
        if not self._check_lock.acquire(blocking=False):
            return AutoTicketResult("busy")
        try:
            settings = self.get_settings()
            reserved = self._reserved_ids()
            candidates = []
            start_from = None
            for _ in range(20):
                next_id, orders = self.account.get_sells(
                    start_from=start_from,
                    include_paid=True,
                    include_closed=False,
                    include_refunded=False,
                    state="paid",
                )
                for order in orders:
                    order_id = str(order.id).lstrip("#").upper()
                    if order_id in reserved or not self._old_enough(order, settings):
                        continue
                    candidates.append(order)
                if not next_id:
                    break
                start_from = next_id

            with self._lock:
                self._data["last_check_at"] = int(time.time())
                self._save()
            if not candidates:
                return AutoTicketResult("empty")
            candidates.sort(key=lambda order: order.date)
            if settings["scope"] == "automatic":
                candidates = self._automatic_candidates(candidates)
            if not candidates:
                return AutoTicketResult("empty")
            selected = candidates[: int(settings["max_orders_per_ticket"])]
            order_ids = tuple(str(order.id).lstrip("#").upper() for order in selected)
            rendered_ids = ", ".join(f"#{order_id}" for order_id in order_ids)
            message = render_template(
                settings["message_template"],
                {
                    "order_ids": rendered_ids,
                    "orders_count": len(order_ids),
                    "account": getattr(self.account, "username", None) or "",
                },
            )
            confirmation_id = self._create_pending(order_ids, message)
            logger.info("Создано подтверждение автотикета для %s", ", ".join(order_ids))
            return AutoTicketResult(
                "pending", order_ids, confirmation_id=confirmation_id
            )
        except Exception as error:
            logger.exception("Ошибка автоматической отправки тикета")
            return AutoTicketResult(
                "error", error=str(error).splitlines()[0][:300]
            )
        finally:
            self._check_lock.release()

    def _reserved_ids(self) -> set[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT order_id FROM auto_ticket_orders"
            ).fetchall()
        return {str(row["order_id"]).upper() for row in rows}

    def _create_pending(self, order_ids: tuple[str, ...], message: str) -> str:
        confirmation_id = uuid.uuid4().hex[:16]
        now = int(time.time())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO auto_ticket_batches
                (id, order_ids, message, status, created_at)
                VALUES (?, ?, ?, 'pending', ?)
                """,
                (confirmation_id, json.dumps(order_ids), message, now),
            )
            for order_id in order_ids:
                connection.execute(
                    """
                    INSERT INTO auto_ticket_orders
                    (order_id, batch_id, status, created_at)
                    VALUES (?, ?, 'pending', ?)
                    """,
                    (order_id, confirmation_id, now),
                )
        return confirmation_id

    def confirm(self, confirmation_id: str) -> AutoTicketResult:
        if not self._confirmation_lock.acquire(blocking=False):
            return AutoTicketResult("busy")
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT order_ids, message, status
                    FROM auto_ticket_batches WHERE id = ?
                    """,
                    (confirmation_id,),
                ).fetchone()
                if row is None:
                    return AutoTicketResult("missing")
                order_ids = tuple(json.loads(row["order_ids"]))
                if row["status"] != "pending":
                    return AutoTicketResult(str(row["status"]), order_ids)
                connection.execute(
                    "UPDATE auto_ticket_batches SET status = 'sending' WHERE id = ?",
                    (confirmation_id,),
                )
                connection.execute(
                    "UPDATE auto_ticket_orders SET status = 'sending' WHERE batch_id = ?",
                    (confirmation_id,),
                )

            try:
                ticket_id = self.ticket_client.send_ticket(
                    str(row["message"]),
                    getattr(self.account, "username", None) or "",
                    order_ids[0],
                    "1",
                    "201",
                    "seller",
                )
            except Exception as error:
                reason = str(error).splitlines()[0][:300]
                with self._connect() as connection:
                    connection.execute(
                        """
                        UPDATE auto_ticket_batches
                        SET status = 'pending', error = ? WHERE id = ?
                        """,
                        (reason, confirmation_id),
                    )
                    connection.execute(
                        """
                        UPDATE auto_ticket_orders
                        SET status = 'pending' WHERE batch_id = ?
                        """,
                        (confirmation_id,),
                    )
                logger.exception("Не удалось отправить подтверждённый автотикет")
                return AutoTicketResult(
                    "error",
                    order_ids,
                    error=reason,
                    confirmation_id=confirmation_id,
                )

            now = int(time.time())
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE auto_ticket_batches
                    SET status = 'sent', ticket_id = ?, error = '', resolved_at = ?
                    WHERE id = ?
                    """,
                    (ticket_id, now, confirmation_id),
                )
                connection.execute(
                    """
                    UPDATE auto_ticket_orders
                    SET status = 'sent', ticket_id = ?, resolved_at = ?
                    WHERE batch_id = ?
                    """,
                    (ticket_id, now, confirmation_id),
                )
            logger.info("Подтверждённый автотикет отправлен для %s", ", ".join(order_ids))
            return AutoTicketResult("sent", order_ids, ticket_id=ticket_id)
        finally:
            self._confirmation_lock.release()

    def cancel(self, confirmation_id: str) -> AutoTicketResult:
        now = int(time.time())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT order_ids, status FROM auto_ticket_batches WHERE id = ?",
                (confirmation_id,),
            ).fetchone()
            if row is None:
                return AutoTicketResult("missing")
            order_ids = tuple(json.loads(row["order_ids"]))
            if row["status"] != "pending":
                return AutoTicketResult(str(row["status"]), order_ids)
            connection.execute(
                """
                UPDATE auto_ticket_batches
                SET status = 'cancelled', resolved_at = ? WHERE id = ?
                """,
                (now, confirmation_id),
            )
            connection.execute(
                """
                UPDATE auto_ticket_orders
                SET status = 'cancelled', resolved_at = ? WHERE batch_id = ?
                """,
                (now, confirmation_id),
            )
        logger.info("Автотикет отменён для %s", ", ".join(order_ids))
        return AutoTicketResult("cancelled", order_ids)

    @staticmethod
    def _old_enough(order: Any, settings: dict[str, Any]) -> bool:
        created = getattr(order, "date", None)
        if not isinstance(created, datetime):
            return False
        now = datetime.now(created.tzinfo) if created.tzinfo else datetime.now()
        return (now - created).total_seconds() >= settings["delay_hours"] * 3600

    def _automatic_candidates(self, orders: list[Any]) -> list[Any]:
        bot_delivered = self.auto_delivery.get_delivered_order_ids()
        with self._lock:
            sources = dict(self._data["order_sources"])
            cursor = int(self._data.get("source_scan_cursor") or 0)
        automatic = []
        unknown = []
        for order in orders:
            order_id = str(getattr(order, "id", "") or "").lstrip("#").upper()
            if order_id in bot_delivered or sources.get(order_id) == "funpay":
                automatic.append(order)
            elif order_id not in sources:
                unknown.append(order)

        if unknown:
            cursor %= len(unknown)
            scan_count = min(25, len(unknown))
            scan_orders = [unknown[(cursor + index) % len(unknown)] for index in range(scan_count)]
            for order in scan_orders:
                order_id = str(getattr(order, "id", "") or "").lstrip("#").upper()
                source = "funpay" if self._detect_funpay_delivery(order_id) else "manual"
                sources[order_id] = source
                if source == "funpay":
                    automatic.append(order)
            cursor = (cursor + scan_count) % len(unknown)
            with self._lock:
                self._data["order_sources"] = sources
                self._data["source_scan_cursor"] = cursor
                self._save()
        return sorted(automatic, key=lambda order: order.date)

    def _detect_funpay_delivery(self, order_id: str) -> bool:
        try:
            full_order = self.account.get_order(order_id)
        except Exception:
            logger.exception("Не удалось определить источник выдачи заказа #%s", order_id)
            return False
        html_text = re.sub(r"\s+", " ", str(getattr(full_order, "html", ""))).casefold()
        return bool(
            re.search(r"автовыдач|автоматическ\w*\s+выдач", html_text)
        )
