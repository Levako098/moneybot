"""Remind buyers about a review after a period of chat inactivity."""

from __future__ import annotations

import html
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.services.automation import render_template


NAME = "Review Reminder"
VERSION = "1.0.0"
DESCRIPTION = (
    "Напоминает покупателям оставить отзыв после периода бездействия "
    "и отвечает на оставленный отзыв."
)
CREDITS = "MoneyBot"
UUID = "6cb1f1e0-2e10-4a74-9e9c-2e2d5c9cf93b"
SETTINGS_PAGE = True

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "bot" / "data" / "review_reminder.json"
LOGGER = logging.getLogger("moneybot.plugin.review_reminder")
LOCK = threading.RLock()
STOP_EVENT = threading.Event()
WORKER_STARTED = False
CARDINAL: Any = None


def _default() -> dict[str, Any]:
    return {
        "enabled": True,
        "cooldown_minutes": 30,
        "reminder_template": (
            "Здравствуйте, {username}! Если всё хорошо, пожалуйста, "
            "оставьте отзыв по заказу #{order_id}. Это очень помогает магазину."
        ),
        "review_template": "Спасибо за ваш отзыв, {username}! Будем рады видеть вас снова.",
        "orders": {},
    }


def _load() -> dict[str, Any]:
    try:
        raw = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        raw = {}
    data = _default()
    if isinstance(raw, dict):
        data.update(raw)
    data["enabled"] = bool(data.get("enabled", True))
    try:
        data["cooldown_minutes"] = min(max(int(data.get("cooldown_minutes", 30)), 1), 10080)
    except (TypeError, ValueError):
        data["cooldown_minutes"] = 30
    for key in ("reminder_template", "review_template"):
        value = str(data.get(key) or _default()[key]).strip()
        data[key] = value[:3500]
    data["orders"] = data.get("orders") if isinstance(data.get("orders"), dict) else {}
    return data


def _save(data: dict[str, Any]) -> None:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = DATA_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(DATA_PATH)


def _menu(data: dict[str, Any]) -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup()
    keyboard.row(
        InlineKeyboardButton(
            "🔴 Выключить" if data["enabled"] else "🟢 Включить",
            callback_data=f"47:{UUID}:toggle",
        )
    )
    keyboard.row(
        InlineKeyboardButton(
            f"Cooldown: {data['cooldown_minutes']} мин",
            callback_data=f"47:{UUID}:cooldown",
        )
    )
    keyboard.row(
        InlineKeyboardButton("Шаблон напоминания", callback_data=f"47:{UUID}:reminder"),
        InlineKeyboardButton("Ответ на отзыв", callback_data=f"47:{UUID}:review"),
    )
    keyboard.row(InlineKeyboardButton("Закрыть", callback_data=f"47:{UUID}:close"))
    return keyboard


def _menu_text(data: dict[str, Any]) -> str:
    status = "включён" if data["enabled"] else "выключен"
    pending_count = sum(
        1 for item in data["orders"].values() if not item.get("review_replied")
    )
    return (
        "<b>Review Reminder</b>\n\n"
        f"Статус: <b>{status}</b>\n"
        f"Cooldown: <b>{data['cooldown_minutes']} мин.</b>\n"
        f"Ожидают отзыва: <b>{pending_count}</b>\n\n"
        f"Напоминание:\n<code>{html.escape(data['reminder_template'][:500])}</code>\n\n"
        f"Ответ на отзыв:\n<code>{html.escape(data['review_template'][:500])}</code>\n\n"
        "Переменные: <code>{username}</code>, <code>{order_id}</code>, "
        "<code>{stars}</code>, <code>{review}</code>, <code>{lot}</code>, <code>{sum}</code>."
    )


def _show_settings(cardinal: Any, chat_id: int, message_id: int | None = None) -> None:
    with LOCK:
        data = _load()
    LOGGER.info("Review Reminder: settings screen requested, chat_id=%s, message_id=%s", chat_id, message_id)
    kwargs = {"reply_markup": _menu(data), "parse_mode": "HTML"}
    if message_id is None:
        cardinal.telegram.bot.send_message(chat_id, _menu_text(data), **kwargs)
    else:
        cardinal.telegram.bot.edit_message_text(
            _menu_text(data), chat_id, message_id, **kwargs
        )


def _prompt(cardinal: Any, call: Any, state: str, text: str) -> None:
    cardinal.telegram.set_state(
        call.message.chat.id, call.message.id, call.from_user.id, state
    )
    cardinal.telegram.bot.send_message(call.message.chat.id, text)


def _order_id(text: Any) -> str:
    match = re.search(r"#([A-Z0-9]{6,})", str(text or ""), re.IGNORECASE)
    return match.group(1).upper() if match else ""


def _message_type(message: Any) -> str:
    return str(getattr(getattr(message, "type", None), "name", ""))


def _context(order: Any, review: Any = None) -> dict[str, Any]:
    return {
        "username": getattr(order, "buyer_username", None) or "покупатель",
        "order_id": str(getattr(order, "id", "")),
        "stars": getattr(review, "stars", "") if review else "",
        "review": getattr(review, "text", "") if review else "",
        "lot": getattr(order, "title", None) or getattr(order, "short_description", None) or "",
        "sum": getattr(order, "sum", "") or "",
    }


def _on_new_order(cardinal: Any, event: Any) -> None:
    shortcut = getattr(event, "order", None)
    if shortcut is None:
        return
    status = str(getattr(getattr(shortcut, "status", None), "name", ""))
    if status and status not in {"PAID", "CONFIRMED"}:
        return
    order_id = str(getattr(shortcut, "id", "") or "").lstrip("#").upper()
    buyer_id = getattr(shortcut, "buyer_id", None)
    if not order_id or buyer_id is None:
        return
    with LOCK:
        data = _load()
        data["orders"].setdefault(
            order_id,
            {
                "chat_id": buyer_id,
                "username": str(getattr(shortcut, "buyer_username", "") or ""),
                "last_activity": time.time(),
                "review_requested": False,
                "review_replied": False,
            },
        )
        _save(data)


def _on_message(cardinal: Any, event: Any) -> None:
    message = getattr(event, "message", None)
    if message is None:
        return
    message_type = _message_type(message)
    if message_type in {"NEW_FEEDBACK", "FEEDBACK_CHANGED"}:
        order_id = _order_id(getattr(message, "text", ""))
        if order_id:
            _handle_review(cardinal, order_id)
        return
    if message_type not in {"", "NON_SYSTEM", "ORDER_PURCHASED"}:
        return
    chat_id = getattr(message, "chat_id", None)
    with LOCK:
        data = _load()
        changed = False
        for item in data["orders"].values():
            if str(item.get("chat_id")) == str(chat_id):
                item["last_activity"] = time.time()
                item["review_requested"] = False
                changed = True
        if changed:
            _save(data)


def _handle_review(cardinal: Any, order_id: str) -> None:
    with LOCK:
        data = _load()
        item = data["orders"].get(order_id)
    if not item or item.get("review_replied"):
        return
    try:
        order = cardinal.account.get_order(order_id)
        review = getattr(order, "review", None)
        if review is None or getattr(order, "seller_id", None) != getattr(cardinal.account, "id", None):
            return
        with LOCK:
            data = _load()
            template = data["review_template"]
        response = render_template(template, _context(order, review))[:1000]
        if response:
            cardinal.account.send_review(order_id, response, int(getattr(review, "stars", 5) or 5))
        with LOCK:
            data = _load()
            if order_id in data["orders"]:
                data["orders"][order_id]["review_replied"] = True
                _save(data)
        LOGGER.info("Ответ на отзыв отправлен по заказу #%s", order_id)
    except Exception:
        LOGGER.exception("Не удалось ответить на отзыв по заказу #%s", order_id)


def _worker(cardinal: Any) -> None:
    while not STOP_EVENT.wait(5):
        with LOCK:
            data = _load()
        if not data["enabled"]:
            continue
        now = time.time()
        cooldown = data["cooldown_minutes"] * 60
        for order_id, item in list(data["orders"].items()):
            if item.get("review_replied") or now - float(item.get("last_activity", now)) < cooldown:
                continue
            try:
                order = cardinal.account.get_order(order_id)
                if getattr(order, "review", None) is not None:
                    _handle_review(cardinal, order_id)
                    continue
                text = render_template(data["reminder_template"], _context(order))[:4000]
                if not text:
                    continue
                cardinal.account.send_message(item["chat_id"], text)
                with LOCK:
                    current = _load()
                    if order_id in current["orders"]:
                        current["orders"][order_id]["last_activity"] = time.time()
                        current["orders"][order_id]["review_requested"] = True
                        _save(current)
                LOGGER.info("Напоминание об отзыве отправлено по заказу #%s", order_id)
            except Exception:
                LOGGER.exception("Не удалось отправить напоминание по заказу #%s", order_id)
                with LOCK:
                    current = _load()
                    if order_id in current["orders"]:
                        current["orders"][order_id]["last_activity"] = time.time()
                        _save(current)


def _on_command(cardinal: Any, message: Any) -> None:
    LOGGER.info(
        "Review Reminder: command received, chat_id=%s, user_id=%s",
        getattr(getattr(message, "chat", None), "id", None),
        getattr(getattr(message, "from_user", None), "id", None),
    )
    _show_settings(cardinal, message.chat.id)


def _on_telegram_message(cardinal: Any, message: Any) -> None:
    user = getattr(message, "from_user", None)
    if not user:
        return
    state = cardinal.telegram.get_state(message.chat.id, user.id)
    if not state:
        return
    value = str(getattr(message, "text", "") or "").strip()
    mode = state.get("state")
    LOGGER.info(
        "Review Reminder: settings input received, chat_id=%s, user_id=%s, state=%s",
        message.chat.id,
        user.id,
        mode,
    )
    if mode == "rr_cooldown":
        if not value.isdigit() or not 1 <= int(value) <= 10080:
            cardinal.telegram.bot.send_message(message.chat.id, "Укажите число минут от 1 до 10080.")
            return
        with LOCK:
            data = _load()
            data["cooldown_minutes"] = int(value)
            _save(data)
    elif mode in {"rr_reminder", "rr_review"}:
        if not value or len(value) > 3500:
            cardinal.telegram.bot.send_message(message.chat.id, "Шаблон должен содержать от 1 до 3500 символов.")
            return
        with LOCK:
            data = _load()
            data["reminder_template" if mode == "rr_reminder" else "review_template"] = value
            _save(data)
    else:
        return
    cardinal.telegram.clear_state(message.chat.id, user.id)
    _show_settings(cardinal, message.chat.id)


def _on_callback(cardinal: Any, call: Any) -> None:
    if not str(getattr(call, "data", "")).startswith(f"47:{UUID}"):
        return
    parts = str(call.data).split(":")
    action = parts[2] if len(parts) > 2 else ""
    LOGGER.info(
        "Review Reminder: callback received, action=%s, chat_id=%s, user_id=%s",
        action,
        getattr(getattr(getattr(call, "message", None), "chat", None), "id", None),
        getattr(getattr(call, "from_user", None), "id", None),
    )
    cardinal.telegram.bot.answer_callback_query(call.id)
    chat_id = call.message.chat.id
    if action in {"", "0", "main"}:
        _show_settings(cardinal, chat_id, call.message.id)
    elif action == "toggle":
        with LOCK:
            data = _load()
            data["enabled"] = not data["enabled"]
            _save(data)
        _show_settings(cardinal, chat_id, call.message.id)
    elif action == "cooldown":
        _prompt(cardinal, call, "rr_cooldown", "Отправьте cooldown в минутах (1-10080).")
    elif action == "reminder":
        with LOCK:
            current = _load()["reminder_template"]
        _prompt(cardinal, call, "rr_reminder", f"Отправьте новый шаблон напоминания.\n\nТекущий:\n{current}")
    elif action == "review":
        with LOCK:
            current = _load()["review_template"]
        _prompt(cardinal, call, "rr_review", f"Отправьте шаблон ответа на отзыв.\n\nТекущий:\n{current}")
    elif action == "close":
        cardinal.telegram.clear_state(chat_id, call.from_user.id)
        cardinal.telegram.bot.delete_message(chat_id, call.message.id)


def _init(cardinal: Any) -> None:
    global CARDINAL, WORKER_STARTED
    CARDINAL = cardinal
    LOGGER.info("Review Reminder: plugin initialized")
    cardinal.add_telegram_commands(UUID, [("reviewreminder", "Настройки напоминаний об отзывах", True)])
    cardinal.telegram.msg_handler(
        lambda message: _on_command(cardinal, message), commands=["reviewreminder"]
    )
    cardinal.telegram.msg_handler(
        lambda message: _on_telegram_message(cardinal, message), content_types=["text"]
    )
    cardinal.telegram.cbq_handler(
        lambda call: _on_callback(cardinal, call),
        lambda call: str(getattr(call, "data", "")).startswith(f"47:{UUID}"),
    )
    if not WORKER_STARTED:
        WORKER_STARTED = True
        STOP_EVENT.clear()
        threading.Thread(target=_worker, args=(cardinal,), daemon=True, name="review-reminder").start()
        LOGGER.info("Review Reminder: worker started")


def _exit(cardinal: Any) -> None:
    STOP_EVENT.set()


BIND_TO_INIT = [_init]
BIND_TO_NEW_ORDER = [_on_new_order]
BIND_TO_NEW_MESSAGE = [_on_message]
BIND_TO_EXIT = [_exit]
