from __future__ import annotations

import html
import json
import math
from pathlib import Path
from typing import Any

from telebot.types import InlineKeyboardButton as B
from telebot.types import InlineKeyboardMarkup as K

from bot.compat.tg_bot import CBT


ROOT = Path(__file__).resolve().parents[3]
CACHE_DIR = ROOT / "storage" / "cache"


class NotificationTypes:
    bot_start = "1"
    new_message = "2"
    command = "3"
    new_order = "4"
    order_confirmed = "5"
    review = "5r"
    lots_restore = "6"
    lots_deactivate = "7"
    delivery = "8"
    lots_raise = "9"
    other = "10"
    announcement = "11"
    ad = "12"
    critical = "13"
    important_announcement = "14"


def _load_json(name: str, default: Any) -> Any:
    try:
        return json.loads((CACHE_DIR / name).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _save_json(name: str, value: Any) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / name).write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_authorized_users() -> dict[int, dict[str, Any]]:
    raw = _load_json("tg_authorized_users.json", {})
    if isinstance(raw, list):
        return {int(item): {} for item in raw}
    return {int(key): value for key, value in raw.items()} if isinstance(raw, dict) else {}


def load_notification_settings() -> dict[str, dict[str, bool]]:
    value = _load_json("notifications.json", {})
    return value if isinstance(value, dict) else {}


def load_answer_templates() -> list[str]:
    value = _load_json("answer_templates.json", [])
    return value if isinstance(value, list) else []


def save_authorized_users(users: dict[int, dict[str, Any]]) -> None:
    _save_json("tg_authorized_users.json", users)


def save_notification_settings(settings: dict[str, dict[str, bool]]) -> None:
    _save_json("notifications.json", settings)


def save_answer_templates(templates: list[str]) -> None:
    _save_json("answer_templates.json", templates)


def escape(text: str) -> str:
    return html.escape(str(text), quote=False)


def has_brand_mark(watermark: str) -> bool:
    value = str(watermark).casefold()
    return "cardinal" in value or "fpc" in value or "кардинал" in value


def split_by_limit(list_of_str: list[str], limit: int = 4096) -> list[str]:
    result = []
    current = ""
    for part in list_of_str:
        if current and len(current) + len(part) > limit:
            result.append(current)
            current = part
        else:
            current += part
    if current:
        result.append(current)
    return result


def bool_to_text(value: bool | int | str | None, on: str = "🟢", off: str = "🔴") -> str:
    return on if value is not None and int(value) else off


def get_offset(element_index: int, max_elements_on_page: int) -> int:
    elements = element_index + 1
    on_page = elements % max_elements_on_page or max_elements_on_page
    return 0 if elements == on_page else element_index - on_page + 1


def add_navigation_buttons(
    keyboard_obj: K,
    curr_offset: int,
    max_elements_on_page: int,
    elements_on_page: int,
    elements_amount: int,
    callback_text: str,
    extra: list[Any] | None = None,
) -> K:
    suffix = (":" + ":".join(str(item) for item in extra)) if extra else ""
    if curr_offset <= 0 and curr_offset + elements_on_page >= elements_amount:
        return keyboard_obj
    back = max(0, curr_offset - max_elements_on_page)
    forward = min(
        get_offset(max(elements_amount - 1, 0), max_elements_on_page),
        curr_offset + elements_on_page,
    )
    page = curr_offset // max_elements_on_page + 1
    pages = max(1, math.ceil(elements_amount / max_elements_on_page))
    keyboard_obj.row(
        B("◀️", callback_data=f"{callback_text}:{back}{suffix}"),
        B(f"{page}/{pages}", callback_data=CBT.EMPTY),
        B("▶️", callback_data=f"{callback_text}:{forward}{suffix}"),
    )
    return keyboard_obj
