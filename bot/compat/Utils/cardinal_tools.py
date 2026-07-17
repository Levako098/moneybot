from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import bcrypt
import requests


ROOT = Path(__file__).resolve().parents[3]
CACHE_DIR = ROOT / "storage" / "cache"


def _cache(name: str, value: Any) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / name).write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _load(name: str, default: Any) -> Any:
    try:
        return json.loads((CACHE_DIR / name).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def count_products(path: str) -> int:
    try:
        return len([line for line in Path(path).read_text(encoding="utf-8").splitlines() if line])
    except OSError:
        return 0


def cache_blacklist(blacklist: list[str]) -> None:
    _cache("blacklist.json", blacklist)


def load_blacklist() -> list[str]:
    value = _load("blacklist.json", [])
    return value if isinstance(value, list) else []


def check_proxy(proxy: dict[str, str]) -> bool:
    try:
        return requests.get("https://api.ipify.org/", proxies=proxy, timeout=10).ok
    except requests.RequestException:
        return False


def validate_proxy(proxy: str) -> tuple[str, str, str, str, str]:
    match = re.fullmatch(
        r"(?:(https?|socks5h?)://)?(?:(.+?):(.+?)@)?([^:]+):(\d+)", proxy.strip()
    )
    if not match:
        raise ValueError("Некорректный формат прокси")
    scheme, login, password, host, port = match.groups()
    if not 0 < int(port) <= 65535:
        raise ValueError("Некорректный порт прокси")
    return scheme or "http", login or "", password or "", host, port


def build_proxy(scheme: str | None, login: str, password: str, ip: str, port: str) -> str:
    auth = f"{login}:{password}@" if login and password else ""
    return f"{scheme or 'http'}://{auth}{ip}:{port}"


def cache_proxy_dict(value: dict[int, str]) -> None:
    _cache("proxy_dict.json", value)


def load_proxy_dict() -> dict[int, str]:
    value = _load("proxy_dict.json", {})
    return {int(key): str(item) for key, item in value.items()} if isinstance(value, dict) else {}


def cache_disabled_plugins(value: list[str]) -> None:
    _cache("disabled_plugins.json", value)


def load_disabled_plugins() -> list[str]:
    value = _load("disabled_plugins.json", [])
    return value if isinstance(value, list) else []


def cache_pinned_plugins(value: list[str]) -> None:
    _cache("pinned_plugins.json", value)


def load_pinned_plugins() -> list[str]:
    value = _load("pinned_plugins.json", [])
    return value if isinstance(value, list) else []


def cache_old_users(value: dict[int, float]) -> None:
    _cache("old_users.json", value)


def load_old_users(greetings_cooldown: float) -> dict[int, float]:
    value = _load("old_users.json", {})
    if isinstance(value, list):
        return {int(item): time.time() for item in value}
    lifetime = greetings_cooldown * 86400
    return {
        int(key): float(saved_at)
        for key, saved_at in value.items()
        if time.time() - float(saved_at) < lifetime
    } if isinstance(value, dict) else {}


def create_greeting_text(cardinal: Any) -> str:
    account = cardinal.account
    return f"MoneyBot / FunPayCardinal compatibility: {account.username} ({account.id})"


def time_to_str(value: int | float) -> str:
    seconds = max(0, int(value))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    if days:
        parts.append(f"{days}д")
    if hours:
        parts.append(f"{hours}ч")
    if minutes:
        parts.append(f"{minutes}мин")
    if seconds:
        parts.append(f"{seconds}сек")
    return " ".join(parts) or "0 сек"


def get_month_name(month_number: int) -> str:
    months = (
        "Января", "Февраля", "Марта", "Апреля", "Мая", "Июня",
        "Июля", "Августа", "Сентября", "Октября", "Ноября", "Декабря",
    )
    return months[month_number - 1] if 1 <= month_number <= 12 else months[0]


def get_products(path: str, amount: int = 1) -> list[Any]:
    target = Path(path)
    products = [line for line in target.read_text(encoding="utf-8").splitlines() if line]
    if len(products) < amount:
        raise ValueError(f"Недостаточно товаров: требуется {amount}, доступно {len(products)}")
    selected = products[:amount]
    remaining = products[amount:]
    target.write_text("\n".join(remaining), encoding="utf-8")
    return [selected, len(remaining)]


def add_products(path: str, products: list[str], at_zero_position: bool = False) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    old = target.read_text(encoding="utf-8").splitlines() if target.exists() else []
    values = products + old if at_zero_position else old + products
    target.write_text("\n".join(item for item in values if item), encoding="utf-8")


def safe_text(text: str) -> str:
    return "⁣".join(str(text))


def _date_variables() -> dict[str, str]:
    now = datetime.now()
    date_text = f"{now.day} {get_month_name(now.month)}"
    return {
        "$full_date_text": f"{date_text} {now.year} года",
        "$date_text": date_text,
        "$date": now.strftime("%d.%m.%Y"),
        "$time": now.strftime("%H:%M"),
        "$full_time": now.strftime("%H:%M:%S"),
    }


def format_msg_text(text: str, obj: Any) -> str:
    values = _date_variables()
    values.update(
        {
            "$username": safe_text(getattr(obj, "author", None) or getattr(obj, "name", "")),
            "$message_text": str(obj),
            "$chat_id": str(getattr(obj, "chat_id", None) or getattr(obj, "id", "")),
            "$chat_name": safe_text(getattr(obj, "chat_name", None) or getattr(obj, "name", "")),
        }
    )
    for key, value in values.items():
        text = text.replace(key, value)
    return text


def format_order_text(text: str, order: Any) -> str:
    values = _date_variables()
    subcategory = getattr(order, "subcategory", None)
    category = getattr(subcategory, "category", None)
    description = str(
        getattr(order, "description", None)
        or getattr(order, "short_description", None)
        or ""
    )
    params = str(getattr(order, "lot_params_text", None) or "")
    values.update(
        {
            "$order_id": str(getattr(order, "id", "")),
            "$username": safe_text(getattr(order, "buyer_username", "")),
            "$order_desc_and_params": f"{description}, {params}" if description and params else description + params,
            "$order_desc_or_params": description or params,
            "$order_desc": description,
            "$order_title": description,
            "$order_params": params,
            "$order_link": f"https://funpay.com/orders/{getattr(order, 'id', '')}/",
            "$category_fullname": str(getattr(subcategory, "fullname", "") or ""),
            "$category": str(getattr(subcategory, "name", "") or ""),
            "$game": str(getattr(category, "name", "") or ""),
        }
    )
    for key, value in values.items():
        text = text.replace(key, value)
    return text


def restart_program() -> None:
    os.execl(os.sys.executable, os.sys.executable, *os.sys.argv)


def shut_down() -> None:
    raise SystemExit(0)


def set_console_title(title: str) -> None:
    if os.name == "nt":
        os.system(f"title {title}")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def check_password(password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed_password.encode())
