"""Auto-delivery plugin for ChatGPT accounts sold through one FunPay lot."""

from __future__ import annotations

import html
import json
import logging
import re
import threading
from pathlib import Path
from typing import Any

from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup


NAME = "ChatGPT Accounts Auto Delivery"
VERSION = "1.0.0"
DESCRIPTION = (
    "Автоматически выдаёт аккаунты ChatGPT из очереди после оплаты заказа "
    "по выбранному lot ID. Аккаунты можно загрузить документом или текстом."
)
CREDITS = "MoneyBot"
UUID = "f3bcf0b5-3bc3-4a4e-b8e0-6a5d9f05ac71"
SETTINGS_PAGE = True

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "bot" / "data" / "chatgpt_accounts.json"
LOGGER = logging.getLogger("moneybot.plugin.chatgpt_accounts")
LOCK = threading.RLock()
PROCESSING: set[str] = set()


def _default() -> dict[str, Any]:
    return {
        "enabled": True,
        "lot_id": "",
        "accounts": [],
        "delivered_orders": [],
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
    data["lot_id"] = str(data.get("lot_id") or "").strip()
    data["accounts"] = [str(item).strip() for item in data.get("accounts", []) if str(item).strip()]
    data["delivered_orders"] = [str(item).upper() for item in data.get("delivered_orders", [])]
    return data


def _save(data: dict[str, Any]) -> None:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = DATA_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(DATA_PATH)


def _menu(data: dict[str, Any]) -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup()
    keyboard.row(InlineKeyboardButton("Указать lot ID", callback_data=f"47:{UUID}:set_lot"))
    keyboard.row(InlineKeyboardButton("Загрузить аккаунты", callback_data=f"47:{UUID}:upload"))
    keyboard.row(
        InlineKeyboardButton(
            "🔴 Автовыдача выключена" if not data["enabled"] else "🟢 Автовыдача включена",
            callback_data=f"47:{UUID}:toggle",
        )
    )
    keyboard.row(InlineKeyboardButton("Очистить очередь", callback_data=f"47:{UUID}:clear"))
    keyboard.row(InlineKeyboardButton("Закрыть", callback_data=f"47:{UUID}:close"))
    return keyboard


def _menu_text(data: dict[str, Any]) -> str:
    lot_id = data["lot_id"] or "не указан"
    status = "включена" if data["enabled"] else "выключена"
    return (
        "<b>ChatGPT Accounts Auto Delivery</b>\n\n"
        f"Lot ID: <code>{html.escape(lot_id)}</code>\n"
        f"Аккаунтов в очереди: <b>{len(data['accounts'])}</b>\n"
        f"Автовыдача: <b>{status}</b>\n\n"
        "Один аккаунт = одна непустая строка файла."
    )


def _show_settings(cardinal: Any, chat_id: int, message_id: int | None = None) -> None:
    with LOCK:
        data = _load()
    kwargs = {"reply_markup": _menu(data), "parse_mode": "HTML"}
    if message_id is None:
        cardinal.telegram.bot.send_message(chat_id, _menu_text(data), **kwargs)
    else:
        cardinal.telegram.bot.edit_message_text(
            _menu_text(data), chat_id, message_id, **kwargs
        )


def _prompt(cardinal: Any, call: Any, state: str, text: str) -> None:
    message = call.message
    cardinal.telegram.set_state(
        message.chat.id, message.id, call.from_user.id, state
    )
    cardinal.telegram.bot.edit_message_text(
        text,
        message.chat.id,
        message.id,
        reply_markup=InlineKeyboardMarkup().add(
            InlineKeyboardButton("Отмена", callback_data=f"47:{UUID}:back")
        ),
    )


def _parse_accounts(text: str) -> list[str]:
    return [line.strip() for line in str(text).splitlines() if line.strip()]


def _lot_id(order: Any) -> str:
    direct = getattr(order, "lot_id", None) or getattr(order, "offer_id", None)
    if direct:
        return str(direct)
    html_text = str(getattr(order, "html", "") or "")
    for pattern in (
        r"data-offer=[\"'](\d+)[\"']",
        r"lots/offer\?id=(\d+)",
        r"offer_id[\"']?\s*[:=]\s*[\"']?(\d+)",
    ):
        match = re.search(pattern, html_text, re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def _send_accounts(cardinal: Any, order: Any, shortcut: Any, accounts: list[str]) -> None:
    buyer = str(getattr(order, "buyer_username", "") or getattr(shortcut, "buyer_username", ""))
    if not buyer:
        raise RuntimeError("не удалось определить покупателя")
    chat = cardinal.account.get_chat_by_name(buyer, make_request=True)
    if chat is None:
        raise RuntimeError("чат покупателя не найден")
    payload = "Спасибо за покупку!\n\nВаши аккаунты ChatGPT:\n\n" + "\n\n".join(accounts)
    cardinal.account.send_message(chat.id, payload[:4000], chat_name=buyer)


def _on_order(cardinal: Any, event: Any) -> None:
    if type(event).__name__ != "NewOrderEvent" or cardinal.account is None:
        return
    with LOCK:
        data = _load()
    if not data["enabled"] or not data["lot_id"]:
        return
    shortcut = getattr(event, "order", None)
    if shortcut is None:
        return
    status = str(getattr(getattr(shortcut, "status", None), "name", ""))
    if status and status != "PAID":
        return
    order_id = str(getattr(shortcut, "id", "") or "").lstrip("#").upper()
    if not order_id or order_id in data["delivered_orders"] or order_id in PROCESSING:
        return
    PROCESSING.add(order_id)
    try:
        order = cardinal.account.get_order(order_id)
        if str(_lot_id(order)) != data["lot_id"]:
            return
        amount = max(1, int(getattr(shortcut, "amount", None) or 1))
        with LOCK:
            data = _load()
            if order_id in data["delivered_orders"]:
                return
            if len(data["accounts"]) < amount:
                cardinal.telegram.send_notification(
                    f"⚠️ Не хватает ChatGPT аккаунтов для заказа #{order_id}: "
                    f"нужно {amount}, доступно {len(data['accounts'])}"
                )
                return
            selected = data["accounts"][:amount]
            del data["accounts"][:amount]
            _save(data)
        _send_accounts(cardinal, order, shortcut, selected)
        with LOCK:
            data = _load()
            data["delivered_orders"] = (data["delivered_orders"] + [order_id])[-1000:]
            _save(data)
        cardinal.telegram.send_notification(f"✅ ChatGPT аккаунт выдан по заказу #{order_id}")
    except Exception as error:
        with LOCK:
            data = _load()
            data["accounts"] = selected + data["accounts"] if "selected" in locals() else data["accounts"]
            _save(data)
        LOGGER.exception("Ошибка выдачи ChatGPT аккаунта для заказа #%s", order_id)
        cardinal.telegram.send_notification(f"❌ Ошибка выдачи ChatGPT #{order_id}: {str(error)[:250]}")
    finally:
        PROCESSING.discard(order_id)


def _on_command(cardinal: Any, message: Any) -> None:
    _show_settings(cardinal, message.chat.id)


def _on_message(cardinal: Any, message: Any) -> None:
    if not getattr(message, "from_user", None):
        return
    state = cardinal.telegram.get_state(message.chat.id, message.from_user.id)
    if not state:
        return
    mode = state.get("state")
    if mode == "cg_lot":
        value = str(getattr(message, "text", "") or "").strip()
        if not value.isdigit():
            cardinal.telegram.bot.send_message(message.chat.id, "Отправьте числовой lot ID.")
            return
        with LOCK:
            data = _load()
            data["lot_id"] = value
            _save(data)
        cardinal.telegram.clear_state(message.chat.id, message.from_user.id)
        _show_settings(cardinal, message.chat.id)
    elif mode == "cg_accounts":
        text = str(getattr(message, "text", "") or "")
        if getattr(message, "document", None) is not None:
            try:
                info = cardinal.telegram.bot.get_file(message.document.file_id)
                text = cardinal.telegram.bot.download_file(info.file_path).decode("utf-8-sig")
            except Exception as error:
                cardinal.telegram.bot.send_message(message.chat.id, f"Не удалось прочитать файл: {error}")
                return
        accounts = _parse_accounts(text)
        if not accounts:
            cardinal.telegram.bot.send_message(message.chat.id, "Файл или сообщение не содержит аккаунтов.")
            return
        with LOCK:
            data = _load()
            data["accounts"].extend(accounts)
            _save(data)
        cardinal.telegram.clear_state(message.chat.id, message.from_user.id)
        cardinal.telegram.bot.send_message(message.chat.id, f"Добавлено аккаунтов: {len(accounts)}")
        _show_settings(cardinal, message.chat.id)


def _on_callback(cardinal: Any, call: Any) -> None:
    if not str(getattr(call, "data", "")).startswith(f"47:{UUID}"):
        return
    action = str(call.data).split(":")[2] if len(str(call.data).split(":")) > 2 else ""
    cardinal.telegram.bot.answer_callback_query(call.id)
    if action in {"", "0", "main"}:
        _show_settings(cardinal, call.message.chat.id, call.message.id)
    elif action == "set_lot":
        _prompt(cardinal, call, "cg_lot", "Отправьте lot ID числом одним сообщением.")
    elif action == "upload":
        _prompt(cardinal, call, "cg_accounts", "Отправьте .txt/.csv документ или аккаунты текстом, по одному в строке.")
    elif action == "toggle":
        with LOCK:
            data = _load()
            data["enabled"] = not data["enabled"]
            _save(data)
        _show_settings(cardinal, call.message.chat.id, call.message.id)
    elif action == "clear":
        with LOCK:
            data = _load()
            data["accounts"] = []
            _save(data)
        _show_settings(cardinal, call.message.chat.id, call.message.id)
    elif action == "back":
        cardinal.telegram.clear_state(call.message.chat.id, call.from_user.id)
        _show_settings(cardinal, call.message.chat.id, call.message.id)
    elif action == "close":
        cardinal.telegram.clear_state(call.message.chat.id, call.from_user.id)
        cardinal.telegram.bot.delete_message(call.message.chat.id, call.message.id)


def _init(cardinal: Any) -> None:
    cardinal.add_telegram_commands(UUID, [("chatgpt", "Настройки авто-выдачи ChatGPT", True)])
    cardinal.telegram.msg_handler(_on_command, commands=["chatgpt"])
    cardinal.telegram.msg_handler(_on_message, content_types=["text", "document"])
    cardinal.telegram.cbq_handler(
        _on_callback, lambda call: str(getattr(call, "data", "")).startswith(f"47:{UUID}")
    )


BIND_TO_INIT = [_init]
BIND_TO_NEW_ORDER = [_on_order]
