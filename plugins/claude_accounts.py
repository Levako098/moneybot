"""Claude account auto-delivery with a manually approved review bonus."""

from __future__ import annotations

import html
import json
import logging
import re
import threading
from pathlib import Path
from typing import Any

from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

NAME = "Claude Accounts Auto Delivery"
VERSION = "1.0.0"
DESCRIPTION = "Автовыдача Claude-аккаунтов и ручная выдача одного бонуса за отзыв."
CREDITS = "MoneyBot"
UUID = "a9f95d3e-514f-4a96-8d12-4c13d2fd0b9c"
SETTINGS_PAGE = True

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "bot" / "data" / "claude_accounts.json"
LOCK = threading.RLock()
LOG = logging.getLogger("moneybot.plugin.claude_accounts")
PROCESSING: set[str] = set()


def _default() -> dict[str, Any]:
    return {
        "enabled": True,
        "lot_id": "",
        "lot_title": "",
        "template": "Спасибо за покупку!\n\n{account}",
        "accounts": [],
        "delivered_orders": [],
        "pending_bonuses": {},
        "granted_bonuses": [],
        "seen_reviews": [],
    }


def _load() -> dict[str, Any]:
    try:
        raw = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        raw = {}
    data = _default()
    if isinstance(raw, dict):
        data.update(raw)
    data["accounts"] = [x for x in data.get("accounts", []) if isinstance(x, dict) and x.get("raw")]
    for key in ("delivered_orders", "granted_bonuses", "seen_reviews"):
        data[key] = [str(x).upper() for x in data.get(key, [])]
    data["pending_bonuses"] = dict(data.get("pending_bonuses", {}))
    return data


def _save(data: dict[str, Any]) -> None:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(DATA_PATH)


def _keyboard(data: dict[str, Any]) -> InlineKeyboardMarkup:
    key = f"47:{UUID}"
    result = InlineKeyboardMarkup()
    result.row(InlineKeyboardButton("Указать lot ID", callback_data=f"{key}:lot"))
    result.row(InlineKeyboardButton("Загрузить TXT", callback_data=f"{key}:upload"))
    result.row(InlineKeyboardButton("Шаблон выдачи", callback_data=f"{key}:template"))
    result.row(InlineKeyboardButton(
        "Автовыдача: ВКЛ" if data["enabled"] else "Автовыдача: ВЫКЛ",
        callback_data=f"{key}:toggle"))
    result.row(InlineKeyboardButton(f"Аккаунтов в очереди: {len(data['accounts'])}", callback_data=f"{key}:noop"))
    result.row(InlineKeyboardButton("Закрыть", callback_data=f"{key}:close"))
    return result


def _show(cardinal: Any, chat_id: int, message_id: int | None = None) -> None:
    with LOCK:
        data = _load()
    text = (
        "<b>Claude Accounts Auto Delivery</b>\n\n"
        f"Лот: <code>{html.escape(str(data.get('lot_id') or 'не указан'))}</code>\n"
        f"Аккаунтов: <b>{len(data['accounts'])}</b>\n"
        f"Ожидают бонуса за отзыв: <b>{len(data['pending_bonuses'])}</b>\n\n"
        "TXT должен содержать блоки <code>=== Claude Account ===</code> и поля Email, Password, Recovery Email, Recovery Password."
    )
    kwargs = {"parse_mode": "HTML", "reply_markup": _keyboard(data)}
    if message_id is None:
        cardinal.telegram.bot.send_message(chat_id, text, **kwargs)
    else:
        cardinal.telegram.bot.edit_message_text(text, chat_id, message_id, **kwargs)


def _prompt(cardinal: Any, call: Any, state: str, text: str) -> None:
    cardinal.telegram.set_state(call.message.chat.id, call.message.id, call.from_user.id, state)
    cardinal.telegram.bot.edit_message_text(
        text, call.message.chat.id, call.message.id,
        reply_markup=InlineKeyboardMarkup().add(
            InlineKeyboardButton("Отмена", callback_data=f"47:{UUID}:back"))
    )


def _parse_accounts(text: str) -> list[dict[str, Any]]:
    blocks = re.split(r"(?im)^\s*===\s*Claude Account\s*===\s*$", text)
    accounts = []
    required = {"email", "password", "recovery email", "recovery password"}
    for index, block in enumerate(blocks[1:], 1):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            if ":" not in line:
                continue
            name, value = line.split(":", 1)
            if name.strip().lower() in required:
                fields[name.strip().lower()] = value.strip()
        missing = required - fields.keys()
        if missing:
            raise ValueError(f"блок {index}: не хватает полей {', '.join(sorted(missing))}")
        raw = "\n".join(f"{name}: {fields[name]}" for name in (
            "email", "password", "recovery email", "recovery password"))
        accounts.append({"raw": raw, "fields": fields})
    if not accounts:
        raise ValueError("не найдены блоки === Claude Account ===")
    return accounts


def _format(account: dict[str, Any], template: str, order: Any) -> str:
    values = {"account": account["raw"], "username": getattr(order, "buyer_username", ""),
              "buyer": getattr(order, "buyer_username", ""), "order_id": getattr(order, "id", "")}
    values.update(account.get("fields", {}))
    return re.sub(r"\{([^{}]+)\}", lambda m: str(values.get(m.group(1), m.group(0))), template).strip()


def _deliver(cardinal: Any, order: Any, bonus: bool = False) -> None:
    order_id = str(getattr(order, "id", "")).lstrip("#").upper()
    buyer = str(getattr(order, "buyer_username", "") or "")
    if not buyer:
        raise RuntimeError("не удалось определить покупателя")
    chat = cardinal.account.get_chat_by_name(buyer, make_request=True)
    if chat is None:
        raise RuntimeError("чат покупателя не найден")
    with LOCK:
        data = _load()
        if not data["accounts"]:
            raise RuntimeError("в очереди нет аккаунтов")
        account = data["accounts"].pop(0)
        _save(data)
    try:
        cardinal.account.send_message(chat.id, _format(account, data["template"], order), chat_name=buyer)
    except Exception:
        with LOCK:
            data = _load(); data["accounts"].insert(0, account); _save(data)
        raise
    with LOCK:
        data = _load()
        target = data["granted_bonuses"] if bonus else data["delivered_orders"]
        target.append(order_id)
        target[:] = target[-1000:]
        data["pending_bonuses"].pop(order_id, None)
        _save(data)


def _lot_id(obj: Any) -> str:
    direct = getattr(obj, "lot_id", None) or getattr(obj, "offer_id", None)
    if direct:
        return str(direct)
    source = str(getattr(obj, "html", "") or "")
    for pattern in (
        r"data-offer=[\"'](\d+)[\"']",
        r"lots/offer\?id=(\d+)",
        r"offer_id[\"']?\s*[:=]\s*[\"']?(\d+)",
    ):
        match = re.search(pattern, source, re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def _on_order(cardinal: Any, event: Any) -> None:
    if type(event).__name__ != "NewOrderEvent" or not cardinal.account:
        return
    shortcut = getattr(event, "order", None)
    if shortcut is None:
        return
    order_id = str(getattr(shortcut, "id", "")).lstrip("#").upper()
    with LOCK:
        data = _load()
    if not data["enabled"] or not data["lot_id"] or order_id in data["delivered_orders"] or order_id in PROCESSING:
        return
    PROCESSING.add(order_id)
    selected = None
    try:
        order = cardinal.account.get_order(order_id)
        if (_lot_id(order) or _lot_id(shortcut)) != data["lot_id"]:
            return
        amount = max(1, int(getattr(shortcut, "amount", 1) or 1))
        with LOCK:
            data = _load()
            if len(data["accounts"]) < amount:
                raise RuntimeError(f"нужно {amount}, доступно {len(data['accounts'])}")
            selected = data["accounts"][:amount]; del data["accounts"][:amount]; _save(data)
        buyer = str(getattr(order, "buyer_username", "") or "")
        chat = cardinal.account.get_chat_by_name(buyer, make_request=True)
        if chat is None: raise RuntimeError("чат покупателя не найден")
        cardinal.account.send_message(chat.id, "\n\n".join(_format(x, data["template"], order) for x in selected), chat_name=buyer)
        with LOCK:
            data = _load(); data["delivered_orders"].append(order_id); data["delivered_orders"] = data["delivered_orders"][-1000:]; _save(data)
        cardinal.telegram.send_notification(f"✅ Claude-аккаунт выдан по заказу #{order_id}")
    except Exception as error:
        with LOCK:
            if selected: data = _load(); data["accounts"] = selected + data["accounts"]; _save(data)
        cardinal.telegram.send_notification(f"❌ Ошибка выдачи Claude #{order_id}: {str(error)[:250]}")
    finally:
        PROCESSING.discard(order_id)


def _order_from_review(cardinal: Any, message: Any) -> Any:
    match = re.search(r"#([A-Z0-9]{6,})", str(getattr(message, "text", "")), re.I)
    return cardinal.account.get_order(match.group(1)) if match else None


def _on_message(cardinal: Any, event: Any) -> None:
    message = getattr(event, "message", None)
    kind = str(getattr(getattr(message, "type", None), "name", "")) if message else ""
    if kind not in {"NEW_FEEDBACK", "FEEDBACK_CHANGED"}:
        return
    try:
        order = _order_from_review(cardinal, message)
        if order is None or getattr(order, "review", None) is None:
            return
        order_id = str(getattr(order, "id", "")).lstrip("#").upper()
        with LOCK:
            data = _load()
            if order_id in data["seen_reviews"] or order_id in data["granted_bonuses"]:
                return
            data["seen_reviews"].append(order_id); data["pending_bonuses"][order_id] = {"buyer": getattr(order, "buyer_username", "")}; _save(data)
        key = InlineKeyboardMarkup().add(InlineKeyboardButton("Выдать +1 за отзыв", callback_data=f"47:{UUID}:bonus:{order_id}"))
        review = getattr(order, "review", None)
        cardinal.telegram.send_notification(
            f"📝 Новый отзыв по заказу #{order_id}\nОценка: {getattr(review, 'stars', '')}\nТекст: {str(getattr(review, 'text', '') or '')[:500]}", keyboard=key)
    except Exception:
        LOG.exception("Не удалось обработать отзыв")


def _on_command(cardinal: Any, message: Any) -> None:
    _show(cardinal, message.chat.id)


def _on_telegram_message(cardinal: Any, message: Any) -> None:
    user = getattr(message, "from_user", None)
    if not user: return
    state = cardinal.telegram.get_state(message.chat.id, user.id)
    if not state: return
    mode = state.get("state")
    try:
        if mode == "cl_lot":
            value = str(getattr(message, "text", "") or "").strip()
            if not value.isdigit(): raise ValueError("lot ID должен быть числом")
            with LOCK: data = _load(); data["lot_id"] = value; _save(data)
        elif mode == "cl_template":
            value = str(getattr(message, "text", "") or "").strip()
            if not value: raise ValueError("шаблон не может быть пустым")
            with LOCK: data = _load(); data["template"] = value; _save(data)
        elif mode == "cl_accounts":
            text = str(getattr(message, "text", "") or "")
            if getattr(message, "document", None) is not None:
                info = cardinal.telegram.bot.get_file(message.document.file_id)
                text = cardinal.telegram.bot.download_file(info.file_path).decode("utf-8-sig")
            accounts = _parse_accounts(text)
            with LOCK: data = _load(); data["accounts"].extend(accounts); _save(data)
        else: return
        cardinal.telegram.clear_state(message.chat.id, user.id)
        cardinal.telegram.bot.send_message(message.chat.id, "Готово.")
        _show(cardinal, message.chat.id)
    except Exception as error:
        cardinal.telegram.bot.send_message(message.chat.id, f"Не удалось сохранить: {error}")


def _on_callback(cardinal: Any, call: Any) -> None:
    if not str(getattr(call, "data", "")).startswith(f"47:{UUID}"): return
    parts = str(call.data).split(":"); action = parts[2] if len(parts) > 2 else ""
    cardinal.telegram.bot.answer_callback_query(call.id)
    chat_id = call.message.chat.id
    if action in {"", "0", "main"}: _show(cardinal, chat_id, call.message.id)
    elif action == "lot": _prompt(cardinal, call, "cl_lot", "Отправьте lot ID одним сообщением.")
    elif action == "upload": _prompt(cardinal, call, "cl_accounts", "Отправьте TXT-файл или текст с блоками === Claude Account ===.")
    elif action == "template": _prompt(cardinal, call, "cl_template", "Отправьте шаблон. Доступны {account}, {email}, {password}, {recovery email}, {recovery password}, {order_id}.")
    elif action == "toggle":
        with LOCK: data = _load(); data["enabled"] = not data["enabled"]; _save(data)
        _show(cardinal, chat_id, call.message.id)
    elif action == "bonus" and len(parts) > 3:
        order_id = parts[3]
        with LOCK: data = _load(); allowed = order_id in data["pending_bonuses"] and order_id not in data["granted_bonuses"]
        if not allowed: cardinal.telegram.bot.send_message(chat_id, "Бонус уже выдан или отзыв не найден."); return
        try:
            _deliver(cardinal, cardinal.account.get_order(order_id), bonus=True)
            cardinal.telegram.bot.edit_message_text(f"✅ Бонус Claude выдан по заказу #{order_id}", chat_id, call.message.id)
        except Exception as error: cardinal.telegram.bot.send_message(chat_id, f"Не удалось выдать бонус: {error}")
    elif action == "back": cardinal.telegram.clear_state(chat_id, call.from_user.id); _show(cardinal, chat_id, call.message.id)
    elif action == "close": cardinal.telegram.clear_state(chat_id, call.from_user.id); cardinal.telegram.bot.delete_message(chat_id, call.message.id)


def _init(cardinal: Any) -> None:
    cardinal.add_telegram_commands(UUID, [("claude", "Настройки авто-выдачи Claude", True)])
    cardinal.telegram.msg_handler(lambda m: _on_command(cardinal, m), commands=["claude"])
    cardinal.telegram.msg_handler(lambda m: _on_telegram_message(cardinal, m), content_types=["text", "document"])
    cardinal.telegram.cbq_handler(lambda c: _on_callback(cardinal, c), lambda c: str(getattr(c, "data", "")).startswith(f"47:{UUID}"))


BIND_TO_INIT = [_init]
BIND_TO_NEW_ORDER = [_on_order]
BIND_TO_NEW_MESSAGE = [_on_message]
