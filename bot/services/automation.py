from __future__ import annotations

import copy
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any


logger = logging.getLogger("moneybot.automation")

AUTOMATION_PATH = Path(__file__).resolve().parent.parent / "data" / "automation.json"
ORDER_ID_RE = re.compile(r"#([A-Z0-9]{8})", re.IGNORECASE)
VARIABLE_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

DEFAULT_SETTINGS: dict[str, Any] = {
    "messages": {
        "enabled": False,
        "cooldown_seconds": 300,
        "template": "Здравствуйте, {username}! Спасибо за сообщение.",
    },
    "reviews": {
        "enabled": False,
        "templates": {
            "1": "Спасибо за отзыв, {username}. Напишите нам в чат, пожалуйста, чтобы мы могли разобраться.",
            "2": "Спасибо за отзыв, {username}. Напишите нам в чат, пожалуйста, чтобы мы могли разобраться.",
            "3": "Спасибо за отзыв, {username}! Мы учтём ваши замечания.",
            "4": "Спасибо за отзыв, {username}! Рады, что вам понравился заказ.",
            "5": "Спасибо за отзыв, {username}! Будем рады видеть вас снова.",
        },
    },
    "notifications": {
        "enabled": True,
        "incoming_messages": True,
        "outgoing_messages": True,
        "orders": True,
        "reviews": True,
        "refunds": True,
        "other_system": True,
    },
}

MESSAGE_VARIABLES = ("username", "chat_name", "message", "account", "chat_id")
REVIEW_VARIABLES = (
    "username",
    "order_id",
    "lot",
    "description",
    "sum",
    "stars",
    "review",
    "category",
    "game",
    "account",
)
NOTIFICATION_KEYS = (
    "incoming_messages",
    "outgoing_messages",
    "orders",
    "reviews",
    "refunds",
    "other_system",
)


def render_template(template: str, variables: dict[str, Any]) -> str:
    values = {name: str(value if value is not None else "") for name, value in variables.items()}
    return VARIABLE_RE.sub(lambda match: values.get(match.group(1), match.group(0)), template).strip()


def _normalized_settings(data: Any) -> dict[str, Any]:
    result = copy.deepcopy(DEFAULT_SETTINGS)
    if not isinstance(data, dict):
        return result

    messages = data.get("messages")
    if isinstance(messages, dict):
        if isinstance(messages.get("enabled"), bool):
            result["messages"]["enabled"] = messages["enabled"]
        if isinstance(messages.get("template"), str) and messages["template"].strip():
            result["messages"]["template"] = messages["template"].strip()
        cooldown = messages.get("cooldown_seconds")
        if isinstance(cooldown, int) and not isinstance(cooldown, bool):
            result["messages"]["cooldown_seconds"] = min(max(cooldown, 0), 86400)

    reviews = data.get("reviews")
    if isinstance(reviews, dict):
        if isinstance(reviews.get("enabled"), bool):
            result["reviews"]["enabled"] = reviews["enabled"]
        templates = reviews.get("templates")
        if isinstance(templates, dict):
            for stars in range(1, 6):
                value = templates.get(str(stars))
                if isinstance(value, str) and value.strip():
                    result["reviews"]["templates"][str(stars)] = value.strip()

    notifications = data.get("notifications")
    if isinstance(notifications, dict):
        for key in ("enabled", *NOTIFICATION_KEYS):
            if isinstance(notifications.get(key), bool):
                result["notifications"][key] = notifications[key]
    return result


class AutomationService:
    def __init__(self, account: Any, path: Path = AUTOMATION_PATH) -> None:
        self.account = account
        self.path = path
        self._lock = threading.RLock()
        self._message_sent_at: dict[str, float] = {}
        self._review_in_progress: set[str] = set()
        self._processed_reviews: set[str] = set()
        self._settings = self._load()

    def _load(self) -> dict[str, Any]:
        try:
            with self.path.open("r", encoding="utf-8-sig") as settings_file:
                data = json.load(settings_file)
        except FileNotFoundError:
            data = None
        except (OSError, json.JSONDecodeError):
            logger.exception("Не удалось прочитать настройки автоответов, используются значения по умолчанию")
            data = None
        settings = _normalized_settings(data)
        self._save(settings)
        return settings

    def _save(self, settings: dict[str, Any] | None = None) -> None:
        current = settings if settings is not None else self._settings
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(".json.tmp")
        with temporary_path.open("w", encoding="utf-8") as settings_file:
            json.dump(current, settings_file, ensure_ascii=False, indent=2)
            settings_file.write("\n")
        temporary_path.replace(self.path)

    def get_settings(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._settings)

    def toggle_messages(self) -> bool:
        with self._lock:
            enabled = not self._settings["messages"]["enabled"]
            self._settings["messages"]["enabled"] = enabled
            self._save()
            return enabled

    def set_message_template(self, template: str) -> None:
        with self._lock:
            self._settings["messages"]["template"] = template.strip()
            self._save()

    def set_message_cooldown(self, seconds: int) -> None:
        with self._lock:
            self._settings["messages"]["cooldown_seconds"] = seconds
            self._save()

    def toggle_reviews(self) -> bool:
        with self._lock:
            enabled = not self._settings["reviews"]["enabled"]
            self._settings["reviews"]["enabled"] = enabled
            self._save()
            return enabled

    def set_review_template(self, stars: int, template: str) -> None:
        with self._lock:
            self._settings["reviews"]["templates"][str(stars)] = template.strip()
            self._save()

    def toggle_notification(self, key: str) -> bool:
        if key not in {"enabled", *NOTIFICATION_KEYS}:
            raise ValueError(f"Unknown notification setting: {key}")
        with self._lock:
            enabled = not self._settings["notifications"][key]
            self._settings["notifications"][key] = enabled
            self._save()
            return enabled

    def should_notify(self, message: Any) -> bool:
        with self._lock:
            settings = copy.deepcopy(self._settings["notifications"])
        if not settings["enabled"]:
            return False

        message_type = getattr(getattr(message, "type", None), "name", "")
        if message_type in {"", "NON_SYSTEM"}:
            account_id = getattr(self.account, "id", None)
            category = (
                "outgoing_messages"
                if getattr(message, "author_id", None) == account_id
                else "incoming_messages"
            )
        elif message_type in {
            "ORDER_PURCHASED",
            "ORDER_CONFIRMED",
            "ORDER_CONFIRMED_BY_ADMIN",
            "ORDER_REOPENED",
        }:
            category = "orders"
        elif message_type in {
            "NEW_FEEDBACK",
            "FEEDBACK_CHANGED",
            "FEEDBACK_DELETED",
            "NEW_FEEDBACK_ANSWER",
            "FEEDBACK_ANSWER_CHANGED",
            "FEEDBACK_ANSWER_DELETED",
        }:
            category = "reviews"
        elif message_type in {"REFUND", "PARTIAL_REFUND"}:
            category = "refunds"
        else:
            category = "other_system"
        return bool(settings[category])

    def handle_event(self, event: Any) -> str | None:
        message = getattr(event, "message", None)
        if message is None or self.account is None:
            return None
        message_type = getattr(getattr(message, "type", None), "name", "")
        if message_type in {"NEW_FEEDBACK", "FEEDBACK_CHANGED"}:
            return self._handle_review(message)
        if message_type in {"", "NON_SYSTEM"}:
            return self._handle_message(message)
        return None

    def _handle_message(self, message: Any) -> str | None:
        if (
            getattr(message, "author_id", 0) in {0, getattr(self.account, "id", None)}
            or bool(getattr(message, "by_bot", False))
        ):
            return None

        with self._lock:
            settings = copy.deepcopy(self._settings["messages"])
        if not settings["enabled"]:
            return None

        chat_id = str(getattr(message, "chat_id", "") or "")
        if not chat_id:
            return None
        now = time.monotonic()
        cooldown = int(settings["cooldown_seconds"])
        with self._lock:
            previous = self._message_sent_at.get(chat_id)
            if previous is not None and now - previous < cooldown:
                return None
            self._message_sent_at[chat_id] = now

        context = {
            "username": getattr(message, "author", None) or getattr(message, "chat_name", None) or "покупатель",
            "chat_name": getattr(message, "chat_name", None) or "",
            "message": getattr(message, "text", None) or "",
            "account": getattr(self.account, "username", None) or "",
            "chat_id": chat_id,
        }
        response = render_template(str(settings["template"]), context)
        if not response:
            with self._lock:
                if self._message_sent_at.get(chat_id) == now:
                    self._message_sent_at.pop(chat_id, None)
            return None
        response = response[:4000]
        try:
            self.account.send_message(chat_id, response)
        except Exception:
            with self._lock:
                if self._message_sent_at.get(chat_id) == now:
                    self._message_sent_at.pop(chat_id, None)
            logger.exception("Не удалось отправить автоответ в чат FunPay %s", chat_id)
            return None
        logger.info("Автоответ отправлен в чат FunPay %s", chat_id)
        return "message"

    def _handle_review(self, message: Any) -> str | None:
        with self._lock:
            settings = copy.deepcopy(self._settings["reviews"])
        if not settings["enabled"]:
            return None

        match = ORDER_ID_RE.search(str(getattr(message, "text", None) or ""))
        if not match:
            return None
        order_id = match.group(1).upper()
        with self._lock:
            if order_id in self._review_in_progress:
                return None
            self._review_in_progress.add(order_id)

        try:
            order = self.account.get_order(order_id)
            if getattr(order, "seller_id", None) != getattr(self.account, "id", None):
                return None
            review = getattr(order, "review", None)
            if review is None or str(getattr(review, "reply", None) or "").strip():
                return None
            stars = int(getattr(review, "stars", 0) or 0)
            if stars not in range(1, 6):
                return None
            review_signature = f"{order_id}:{stars}:{getattr(review, 'text', None) or ''}"
            with self._lock:
                if review_signature in self._processed_reviews:
                    return None

            subcategory = getattr(order, "subcategory", None)
            category = getattr(subcategory, "category", None)
            context = {
                "username": getattr(order, "buyer_username", None) or getattr(review, "author", None) or "покупатель",
                "order_id": order_id,
                "lot": getattr(order, "title", None) or getattr(order, "short_description", None) or "",
                "description": getattr(order, "full_description", None) or "",
                "sum": getattr(order, "sum", None) or "",
                "stars": stars,
                "review": getattr(review, "text", None) or "",
                "category": getattr(subcategory, "name", None) or "",
                "game": getattr(category, "name", None) or "",
                "account": getattr(self.account, "username", None) or getattr(order, "seller_username", None) or "",
            }
            response = render_template(str(settings["templates"][str(stars)]), context)
            if not response:
                return None
            response = response[:1000]
            self.account.send_review(order_id, response, stars)
            with self._lock:
                self._processed_reviews.add(review_signature)
            logger.info("Автоответ отправлен на отзыв к заказу #%s", order_id)
            return "review"
        except Exception:
            logger.exception("Не удалось обработать отзыв к заказу #%s", order_id)
            return None
        finally:
            with self._lock:
                self._review_in_progress.discard(order_id)
