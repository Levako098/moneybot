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
VERSION = "1.3.0"
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
ACCOUNT_PAGE_SIZE = 8


def _default() -> dict[str, Any]:
    return {
        "enabled": True,
        "lot_id": "",
        "lot_title": "",
        "account_format": [],
        "template": "Спасибо за покупку!\n\nАккаунт ChatGPT:\n{account}\n\nЗаказ: #{order_id}",
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
    data["lot_title"] = str(data.get("lot_title") or "").strip()
    data["account_format"] = [
        str(item).strip() for item in data.get("account_format", []) if str(item).strip()
    ]
    data["template"] = str(data.get("template") or _default()["template"]).strip()
    accounts = []
    for item in data.get("accounts", []):
        if isinstance(item, dict):
            raw = str(item.get("raw") or item.get("account") or "").strip()
            fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
            fields = {str(key): str(value) for key, value in fields.items()}
        else:
            raw, fields = str(item).strip(), {}
        if raw:
            accounts.append({"raw": raw, "fields": fields})
    data["accounts"] = accounts
    data["delivered_orders"] = [str(item).upper() for item in data.get("delivered_orders", [])]
    return data


def _save(data: dict[str, Any]) -> None:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = DATA_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(DATA_PATH)


def _menu(data: dict[str, Any]) -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup()
    keyboard.row(
        InlineKeyboardButton("Указать lot ID", callback_data=f"47:{UUID}:set_lot"),
        InlineKeyboardButton("Проверить лот", callback_data=f"47:{UUID}:check_lot"),
    )
    keyboard.row(InlineKeyboardButton("Загрузить аккаунты", callback_data=f"47:{UUID}:upload"))
    keyboard.row(InlineKeyboardButton("Шаблон выдачи", callback_data=f"47:{UUID}:template"))
    keyboard.row(
        InlineKeyboardButton(
            "🔴 Автовыдача выключена" if not data["enabled"] else "🟢 Автовыдача включена",
            callback_data=f"47:{UUID}:toggle",
        )
    )
    keyboard.row(InlineKeyboardButton("Очистить очередь", callback_data=f"47:{UUID}:clear"))
    keyboard.row(InlineKeyboardButton("Удалить аккаунты выборочно", callback_data=f"47:{UUID}:accounts:0"))
    keyboard.row(InlineKeyboardButton("Закрыть", callback_data=f"47:{UUID}:close"))
    return keyboard


def _menu_text(data: dict[str, Any]) -> str:
    lot_id = data["lot_id"] or "не указан"
    status = "включена" if data["enabled"] else "выключена"
    return (
        "<b>ChatGPT Accounts Auto Delivery</b>\n\n"
        f"Lot ID: <code>{html.escape(lot_id)}</code>\n"
        f"Название: <b>{html.escape(data['lot_title'] or 'не проверено')}</b>\n"
        f"Аккаунтов в очереди: <b>{len(data['accounts'])}</b>\n"
        f"Автовыдача: <b>{status}</b>\n\n"
        "Один аккаунт = одна непустая строка файла.\n"
        f"Формат: <code>{html.escape(':'.join(data['account_format']) or 'целая строка')}</code>\n"
        f"Шаблон: <code>{html.escape(data['template'][:180])}</code>"
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


def _accounts_view(data: dict[str, Any], page: int) -> tuple[str, InlineKeyboardMarkup]:
    total = len(data["accounts"])
    pages = max(1, (total + ACCOUNT_PAGE_SIZE - 1) // ACCOUNT_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    start = page * ACCOUNT_PAGE_SIZE
    end = min(total, start + ACCOUNT_PAGE_SIZE)
    lines = [f"<b>Аккаунты в очереди</b> ({total})", ""]
    if not total:
        lines.append("Очередь пуста.")
    else:
        for index in range(start, end):
            item = data["accounts"][index]
            raw = item.get("raw", "") if isinstance(item, dict) else str(item)
            lines.append(f"<b>#{index + 1}</b> <code>{html.escape(raw[:100])}</code>")
    keyboard = InlineKeyboardMarkup()
    for index in range(start, end):
        item = data["accounts"][index]
        raw = item.get("raw", "") if isinstance(item, dict) else str(item)
        keyboard.row(
            InlineKeyboardButton(
                f"🗑 #{index + 1} {raw[:28]}",
                callback_data=f"47:{UUID}:ask_delete:{index}",
            )
        )
    if pages > 1:
        keyboard.row(
            InlineKeyboardButton("◀️", callback_data=f"47:{UUID}:accounts:{max(0, page - 1)}"),
            InlineKeyboardButton(f"{page + 1}/{pages}", callback_data=f"47:{UUID}:accounts:{page}"),
            InlineKeyboardButton("▶️", callback_data=f"47:{UUID}:accounts:{min(pages - 1, page + 1)}"),
        )
    keyboard.row(InlineKeyboardButton("Назад", callback_data=f"47:{UUID}:0"))
    return "\n".join(lines), keyboard


def _show_accounts(cardinal: Any, call: Any, page: int = 0) -> None:
    with LOCK:
        data = _load()
    text, keyboard = _accounts_view(data, page)
    cardinal.telegram.bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.id,
        parse_mode="HTML",
        reply_markup=keyboard,
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


def _parse_format(text: str) -> list[str]:
    names = [part.strip() for part in str(text).split(":") if part.strip()]
    if not names or len(names) > 30:
        raise ValueError("укажите от 1 до 30 полей через двоеточие")
    if any(not re.fullmatch(r"[A-Za-zА-Яа-яЁё0-9_]+", name) for name in names):
        raise ValueError("имена полей могут содержать только буквы, цифры и _")
    if len(set(names)) != len(names):
        raise ValueError("имена полей не должны повторяться")
    return names


def _parse_accounts(text: str, names: list[str]) -> list[dict[str, Any]]:
    accounts = []
    for line_number, line in enumerate(
        (item.strip() for item in str(text).splitlines()), start=1
    ):
        if not line:
            continue
        values = line.split(":")
        if len(values) != len(names):
            raise ValueError(
                f"строка {line_number}: ожидалось {len(names)} полей, разделённых :"
            )
        accounts.append({"raw": line, "fields": dict(zip(names, values))})
    return accounts


TEMPLATE_HELP = (
    "Переменные шаблона:\n"
    "{account} — выданный аккаунт\n"
    "{username} / {buyer} — покупатель\n"
    "{order_id} — ID заказа\n"
    "{lot_id} — ID лота\n"
    "{amount} — количество аккаунтов\n"
    "{position} — номер аккаунта в заказе\n"
    "{total} — всего аккаунтов в заказе\n"
    "{lot} — название лота"
)


class _TemplateValues(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _format_account(
    template: str,
    account: Any,
    order: Any,
    lot_id: str,
    amount: int,
    position: int,
) -> str:
    buyer = str(getattr(order, "buyer_username", "") or "")
    raw = str(account.get("raw") if isinstance(account, dict) else account)
    fields = account.get("fields", {}) if isinstance(account, dict) else {}
    values = _TemplateValues(
        account=raw,
        raw=raw,
        username=buyer,
        buyer=buyer,
        order_id=str(getattr(order, "id", "") or ""),
        lot_id=lot_id,
        amount=amount,
        position=position,
        total=amount,
        lot=str(getattr(order, "title", None) or getattr(order, "short_description", None) or ""),
    )
    values.update(fields)
    return re.sub(
        r"\{([A-Za-zА-Яа-яЁё0-9_]+)\}",
        lambda match: str(values.get(match.group(1), match.group(0))),
        template,
    ).strip()


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


def _resolve_lot_title(account: Any, lot_id: str) -> str:
    errors = []
    try:
        fields = account.get_lot_fields(int(lot_id))
        title = str(
            getattr(fields, "title_ru", None)
            or getattr(fields, "title_en", None)
            or ""
        ).strip()
        if title:
            return title
    except Exception as error:
        errors.append(str(error).splitlines()[0][:180])

    try:
        profile = account.get_user(account.id)
        for lot in profile.get_lots():
            if str(getattr(lot, "id", "")) == str(lot_id):
                title = str(getattr(lot, "title", None) or getattr(lot, "description", None) or "").strip()
                if title:
                    return title
    except Exception as error:
        errors.append(str(error).splitlines()[0][:180])
    reason = errors[0] if errors else "лот не найден в активных лотах профиля"
    raise RuntimeError(reason)


def _send_accounts(
    cardinal: Any,
    order: Any,
    shortcut: Any,
    accounts: list[Any],
    template: str,
    lot_id: str,
) -> None:
    buyer = str(getattr(order, "buyer_username", "") or getattr(shortcut, "buyer_username", ""))
    if not buyer:
        raise RuntimeError("не удалось определить покупателя")
    chat = cardinal.account.get_chat_by_name(buyer, make_request=True)
    if chat is None:
        raise RuntimeError("чат покупателя не найден")
    amount = len(accounts)
    payload = "\n\n".join(
        _format_account(template, account, order, lot_id, amount, index)
        for index, account in enumerate(accounts, start=1)
    )
    if not payload:
        raise RuntimeError("шаблон выдачи сформировал пустое сообщение")
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
        if str(_lot_id(order) or _lot_id(shortcut)) != data["lot_id"]:
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
        _send_accounts(cardinal, order, shortcut, selected, data["template"], data["lot_id"])
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
    elif mode == "cg_format":
        try:
            names = _parse_format(getattr(message, "text", "") or "")
        except ValueError as error:
            cardinal.telegram.bot.send_message(message.chat.id, f"Неверный формат: {error}")
            return
        with LOCK:
            data = _load()
            data["account_format"] = names
            _save(data)
        cardinal.telegram.set_state(
            message.chat.id, message.id, message.from_user.id, "cg_accounts"
        )
        cardinal.telegram.bot.send_message(
            message.chat.id,
            "Формат сохранён: " + ":".join(names) + "\nТеперь отправьте .txt/.csv или аккаунты текстом.",
        )
    elif mode == "cg_accounts":
        text = str(getattr(message, "text", "") or "")
        if getattr(message, "document", None) is not None:
            try:
                info = cardinal.telegram.bot.get_file(message.document.file_id)
                text = cardinal.telegram.bot.download_file(info.file_path).decode("utf-8-sig")
            except Exception as error:
                cardinal.telegram.bot.send_message(message.chat.id, f"Не удалось прочитать файл: {error}")
                return
        try:
            names = _load()["account_format"]
            if not names:
                raise ValueError("сначала укажите формат аккаунтов")
            accounts = _parse_accounts(text, names)
        except ValueError as error:
            cardinal.telegram.bot.send_message(message.chat.id, f"Аккаунты не загружены: {error}")
            return
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
    elif mode == "cg_template":
        template = str(getattr(message, "text", "") or "").strip()
        if not template:
            cardinal.telegram.bot.send_message(message.chat.id, "Шаблон не должен быть пустым.")
            return
        if len(template) > 3500:
            cardinal.telegram.bot.send_message(message.chat.id, "Шаблон слишком длинный: максимум 3500 символов.")
            return
        with LOCK:
            data = _load()
            data["template"] = template
            _save(data)
        cardinal.telegram.clear_state(message.chat.id, message.from_user.id)
        _show_settings(cardinal, message.chat.id)


def _on_callback(cardinal: Any, call: Any) -> None:
    if not str(getattr(call, "data", "")).startswith(f"47:{UUID}"):
        return
    parts = str(call.data).split(":")
    action = parts[2] if len(parts) > 2 else ""
    argument = parts[3] if len(parts) > 3 else ""
    cardinal.telegram.bot.answer_callback_query(call.id)
    if action in {"", "0", "main"}:
        _show_settings(cardinal, call.message.chat.id, call.message.id)
    elif action == "set_lot":
        _prompt(cardinal, call, "cg_lot", "Отправьте lot ID числом одним сообщением.")
    elif action == "check_lot":
        with LOCK:
            data = _load()
        if not data["lot_id"]:
            cardinal.telegram.bot.send_message(call.message.chat.id, "Сначала укажите lot ID.")
        else:
            try:
                title = _resolve_lot_title(cardinal.account, data["lot_id"])
                with LOCK:
                    data = _load()
                    data["lot_title"] = title
                    _save(data)
            except Exception as error:
                cardinal.telegram.bot.send_message(
                    call.message.chat.id,
                    f"Не удалось проверить лот {data['lot_id']}: {str(error).splitlines()[0][:300]}",
                )
        _show_settings(cardinal, call.message.chat.id, call.message.id)
    elif action == "upload":
        _prompt(
            cardinal,
            call,
            "cg_format",
            "Сначала укажите формат одной строки аккаунта.\n\n"
            "Пример: mail:password:2facode:2falinkactivator\n"
            "Имена полей станут переменными шаблона: {mail}, {password}, {2facode}, {2falinkactivator}.",
        )
    elif action == "accounts":
        try:
            page = int(argument or 0)
        except ValueError:
            page = 0
        _show_accounts(cardinal, call, page)
    elif action == "ask_delete":
        try:
            index = int(argument)
        except ValueError:
            return
        with LOCK:
            data = _load()
        if not 0 <= index < len(data["accounts"]):
            _show_accounts(cardinal, call, 0)
            return
        item = data["accounts"][index]
        raw = item.get("raw", "") if isinstance(item, dict) else str(item)
        keyboard = InlineKeyboardMarkup().row(
            InlineKeyboardButton("Удалить", callback_data=f"47:{UUID}:delete_account:{index}"),
            InlineKeyboardButton("Отмена", callback_data=f"47:{UUID}:accounts:{index // ACCOUNT_PAGE_SIZE}"),
        )
        cardinal.telegram.bot.edit_message_text(
            f"Удалить аккаунт <code>{html.escape(raw[:160])}</code>?",
            call.message.chat.id,
            call.message.id,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    elif action == "delete_account":
        try:
            index = int(argument)
        except ValueError:
            return
        with LOCK:
            data = _load()
            if 0 <= index < len(data["accounts"]):
                data["accounts"].pop(index)
                _save(data)
        _show_accounts(cardinal, call, index // ACCOUNT_PAGE_SIZE)
    elif action == "template":
        with LOCK:
            data = _load()
            current = data["template"]
            custom = ": ".join("{" + name + "}" for name in data["account_format"])
        _prompt(
            cardinal,
            call,
            "cg_template",
            f"{TEMPLATE_HELP}\n"
            f"Поля аккаунта: {custom or 'формат ещё не задан'}\n\n"
            f"Текущий шаблон:\n{current}\n\nОтправьте новый шаблон одним сообщением.",
        )
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
    cardinal.telegram.msg_handler(
        lambda message: _on_command(cardinal, message), commands=["chatgpt"]
    )
    cardinal.telegram.msg_handler(
        lambda message: _on_message(cardinal, message),
        content_types=["text", "document"],
    )
    cardinal.telegram.cbq_handler(
        lambda call: _on_callback(cardinal, call),
        lambda call: str(getattr(call, "data", "")).startswith(f"47:{UUID}"),
    )


BIND_TO_INIT = [_init]
BIND_TO_NEW_ORDER = [_on_order]
