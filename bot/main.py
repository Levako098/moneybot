from __future__ import annotations

import asyncio
import csv
import contextlib
import html
import io
import json
import logging
import os
import platform
import re
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Awaitable, Callable

import psutil
from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import BotConfig, ConfigError, load_config
from bot.compat.tg_bot import CBT as CARDINAL_CBT
from bot.funpay_service import (
    FunPayService,
    FunPayServiceError,
    ProfileInfo,
    read_balance,
)
from bot.version import __version__
from bot.services.auto_delivery import (
    DELIVERY_VARIABLES,
    AutoDeliveryService,
    DeliveryResult,
    RaiseLotsResult,
)
from bot.services.auto_tickets import AutoTicketResult, AutoTicketService
from bot.services.automation import (
    MESSAGE_VARIABLES,
    NOTIFICATION_KEYS,
    REVIEW_VARIABLES,
    AutomationService,
)
from bot.services.tickets import (
    SupportRateLimitedError,
    TicketClient,
    TicketError,
)
from bot.services.plugin_manager import PluginManager, PluginRecord
from bot.services.system_settings import (
    CleanupResult,
    ResourceWarning,
    SystemSettingsService,
    TemporaryCleanupResult,
)
from bot.services.update_service import (
    ReleaseInfo,
    UpdateCheckResult,
    UpdateInstallResult,
    UpdateService,
)


AUTO_RAISE_SETTINGS_PATH = Path(__file__).resolve().parent / "data" / "auto_raise.json"


def auto_raise_enabled() -> bool:
    try:
        data = json.loads(AUTO_RAISE_SETTINGS_PATH.read_text(encoding="utf-8"))
        return bool(data.get("enabled", False)) if isinstance(data, dict) else False
    except (OSError, ValueError, TypeError):
        return False


def set_auto_raise_enabled(enabled: bool) -> None:
    AUTO_RAISE_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = AUTO_RAISE_SETTINGS_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps({"enabled": bool(enabled)}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(AUTO_RAISE_SETTINGS_PATH)


LOG_PATH = Path(__file__).resolve().parent / "data" / "moneybot.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
    ],
)
logger = logging.getLogger("moneybot")
router = Router(name="owner")


class NewTicket(StatesGroup):
    waiting_order_id = State()
    waiting_text = State()
    waiting_confirmation = State()


class FunPayReply(StatesGroup):
    waiting_text = State()


class AutomationSettings(StatesGroup):
    waiting_message_template = State()
    waiting_message_cooldown = State()
    waiting_review_template = State()
    waiting_command_name = State()
    waiting_command_response = State()
    waiting_command_notification = State()


class AutoDeliverySettings(StatesGroup):
    waiting_text = State()


class SystemSettings(StatesGroup):
    waiting_log_limit = State()


class PluginUpload(StatesGroup):
    waiting_file = State()


class AutoTicketSettings(StatesGroup):
    waiting_delay = State()
    waiting_interval = State()
    waiting_limit = State()
    waiting_template = State()


class OwnerOnlyMiddleware(BaseMiddleware):
    def __init__(self, owner_id: int) -> None:
        self.owner_id = owner_id

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if isinstance(event, CallbackQuery):
            chat = event.message.chat if event.message else None
        else:
            chat = getattr(event, "chat", None)
        if not user or not chat:
            return None
        if user.id != self.owner_id or chat.id != self.owner_id:
            return None
        return await handler(event, data)


def main_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Аккаунт", callback_data="menu:account")
    builder.button(text="Плагины", callback_data="menu:plugins")
    builder.button(text="Тикеты", callback_data="menu:tickets")
    builder.button(text="Настройки", callback_data="menu:settings")
    builder.adjust(2, 2)
    return builder.as_markup()


def back_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Назад", callback_data="menu:main")
    return builder.as_markup()


def account_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Обновить", callback_data="account:refresh")
    builder.button(text="Назад", callback_data="menu:account")
    builder.adjust(1)
    return builder.as_markup()


def account_root_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Информация", callback_data="account:info")
    builder.button(text="Ограничения", callback_data="account:restrictions")
    builder.button(text="Лоты", callback_data="account:lots")
    builder.button(text="Назад", callback_data="menu:main")
    builder.adjust(2, 1, 1)
    return builder.as_markup()


def restrictions_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Обновить", callback_data="account:restrictions")
    builder.button(text="Назад", callback_data="menu:account")
    builder.adjust(1)
    return builder.as_markup()


def lots_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Скачать список", callback_data="account:lots:download")
    builder.button(text="Автовыдача", callback_data="account:delivery:page:0")
    builder.button(text="Назад", callback_data="menu:account")
    builder.adjust(2, 1)
    return builder.as_markup()


def delivery_list_menu(
    rows: list[dict[str, Any]], page: int, page_size: int = 8
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    total_pages = max(1, (len(rows) + page_size - 1) // page_size)
    page = min(max(page, 0), total_pages - 1)
    for row in rows[page * page_size:(page + 1) * page_size]:
        marker = "🟢" if row["enabled"] else "🔴"
        title = str(row["title"])
        label = title[:35] + ("..." if len(title) > 35 else "")
        builder.button(
            text=f"{marker} {label}",
            callback_data=f"account:delivery:lot:{row['lot_id']}:{page}",
        )
    if total_pages > 1:
        previous_page = page - 1 if page > 0 else total_pages - 1
        next_page = page + 1 if page + 1 < total_pages else 0
        builder.button(text="◀", callback_data=f"account:delivery:page:{previous_page}")
        builder.button(text=f"{page + 1}/{total_pages}", callback_data="account:delivery:noop")
        builder.button(text="▶", callback_data=f"account:delivery:page:{next_page}")
    builder.button(text="Назад", callback_data="account:lots")
    builder.adjust(*([1] * min(page_size, len(rows[page * page_size:(page + 1) * page_size]))), 3, 1)
    return builder.as_markup()


def delivery_detail_menu(lot_id: str, page: int, enabled: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Изменить текст" if enabled else "Настроить автовыдачу",
        callback_data=f"account:delivery:set:{lot_id}:{page}",
    )
    if enabled:
        builder.button(
            text="Отключить",
            callback_data=f"account:delivery:disable:{lot_id}:{page}",
        )
    builder.button(text="Назад", callback_data=f"account:delivery:page:{page}")
    builder.adjust(1)
    return builder.as_markup()


def delivery_cancel_menu(lot_id: str, page: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Отмена", callback_data=f"account:delivery:lot:{lot_id}:{page}"
    )
    return builder.as_markup()


def settings_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Ответ на сообщения", callback_data="settings:messages")
    builder.button(text="Ответ на отзывы", callback_data="settings:reviews")
    builder.button(text="Уведомления", callback_data="settings:notifications")
    builder.button(text="Системные настройки", callback_data="settings:system")
    marker = "🟢" if auto_raise_enabled() else "🔴"
    builder.button(
        text=f"{marker} Поднятие всех лотов",
        callback_data="settings:lots:auto_toggle",
    )
    builder.button(text="Поднять сейчас", callback_data="settings:lots:raise")
    builder.button(text="Назад", callback_data="menu:main")
    builder.adjust(2, 1, 1, 1, 1, 1)
    return builder.as_markup()


def raise_lots_confirm_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Поднять", callback_data="settings:lots:raise:confirm")
    builder.button(text="Отмена", callback_data="menu:settings")
    builder.adjust(2)
    return builder.as_markup()


def message_settings_menu(enabled: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Выключить" if enabled else "Включить",
        callback_data="settings:messages:toggle",
    )
    builder.button(text="Изменить шаблон", callback_data="settings:messages:template")
    builder.button(text="Изменить КД", callback_data="settings:messages:cooldown")
    builder.button(text="Команды", callback_data="settings:messages:commands")
    builder.button(text="Назад", callback_data="menu:settings")
    builder.adjust(1)
    return builder.as_markup()


def review_settings_menu(enabled: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Выключить" if enabled else "Включить",
        callback_data="settings:reviews:toggle",
    )
    for stars in range(1, 6):
        builder.button(
            text=f"{stars} зв. шаблон",
            callback_data=f"settings:reviews:template:{stars}",
        )
    builder.button(text="Назад", callback_data="menu:settings")
    builder.adjust(1, 2, 2, 1, 1)
    return builder.as_markup()


def settings_cancel_menu(section: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Отмена", callback_data=f"settings:{section}")
    return builder.as_markup()


def notifications_settings_menu(settings: dict[str, Any]) -> InlineKeyboardMarkup:
    labels = {
        "incoming_messages": "Входящие сообщения",
        "outgoing_messages": "Мои сообщения",
        "orders": "Заказы",
        "reviews": "Отзывы",
        "refunds": "Возвраты",
        "other_system": "Прочие системные",
    }
    builder = InlineKeyboardBuilder()
    master_status = "🟢" if settings["enabled"] else "🔴"
    builder.button(
        text=f"{master_status} · Все уведомления",
        callback_data="settings:notifications:toggle:enabled",
    )
    for key in NOTIFICATION_KEYS:
        status = "🟢" if settings[key] else "🔴"
        builder.button(
            text=f"{status} · {labels[key]}",
            callback_data=f"settings:notifications:toggle:{key}",
        )
    builder.button(text="Назад", callback_data="menu:settings")
    builder.adjust(1)
    return builder.as_markup()


def system_settings_menu(settings: dict[str, Any]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    marker = "🟢" if settings["logs_enabled"] else "🔴"
    builder.button(
        text=f"{marker} Логи",
        callback_data="settings:system:toggle_logs",
    )
    builder.button(text="Ресурсы", callback_data="settings:system:resources")
    builder.button(
        text=f"Лимит: {settings['max_log_size_mb']} МБ",
        callback_data="settings:system:log_limit",
    )
    builder.button(text="Очистить логи", callback_data="settings:system:cleanup")
    builder.button(text="Назад", callback_data="menu:settings")
    builder.adjust(2, 1, 1, 1)
    return builder.as_markup()


def system_resources_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Обновить", callback_data="settings:system:resources")
    builder.button(text="Назад", callback_data="settings:system")
    builder.adjust(2)
    return builder.as_markup()


def resource_warning_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Очистить временные файлы",
        callback_data="system:resources:cleanup",
    )
    builder.button(text="Проверить ресурсы", callback_data="settings:system:resources")
    builder.adjust(1)
    return builder.as_markup()


def update_menu(release: ReleaseInfo) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Обновить", callback_data="update:install")
    if release.url:
        builder.button(text="Открыть GitHub", url=release.url)
    builder.adjust(1)
    return builder.as_markup()


def plugins_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="FunPay-профиль", callback_data="menu:account")
    builder.button(text="Автотикеты", callback_data="menu:tickets")
    builder.button(text="Назад", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def plugin_list_menu(manager: PluginManager) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for plugin in sorted(
        manager.plugins.values(),
        key=lambda item: (not item.pinned, item.name.casefold()),
    ):
        status = "🟢" if plugin.enabled else "🔴"
        pin = "📌 " if plugin.pinned else ""
        builder.button(
            text=f"{status} · {pin}{plugin.name} {plugin.version}",
            callback_data=f"plugin:view:{plugin.uuid}",
        )
    builder.button(text="Добавить плагин", callback_data="plugins:add")
    builder.button(text="Обновить", callback_data="plugins:refresh")
    builder.button(text="Назад", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def plugin_detail(plugin: PluginRecord) -> tuple[str, InlineKeyboardMarkup]:
    status = "включён" if plugin.enabled else "выключен"
    error = f"\n\n<b>Ошибка:</b> {html.escape(plugin.error)}" if plugin.error else ""
    commands = ""
    if plugin.commands:
        commands = "\n\n<b>Команды:</b>\n" + "\n".join(
            f"<code>/{html.escape(command)}</code> — {html.escape(description)}"
            for command, description in plugin.commands.items()
        )
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Выключить" if plugin.enabled else "Включить",
        callback_data=f"plugin:toggle:{plugin.uuid}",
    )
    builder.button(
        text="Открепить" if plugin.pinned else "Закрепить",
        callback_data=f"{CARDINAL_CBT.PIN_PLUGIN}:{plugin.uuid}:0",
    )
    if plugin.settings_page and plugin.enabled:
        builder.button(
            text="Настройки плагина",
            callback_data=f"{CARDINAL_CBT.PLUGIN_SETTINGS}:{plugin.uuid}:0",
        )
    builder.button(text="Удалить", callback_data=f"plugin:delete:{plugin.uuid}")
    builder.button(text="К списку", callback_data="menu:plugins")
    builder.adjust(1)
    text = (
        f"<b>{html.escape(plugin.name)} {html.escape(plugin.version)}</b>\n\n"
        f"<b>Статус:</b> {status}\n"
        f"<b>Файл:</b> <code>{html.escape(plugin.path.name)}</code>\n\n"
        f"<b>Автор:</b> {html.escape(plugin.credits or 'не указан')}\n\n"
        f"<b>Описание:</b>\n{html.escape(plugin.description[:1600])}"
        f"{commands}{error}"
    )
    return text, builder.as_markup()


def tickets_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Мои тикеты", callback_data="tickets:list")
    builder.button(text="Новый тикет", callback_data="tickets:new")
    builder.button(text="Настройки", callback_data="tickets:settings")
    builder.button(text="Назад", callback_data="menu:main")
    builder.adjust(2, 1, 1)
    return builder.as_markup()


def auto_ticket_settings_menu(settings: dict[str, Any]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    marker = "🟢" if settings["enabled"] else "🔴"
    scope = "Все заказы" if settings["scope"] == "all" else "Только автовыдача"
    builder.button(
        text=f"{marker} Автотикеты",
        callback_data="tickets:settings:toggle",
    )
    builder.button(
        text=f"Область: {scope}",
        callback_data="tickets:settings:scope",
    )
    builder.button(
        text=f"Ожидание: {settings['delay_hours']} ч",
        callback_data="tickets:settings:delay",
    )
    builder.button(
        text=f"Проверка: {settings['check_interval_minutes']} мин",
        callback_data="tickets:settings:interval",
    )
    builder.button(
        text=f"Лимит: {settings['max_orders_per_ticket']}",
        callback_data="tickets:settings:limit",
    )
    builder.button(text="Текст тикета", callback_data="tickets:settings:template")
    builder.button(text="Проверить сейчас", callback_data="tickets:settings:check")
    builder.button(text="Тест подтверждения", callback_data="tickets:settings:test")
    builder.button(text="Назад", callback_data="menu:tickets")
    builder.adjust(1)
    return builder.as_markup()


def auto_ticket_confirmation_menu(
    confirmation_id: str, test: bool = False
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if test:
        builder.button(text="Отправить", callback_data="autoticket:test:confirm")
        builder.button(text="Отменить", callback_data="autoticket:test:cancel")
    else:
        builder.button(
            text="Отправить",
            callback_data=f"autoticket:confirm:{confirmation_id}",
        )
        builder.button(
            text="Отменить",
            callback_data=f"autoticket:cancel:{confirmation_id}",
        )
    builder.adjust(2)
    return builder.as_markup()


def auto_ticket_cancel_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Отмена", callback_data="tickets:settings")
    return builder.as_markup()


def ticket_cancel_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Отмена", callback_data="tickets:cancel")
    return builder.as_markup()


def ticket_type_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Я продавец", callback_data="ticket:type:seller")
    builder.button(text="Я покупатель", callback_data="ticket:type:buyer")
    builder.button(text="Проблема аккаунта", callback_data="ticket:type:account")
    builder.button(text="Назад", callback_data="menu:tickets")
    builder.adjust(2, 1, 1)
    return builder.as_markup()


def seller_topics_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Не подтвердил заказ", callback_data="ticket:topic:seller:201")
    builder.button(text="Проблема с покупателем", callback_data="ticket:topic:seller:202")
    builder.button(text="Жалоба на отзыв", callback_data="ticket:topic:seller:203")
    builder.button(text="Отмена", callback_data="tickets:cancel")
    builder.adjust(1)
    return builder.as_markup()


def buyer_topics_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Продавец не отвечает", callback_data="ticket:topic:buyer:101")
    builder.button(text="Жалоба на продавца", callback_data="ticket:topic:buyer:102")
    builder.button(text="Отмена", callback_data="tickets:cancel")
    builder.adjust(1)
    return builder.as_markup()


def account_topics_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Аккаунт взломан", callback_data="ticket:topic:account:404")
    builder.button(text="Восстановить доступ", callback_data="ticket:topic:account:405")
    builder.button(text="Другая блокировка", callback_data="ticket:topic:account:409")
    builder.button(text="Ограничили чат", callback_data="ticket:topic:account:411")
    builder.button(text="Отмена", callback_data="tickets:cancel")
    builder.adjust(1)
    return builder.as_markup()


def ticket_confirm_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Отправить", callback_data="tickets:send")
    builder.button(text="Отмена", callback_data="tickets:cancel")
    builder.adjust(2)
    return builder.as_markup()


def money(value: float, currency: str) -> str:
    return f"{value:,.2f} {currency}".replace(",", " ")


def format_profile(profile: ProfileInfo) -> str:
    username = html.escape(profile.username)
    status = "онлайн" if profile.online else "не в сети"
    banned = "да" if profile.banned else "нет"
    lines = [
        "<b>FunPay-профиль</b>",
        "",
        "<b>Golden key:</b> действителен",
        f'<b>Ник:</b> <a href="{profile.profile_url}">{username}</a>',
        f"<b>ID:</b> <code>{profile.user_id}</code>",
        f"<b>Статус:</b> {status}",
        f"<b>Заблокирован:</b> {banned}",
        f"<b>Активные продажи:</b> {profile.active_sales}",
        f"<b>Активные покупки:</b> {profile.active_purchases}",
        "",
        "<b>Баланс</b>",
    ]
    if profile.balance:
        balance = profile.balance
        lines.extend(
            [
                f"RUB: {money(balance.total_rub, 'RUB')} "
                f"(доступно {money(balance.available_rub, 'RUB')})",
                f"USD: {money(balance.total_usd, 'USD')} "
                f"(доступно {money(balance.available_usd, 'USD')})",
                f"EUR: {money(balance.total_eur, 'EUR')} "
                f"(доступно {money(balance.available_eur, 'EUR')})",
            ]
        )
    else:
        lines.append(
            f"Не удалось получить: {html.escape(profile.balance_error or 'нет данных')}"
        )
    if profile.avatar_url:
        lines.extend(["", f'<a href="{html.escape(profile.avatar_url)}">Аватар</a>'])
    return "\n".join(lines)


def build_delivery_rows(
    lots: list[Any], rules: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    rows = []
    current_ids = set()
    for lot in lots:
        metadata = AutoDeliveryService.lot_metadata(lot)
        lot_id = metadata["lot_id"]
        current_ids.add(lot_id)
        metadata["enabled"] = lot_id in rules
        metadata["active"] = True
        rows.append(metadata)
    for lot_id, rule in rules.items():
        if lot_id in current_ids:
            continue
        stale = dict(rule)
        stale["lot_id"] = lot_id
        stale["enabled"] = True
        stale["active"] = False
        rows.append(stale)
    return sorted(
        rows,
        key=lambda row: (
            not bool(row.get("active")),
            str(row.get("title") or "").casefold(),
            str(row.get("lot_id") or ""),
        ),
    )


def build_lots_csv(lots: list[Any]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\n")
    writer.writerow(["ID", "Название", "Категория", "Подкатегория", "Цена", "Ссылка"])
    for lot in lots:
        metadata = AutoDeliveryService.lot_metadata(lot)
        writer.writerow(
            [
                metadata["lot_id"],
                metadata["title"],
                metadata["category"],
                metadata["subcategory"],
                metadata["price"],
                metadata["link"],
            ]
        )
    return output.getvalue().encode("utf-8-sig")


def format_delivery_detail(row: dict[str, Any], rule: dict[str, Any] | None) -> str:
    active = "активен" if row.get("active") else "не найден среди активных"
    lines = [
        "<b>Автовыдача лота</b>",
        "",
        f"<b>ID:</b> <code>{html.escape(str(row['lot_id']))}</code>",
        f"<b>Название:</b> {html.escape(str(row.get('title') or 'Без названия'))}",
        f"<b>Категория:</b> {html.escape(str(row.get('category') or '—'))}",
        f"<b>Статус лота:</b> {active}",
        f"<b>Автовыдача:</b> {'включена' if rule else 'выключена'}",
    ]
    if rule:
        lines.extend(
            [
                "",
                "<b>Текст выдачи:</b>",
                f"<code>{html.escape(str(rule.get('text') or ''))}</code>",
            ]
        )
    return "\n".join(lines)


def format_funpay_message(message: Any, account_id: int | None) -> str:
    incoming = getattr(message, "author_id", None) != account_id
    author = html.escape(str(getattr(message, "author", None) or "FunPay"))
    chat_name = html.escape(
        str(getattr(message, "chat_name", None) or getattr(message, "chat_id", "—"))
    )
    body = str(getattr(message, "text", None) or "").strip()
    image_link = str(getattr(message, "image_link", None) or "").strip()
    badge = str(getattr(message, "badge", None) or "").strip()
    if body:
        body_text = html.escape(body[:3000] + ("..." if len(body) > 3000 else ""))
    elif image_link:
        body_text = f'<a href="{html.escape(image_link)}">Изображение</a>'
    else:
        body_text = "Сообщение без текста"
    badge_text = f" · {html.escape(badge)}" if badge else ""
    title = f"👤 <b>{author}</b>{badge_text}" if incoming else f"📤 <b>Вы → {chat_name}</b>"
    return f"{title}\n{body_text}"


def funpay_chat_menu(chat_id: Any) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Ответить",
        callback_data=f"fp:reply:{chat_id}",
    )
    builder.button(
        text="Открыть чат",
        url=f"https://funpay.com/chat/?node={chat_id}",
    )
    builder.adjust(2)
    return builder.as_markup()


async def send_funpay_notification(
    bot: Bot,
    owner_id: int,
    message: Any,
    account_id: int | None,
) -> None:
    await bot.send_message(
        owner_id,
        format_funpay_message(message, account_id),
        reply_markup=funpay_chat_menu(getattr(message, "chat_id", "")),
    )


async def send_delivery_result(
    bot: Bot, owner_id: int, result: DeliveryResult
) -> None:
    if result.status == "delivered":
        text = (
            "<b>Автовыдача выполнена</b>\n\n"
            f"Заказ: <code>#{html.escape(result.order_id)}</code>\n"
            f"Лот: <code>{html.escape(str(result.lot_id or '—'))}</code>\n"
            f"Покупатель: {html.escape(str(result.buyer_username or '—'))}"
        )
    elif result.status == "ambiguous":
        text = (
            "<b>Автовыдача не выполнена</b>\n\n"
            f"Заказ: <code>#{html.escape(result.order_id)}</code>\n"
            "Несколько настроенных лотов имеют одинаковое название и категорию."
        )
    else:
        text = (
            "<b>Ошибка автовыдачи</b>\n\n"
            f"Заказ: <code>#{html.escape(result.order_id)}</code>\n"
            f"Причина: {html.escape(str(result.error or 'неизвестная ошибка'))}"
        )
    await bot.send_message(owner_id, text)


def format_cooldown(seconds: int) -> str:
    if seconds == 0:
        return "без задержки"
    if seconds % 3600 == 0:
        return f"{seconds // 3600} ч"
    if seconds % 60 == 0:
        return f"{seconds // 60} мин"
    return f"{seconds} сек"


def parse_cooldown(value: str) -> int | None:
    match = re.fullmatch(r"\s*(\d+)\s*([smhсмч]?)\s*", value.lower())
    if not match:
        return None
    amount = int(match.group(1))
    multiplier = {
        "": 1,
        "s": 1,
        "с": 1,
        "m": 60,
        "м": 60,
        "h": 3600,
        "ч": 3600,
    }[match.group(2)]
    seconds = amount * multiplier
    return seconds if 0 <= seconds <= 86400 else None


def format_message_settings(automation: AutomationService) -> str:
    settings = automation.get_settings()["messages"]
    status = "включён" if settings["enabled"] else "выключен"
    variables = {
        "username": "имя отправителя",
        "chat_name": "название чата",
        "message": "текст сообщения",
        "command": "команда покупателя",
        "account": "ваш ник FunPay",
        "chat_id": "ID чата",
    }
    variable_lines = "\n".join(
        f"<code>{{{name}}}</code> — {variables[name]}" for name in MESSAGE_VARIABLES
    )
    template = html.escape(str(settings["template"]))
    command_count = len(settings.get("commands", {}))
    return (
        "<b>Ответ на сообщения</b>\n\n"
        f"<b>Статус:</b> {status}\n"
        f"<b>КД для каждого чата:</b> {format_cooldown(int(settings['cooldown_seconds']))}\n\n"
        f"<b>Команд:</b> {command_count}\n\n"
        f"<b>Шаблон:</b>\n<code>{template}</code>\n\n"
        f"<b>Переменные:</b>\n{variable_lines}\n\n"
        "Автоответ отправляется только на обычные входящие сообщения покупателей."
    )


def format_review_settings(automation: AutomationService) -> str:
    settings = automation.get_settings()["reviews"]
    status = "включён" if settings["enabled"] else "выключен"
    variables = {
        "username": "имя покупателя",
        "order_id": "номер заказа",
        "lot": "название купленного лота",
        "description": "полное описание лота",
        "sum": "сумма заказа",
        "stars": "количество звёзд",
        "review": "текст отзыва",
        "category": "категория лота",
        "game": "игра или раздел",
        "account": "ваш ник FunPay",
    }
    variable_lines = "\n".join(
        f"<code>{{{name}}}</code> — {variables[name]}" for name in REVIEW_VARIABLES
    )
    template_lines = []
    for stars in range(1, 6):
        template = str(settings["templates"][str(stars)])
        preview = template[:140] + ("..." if len(template) > 140 else "")
        template_lines.append(f"<b>{stars} зв.:</b> <code>{html.escape(preview)}</code>")
    return (
        "<b>Ответ на отзывы</b>\n\n"
        f"<b>Статус:</b> {status}\n\n"
        + "\n".join(template_lines)
        + f"\n\n<b>Переменные:</b>\n{variable_lines}\n\n"
        "Для каждой оценки используется отдельный шаблон. Существующие ответы не изменяются."
    )


def format_notification_settings(automation: AutomationService) -> str:
    settings = automation.get_settings()["notifications"]
    labels = {
        "incoming_messages": "Входящие сообщения покупателей",
        "outgoing_messages": "Ваши исходящие сообщения",
        "orders": "Покупки и изменения заказов",
        "reviews": "Новые и изменённые отзывы",
        "refunds": "Полные и частичные возвраты",
        "other_system": "Остальные системные сообщения",
    }
    master = "включены" if settings["enabled"] else "выключены"
    lines = ["<b>Уведомления FunPay</b>", "", f"<b>Все уведомления:</b> {master}", ""]
    for key in NOTIFICATION_KEYS:
        status = "вкл." if settings[key] else "выкл."
        lines.append(f"<b>{labels[key]}:</b> {status}")
    lines.extend(
        [
            "",
            "Общий переключатель временно отключает всё, не изменяя выбранные категории.",
        ]
    )
    return "\n".join(lines)


def format_system_settings(system_settings: SystemSettingsService) -> str:
    settings = system_settings.get_settings()
    logs_status = "включены" if settings["logs_enabled"] else "выключены"
    return (
        "<b>Системные настройки</b>\n\n"
        f"<b>Логи:</b> {logs_status}\n"
        f"<b>Автоочистка:</b> при размере файла больше "
        f"{settings['max_log_size_mb']} МБ\n"
        "<b>Проверка:</b> один раз в час\n\n"
        "Бот также проверяет RAM и диск каждые 5 минут и предупреждает при загрузке от 90%."
    )


def format_auto_ticket_settings(auto_tickets: AutoTicketService) -> str:
    settings = auto_tickets.get_settings()
    database = auto_tickets.get_database_stats()
    status = "включены" if settings["enabled"] else "выключены"
    scope = (
        "все неподтверждённые заказы"
        if settings["scope"] == "all"
        else "только заказы автовыдачи FunPay или MoneyBot"
    )
    last_check_at = auto_tickets.get_last_check_at()
    last_check = (
        time.strftime("%d.%m.%Y %H:%M", time.localtime(last_check_at))
        if last_check_at
        else "ещё не выполнялась"
    )
    template = html.escape(str(settings["message_template"]))
    return (
        "<b>Настройки автотикетов</b>\n\n"
        f"<b>Статус:</b> {status}\n"
        f"<b>Область:</b> {scope}\n"
        f"<b>Без подтверждения:</b> {settings['delay_hours']} ч\n"
        f"<b>Интервал проверки:</b> {settings['check_interval_minutes']} мин\n"
        f"<b>Заказов в тикете:</b> до {settings['max_orders_per_ticket']}\n"
        f"<b>Последняя проверка:</b> {last_check}\n\n"
        f"<b>База SQLite:</b> ожидают {database['pending']}, "
        f"отправлены {database['sent']}, отменены {database['cancelled']}\n\n"
        f"<b>Текст:</b>\n<code>{template}</code>\n\n"
        "Перед каждой отправкой бот обязательно запросит подтверждение."
    )


def format_auto_ticket_result(result: AutoTicketResult) -> str:
    if result.status == "pending":
        orders = ", ".join(f"#{order_id}" for order_id in result.order_ids)
        return (
            "<b>Отправить автотикет?</b>\n\n"
            f"Заказы: {html.escape(orders)}\n"
            "Support будет вызван только после нажатия «Отправить»."
        )
    if result.status == "sent":
        orders = ", ".join(f"#{order_id}" for order_id in result.order_ids)
        ticket = f" #{html.escape(result.ticket_id)}" if result.ticket_id else ""
        return f"<b>Автотикет{ticket} отправлен</b>\nЗаказы: {html.escape(orders)}"
    if result.status == "empty":
        return "Подходящих неподтверждённых заказов не найдено."
    if result.status == "disabled":
        return "Сначала включите автотикеты."
    if result.status == "busy":
        return "Проверка уже выполняется."
    if result.status == "cancelled":
        return "Автотикет отменён. Заказы сохранены в базе как отменённые."
    if result.status == "missing":
        return "Подтверждение не найдено в базе."
    if result.status == "sending":
        return "Этот автотикет уже отправляется."
    return "<b>Ошибка автотикета</b>\n" + html.escape(result.error or "неизвестная ошибка")


def format_cleanup_result(result: CleanupResult) -> str:
    text = (
        f"Очищено файлов: {result.files_cleaned}\n"
        f"Освобождено: {format_bytes(result.bytes_freed)}"
    )
    if result.files_failed:
        text += f"\nНе удалось очистить: {result.files_failed}"
    return text


def format_temporary_cleanup_result(result: TemporaryCleanupResult) -> str:
    text = (
        "<b>Временные файлы очищены</b>\n\n"
        f"Каталогов кеша: {result.cache_directories_cleaned}\n"
        f"Файлов кеша: {result.cache_files_cleaned}\n"
        f"Файлов логов: {result.log_files_cleaned}\n"
        f"Освобождено: {format_bytes(result.bytes_freed)}"
    )
    if result.files_failed:
        text += f"\nНе удалось очистить: {result.files_failed}"
    return text


def format_resource_warning(warning: ResourceWarning) -> str:
    reasons = ", ".join(warning.reasons)
    return (
        "<b>На сервере осталось мало ресурсов</b>\n\n"
        f"Проблема: {html.escape(reasons)}\n"
        f"RAM занято: {warning.memory_percent:.1f}%\n"
        f"RAM доступно: {format_bytes(warning.memory_available)}\n"
        f"Диск {html.escape(warning.disk_path)} занят: {warning.disk_percent:.1f}%\n"
        f"На диске свободно: {format_bytes(warning.disk_free)}\n\n"
        "Кнопка очистит кеши и логи MoneyBot. Системные и чужие файлы не удаляются."
    )


def format_update_release(release: ReleaseInfo, current_version: str) -> str:
    notes = html.escape(release.notes[:2200]) if release.notes else "Описание релиза отсутствует."
    return (
        f"<b>Вышло обновление MoneyBot {html.escape(release.version)}</b>\n\n"
        f"Текущая версия: <code>{html.escape(current_version)}</code>\n"
        f"Новая версия: <code>{html.escape(release.version)}</code>\n"
        f"<b>{html.escape(release.name)}</b>\n\n"
        f"{notes}"
    )


def format_update_check(result: UpdateCheckResult) -> str:
    if result.error:
        return "<b>Не удалось проверить обновления</b>\n" + html.escape(result.error)
    if not result.update_available or result.latest is None:
        return (
            "<b>Обновлений нет</b>\n"
            f"Установлена актуальная версия <code>{html.escape(result.current_version)}</code>."
        )
    return format_update_release(result.latest, result.current_version)


def format_update_install(result: UpdateInstallResult) -> str:
    if result.status == "busy":
        return "Обновление уже выполняется."
    if result.status == "dirty":
        return (
            "<b>Обновление остановлено</b>\n"
            f"{html.escape(result.error)}. Сначала сохраните или отмените локальные изменения."
        )
    if result.status == "installed":
        return (
            "<b>Обновление установлено</b>\n"
            f"Версия: <code>{html.escape(result.version or 'новая')}</code>\n"
            "Бот перезапускается..."
        )
    if result.status == "current":
        return (
            "<b>Обновлений нет</b>\n"
            f"Установлена актуальная версия <code>{html.escape(result.version)}</code>."
        )
    return "<b>Не удалось установить обновление</b>\n" + html.escape(
        result.error or "неизвестная ошибка"
    )


def format_raise_lots_result(result: RaiseLotsResult) -> str:
    if result.status == "busy":
        return "Поднятие лотов уже выполняется."
    if result.status == "empty":
        return "Активные лоты не найдены."

    lines = [
        "<b>Поднятие лотов завершено</b>",
        "",
        f"Активных лотов: {result.total_lots}",
        f"Категорий поднято: {result.categories_raised} из {result.categories_total}",
    ]
    if result.raised_categories:
        lines.extend(["", "<b>Поднятые категории:</b>"])
        lines.extend(f"• {html.escape(category)}" for category in result.raised_categories)
    if result.errors:
        lines.extend(["", "<b>Не удалось поднять:</b>"])
        for category, reason in result.errors[:10]:
            lines.append(f"• {html.escape(category)}: {html.escape(reason)}")
        if len(result.errors) > 10:
            lines.append(f"• и ещё {len(result.errors) - 10}")
    return "\n".join(lines)


def format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if size < 1024 or unit == "ТБ":
            decimals = 0 if unit == "Б" else 1
            return f"{size:.{decimals}f} {unit}"
        size /= 1024
    return f"{size:.1f} ТБ"


def format_duration(seconds: int) -> str:
    days, remainder = divmod(max(seconds, 0), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days} д")
    if hours or days:
        parts.append(f"{hours} ч")
    parts.append(f"{minutes} мин")
    return " ".join(parts)


def collect_system_info() -> str:
    process = psutil.Process()
    process.cpu_percent(None)
    cpu_percent = psutil.cpu_percent(interval=0.25)
    process_cpu = process.cpu_percent(None)
    memory = psutil.virtual_memory()
    disk_path = Path.cwd().anchor or "/"
    disk = psutil.disk_usage(disk_path)
    system_uptime = int(time.time() - psutil.boot_time())
    process_uptime = int(time.time() - process.create_time())
    process_memory = process.memory_info().rss
    physical_cores = psutil.cpu_count(logical=False) or 0
    logical_cores = psutil.cpu_count(logical=True) or 0
    return (
        "<b>Система</b>\n\n"
        f"<b>ОС:</b> {html.escape(platform.system())} {html.escape(platform.release())}\n"
        f"<b>Python:</b> {html.escape(platform.python_version())}\n"
        f"<b>Аптайм системы:</b> {format_duration(system_uptime)}\n\n"
        "<b>Процессор</b>\n"
        f"Загрузка: {cpu_percent:.1f}%\n"
        f"Ядра: {physical_cores} физических / {logical_cores} логических\n\n"
        "<b>Оперативная память</b>\n"
        f"Использовано: {format_bytes(memory.used)} из {format_bytes(memory.total)} "
        f"({memory.percent:.1f}%)\n"
        f"Доступно: {format_bytes(memory.available)}\n\n"
        f"<b>Диск {html.escape(disk_path)}</b>\n"
        f"Использовано: {format_bytes(disk.used)} из {format_bytes(disk.total)} "
        f"({disk.percent:.1f}%)\n"
        f"Свободно: {format_bytes(disk.free)}\n\n"
        "<b>Процесс бота</b>\n"
        f"Память: {format_bytes(process_memory)}\n"
        f"CPU: {process_cpu:.1f}%\n"
        f"Работает: {format_duration(process_uptime)}"
    )


def build_log_archive(files: list[Path]) -> tuple[bytes, int, int]:
    output = io.BytesIO()
    count = 0
    source_size = 0
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            try:
                data = path.read_bytes()
            except OSError:
                continue
            if not data:
                continue
            try:
                archive_name = str(path.relative_to(Path.cwd())).replace("\\", "/")
            except ValueError:
                archive_name = path.name
            archive.writestr(archive_name, data)
            count += 1
            source_size += len(data)
    return output.getvalue(), count, source_size


def support_error(error: Exception) -> str:
    if isinstance(error, SupportRateLimitedError):
        return str(error)
    text = str(error).splitlines()[0].strip()
    return text[:300] if text else type(error).__name__


async def show_main(target: Message) -> None:
    await target.edit_text("<b>Главное меню</b>", reply_markup=main_menu())


@router.message(CommandStart())
@router.message(Command("menu"))
async def command_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("<b>Главное меню</b>", reply_markup=main_menu())


@router.message(Command("profile"))
async def command_profile(message: Message, funpay: FunPayService) -> None:
    wait = await message.answer("Обновляю данные FunPay...")
    await render_account(wait, funpay)


@router.message(Command("tickets"))
async def command_tickets(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("<b>Тикеты FunPay Support</b>", reply_markup=tickets_menu())


@router.message(Command("system"))
async def command_system(message: Message) -> None:
    wait = await message.answer("Собираю информацию о системе...")
    try:
        text = await asyncio.to_thread(collect_system_info)
    except Exception as error:
        text = (
            "<b>Не удалось получить информацию о системе</b>\n"
            + html.escape(str(error).splitlines()[0][:300])
        )
    await wait.edit_text(text)


@router.message(Command("update"))
async def command_update(message: Message, update_service: UpdateService) -> None:
    wait = await message.answer("Проверяю обновления MoneyBot...")
    result = await asyncio.to_thread(update_service.check)
    markup = update_menu(result.latest) if result.update_available and result.latest else None
    await wait.edit_text(format_update_check(result), reply_markup=markup)


@router.message(Command("log"))
async def command_log(
    message: Message, system_settings: SystemSettingsService
) -> None:
    wait = await message.answer("Собираю логи...")
    files = system_settings.get_log_files()
    archive, count, source_size = await asyncio.to_thread(build_log_archive, files)
    if count == 0:
        await wait.edit_text("Лог-файлы пока пусты или не найдены.")
        return
    if len(archive) > 49 * 1024 * 1024:
        await wait.edit_text(
            "Архив логов больше 49 МБ. Очистите логи в системных настройках "
            "или уменьшите лимит автоочистки."
        )
        return
    document = BufferedInputFile(archive, filename="moneybot_logs.zip")
    await message.answer_document(
        document,
        caption=(
            f"Логов в архиве: {count}\n"
            f"Размер до сжатия: {format_bytes(source_size)}\n"
            f"Размер ZIP: {format_bytes(len(archive))}"
        ),
    )
    await wait.delete()


@router.callback_query(F.data == "update:install")
async def callback_update_install(
    callback: CallbackQuery, update_service: UpdateService
) -> None:
    await callback.answer("Устанавливаю обновление...")
    if not callback.message:
        return
    await callback.message.edit_text(
        "<b>Устанавливаю обновление</b>\n"
        "Проверяю Git, загружаю код и зависимости..."
    )
    result = await asyncio.to_thread(update_service.install)
    await callback.message.edit_text(format_update_install(result))
    if result.status != "installed":
        return
    await asyncio.sleep(1)
    try:
        update_service.schedule_restart()
    except Exception as error:
        logger.exception("Не удалось запланировать перезапуск после обновления")
        await callback.message.edit_text(
            "<b>Обновление установлено, но автоперезапуск не выполнен</b>\n"
            + html.escape(str(error).splitlines()[0][:300])
            + "\nПерезапустите службу MoneyBot вручную."
        )
        return
    await asyncio.sleep(1)
    os._exit(0)


@router.callback_query(F.data.startswith("fp:reply:"))
async def callback_funpay_reply(callback: CallbackQuery, state: FSMContext) -> None:
    chat_id = str(callback.data or "").split(":", maxsplit=2)[-1]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", chat_id):
        await callback.answer("Некорректный ID чата", show_alert=True)
        return
    await callback.answer()
    await state.set_state(FunPayReply.waiting_text)
    await state.update_data(funpay_reply_chat_id=chat_id)
    builder = InlineKeyboardBuilder()
    builder.button(text="Отмена", callback_data="fp:reply_cancel")
    if callback.message:
        await callback.message.answer(
            "<b>Ответ в FunPay</b>\n\nОтправьте текст ответа одним сообщением.",
            reply_markup=builder.as_markup(),
        )


@router.callback_query(F.data == "fp:reply_cancel")
async def callback_funpay_reply_cancel(
    callback: CallbackQuery, state: FSMContext
) -> None:
    await callback.answer("Отменено")
    await state.clear()
    if callback.message:
        await callback.message.edit_text("Ответ отменён.")


@router.message(FunPayReply.waiting_text, F.text)
async def receive_funpay_reply(
    message: Message, state: FSMContext, plugin_manager: PluginManager
) -> None:
    text = str(message.text or "").strip()
    if not text:
        await message.answer("Ответ не может быть пустым.")
        return
    if len(text) > 4000:
        await message.answer("Ответ слишком длинный. Максимум 4000 символов.")
        return
    data = await state.get_data()
    chat_id = str(data.get("funpay_reply_chat_id") or "")
    if not plugin_manager.account:
        await message.answer("FunPay-аккаунт не инициализирован.")
        return
    try:
        await asyncio.to_thread(plugin_manager.account.send_message, chat_id, text)
    except Exception as error:
        reason = html.escape(str(error).splitlines()[0][:300])
        await message.answer(f"<b>Ответ не отправлен</b>\n{reason}")
        return
    await state.clear()
    await message.answer(
        "Ответ отправлен в FunPay.", reply_markup=funpay_chat_menu(chat_id)
    )


@router.callback_query(F.data == "menu:main")
async def callback_main(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    if callback.message:
        await show_main(callback.message)


@router.callback_query(F.data == "menu:account")
async def callback_account_menu(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "<b>FunPay</b>\n\nВыберите раздел.",
            reply_markup=account_root_menu(),
        )


@router.callback_query(F.data == "account:info")
@router.callback_query(F.data == "account:refresh")
async def callback_account(callback: CallbackQuery, funpay: FunPayService) -> None:
    await callback.answer("Обновляю...")
    if callback.message:
        await callback.message.edit_text("Обновляю данные FunPay...")
        await render_account(callback.message, funpay)


async def render_account(message: Message, funpay: FunPayService) -> None:
    try:
        profile = await asyncio.to_thread(funpay.get_profile)
        text = format_profile(profile)
    except FunPayServiceError as error:
        text = f"<b>Ошибка FunPay</b>\n{html.escape(str(error))}"
    await message.edit_text(text, reply_markup=account_menu())


@router.callback_query(F.data == "account:restrictions")
async def callback_account_restrictions(
    callback: CallbackQuery, plugin_manager: PluginManager
) -> None:
    await callback.answer("Обновляю...")
    if not callback.message:
        return
    await callback.message.edit_text("Получаю ограничения рейтинга FunPay...")
    try:
        restrictions = await asyncio.to_thread(
            plugin_manager.get_rating_restrictions
        )
        username = (
            plugin_manager.account.username
            if plugin_manager.account and plugin_manager.account.username
            else "неизвестно"
        )
        lines = [
            "<b>Ограничения рейтинга</b>",
            f"👤 <b>Аккаунт:</b> {html.escape(username)}",
            "",
        ]
        if restrictions:
            for item in restrictions:
                lines.extend(
                    [
                        f"<b>{html.escape(item['section'])}</b>",
                        html.escape(item["restriction"]),
                        "",
                    ]
                )
        else:
            lines.append("Ограничений рейтинга нет.")
        text = "\n".join(lines).strip()
    except Exception as error:
        reason = html.escape(str(error).splitlines()[0][:300])
        text = f"<b>Не удалось получить ограничения</b>\n{reason}"
    await callback.message.edit_text(text, reply_markup=restrictions_menu())


@router.callback_query(F.data == "account:lots")
async def callback_account_lots(
    callback: CallbackQuery,
    state: FSMContext,
    auto_delivery: AutoDeliveryService,
) -> None:
    await callback.answer("Загружаю...")
    await state.clear()
    if not callback.message:
        return
    try:
        lots = await asyncio.to_thread(auto_delivery.get_lots, True)
        configured = len(auto_delivery.get_rules())
        text = (
            "<b>Лоты FunPay</b>\n\n"
            f"Активных лотов: {len(lots)}\n"
            f"Настроено для автовыдачи: {configured}"
        )
    except Exception as error:
        text = (
            "<b>Не удалось получить лоты</b>\n"
            + html.escape(str(error).splitlines()[0][:300])
        )
    await callback.message.edit_text(text, reply_markup=lots_menu())


@router.callback_query(F.data == "account:lots:download")
async def callback_lots_download(
    callback: CallbackQuery, auto_delivery: AutoDeliveryService
) -> None:
    await callback.answer("Готовлю файл...")
    if not callback.message:
        return
    try:
        lots = await asyncio.to_thread(auto_delivery.get_lots, True)
        document = BufferedInputFile(build_lots_csv(lots), filename="funpay_lots.csv")
        await callback.message.answer_document(
            document,
            caption=f"Лоты FunPay: {len(lots)} шт. Названия и ID находятся в CSV-файле.",
        )
    except Exception as error:
        await callback.message.answer(
            "<b>Файл не создан</b>\n"
            + html.escape(str(error).splitlines()[0][:300])
        )


@router.callback_query(F.data == "account:delivery:noop")
async def callback_delivery_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith("account:delivery:page:"))
async def callback_delivery_page(
    callback: CallbackQuery,
    state: FSMContext,
    auto_delivery: AutoDeliveryService,
) -> None:
    page_text = str(callback.data or "").rsplit(":", maxsplit=1)[-1]
    page = int(page_text) if page_text.isdigit() else 0
    await callback.answer("Загружаю...")
    await state.clear()
    if not callback.message:
        return
    try:
        lots = await asyncio.to_thread(auto_delivery.get_lots)
        rows = build_delivery_rows(lots, auto_delivery.get_rules())
        total_pages = max(1, (len(rows) + 7) // 8)
        page = min(page, total_pages - 1)
        text = (
            "<b>Автовыдача</b>\n\n"
            "Выберите лот. 🟢 — настроен, 🔴 — выключен.\n"
            "После новой покупки настроенный текст будет отправлен покупателю один раз."
        )
        await callback.message.edit_text(
            text, reply_markup=delivery_list_menu(rows, page)
        )
    except Exception as error:
        await callback.message.edit_text(
            "<b>Не удалось получить лоты</b>\n"
            + html.escape(str(error).splitlines()[0][:300]),
            reply_markup=lots_menu(),
        )


@router.callback_query(F.data.regexp(r"^account:delivery:lot:[^:]+:\d+$"))
async def callback_delivery_lot(
    callback: CallbackQuery,
    state: FSMContext,
    auto_delivery: AutoDeliveryService,
) -> None:
    parts = str(callback.data or "").split(":")
    lot_id, page = parts[-2], int(parts[-1])
    await callback.answer()
    await state.clear()
    if not callback.message:
        return
    lots = await asyncio.to_thread(auto_delivery.get_lots)
    rows = build_delivery_rows(lots, auto_delivery.get_rules())
    row = next((item for item in rows if item["lot_id"] == lot_id), None)
    if row is None:
        await callback.message.edit_text(
            "Лот не найден.", reply_markup=delivery_list_menu(rows, page)
        )
        return
    rule = auto_delivery.get_rule(lot_id)
    await callback.message.edit_text(
        format_delivery_detail(row, rule),
        reply_markup=delivery_detail_menu(lot_id, page, rule is not None),
    )


@router.callback_query(F.data.regexp(r"^account:delivery:set:[^:]+:\d+$"))
async def callback_delivery_set(
    callback: CallbackQuery,
    state: FSMContext,
    auto_delivery: AutoDeliveryService,
) -> None:
    parts = str(callback.data or "").split(":")
    lot_id, page = parts[-2], int(parts[-1])
    lots = await asyncio.to_thread(auto_delivery.get_lots)
    lot = next((item for item in lots if str(item.id) == lot_id), None)
    rule = auto_delivery.get_rule(lot_id)
    if lot is None and rule is None:
        await callback.answer("Лот не найден", show_alert=True)
        return
    await callback.answer()
    await state.set_state(AutoDeliverySettings.waiting_text)
    await state.update_data(delivery_lot_id=lot_id, delivery_page=page)
    variables = " ".join(f"<code>{{{name}}}</code>" for name in DELIVERY_VARIABLES)
    if callback.message:
        await callback.message.edit_text(
            "<b>Текст автовыдачи</b>\n\n"
            "Отправьте строку, которая будет автоматически выдана покупателю. "
            "Максимум 4000 символов.\n\n"
            f"<b>Переменные:</b>\n{variables}",
            reply_markup=delivery_cancel_menu(lot_id, page),
        )


@router.message(AutoDeliverySettings.waiting_text, F.text)
async def receive_delivery_text(
    message: Message,
    state: FSMContext,
    auto_delivery: AutoDeliveryService,
) -> None:
    text = str(message.text or "").strip()
    if not text:
        await message.answer("Текст автовыдачи не может быть пустым.")
        return
    if len(text) > 4000:
        await message.answer("Текст слишком длинный. Максимум 4000 символов.")
        return
    data = await state.get_data()
    lot_id = str(data.get("delivery_lot_id") or "")
    page = int(data.get("delivery_page") or 0)
    lots = await asyncio.to_thread(auto_delivery.get_lots)
    lot = next((item for item in lots if str(item.id) == lot_id), None)
    if lot is not None:
        auto_delivery.set_rule(lot, text)
        row = AutoDeliveryService.lot_metadata(lot)
        row.update({"active": True, "enabled": True})
    elif auto_delivery.update_rule_text(lot_id, text):
        row = dict(auto_delivery.get_rule(lot_id) or {})
        row.update({"lot_id": lot_id, "active": False, "enabled": True})
    else:
        await state.clear()
        await message.answer("Лот больше не найден. Откройте список заново.")
        return
    await state.clear()
    rule = auto_delivery.get_rule(lot_id)
    await message.answer(
        format_delivery_detail(row, rule),
        reply_markup=delivery_detail_menu(lot_id, page, True),
    )


@router.callback_query(F.data.regexp(r"^account:delivery:disable:[^:]+:\d+$"))
async def callback_delivery_disable(
    callback: CallbackQuery,
    state: FSMContext,
    auto_delivery: AutoDeliveryService,
) -> None:
    parts = str(callback.data or "").split(":")
    lot_id, page = parts[-2], int(parts[-1])
    auto_delivery.delete_rule(lot_id)
    await state.clear()
    await callback.answer("Автовыдача отключена")
    if callback.message:
        lots = await asyncio.to_thread(auto_delivery.get_lots)
        rows = build_delivery_rows(lots, auto_delivery.get_rules())
        await callback.message.edit_text(
            "<b>Автовыдача</b>\n\nВыберите лот.",
            reply_markup=delivery_list_menu(rows, page),
        )


@router.callback_query(F.data.in_({"menu:plugins", "plugins:refresh"}))
async def callback_plugins(
    callback: CallbackQuery,
    state: FSMContext,
    plugin_manager: PluginManager,
) -> None:
    await callback.answer()
    await state.clear()
    if callback.message:
        enabled = sum(1 for item in plugin_manager.plugins.values() if item.enabled)
        await callback.message.edit_text(
            f"<b>Плагины Cardinal</b>\n\n"
            f"Найдено: {len(plugin_manager.plugins)}\n"
            f"Включено: {enabled}\n\n"
            "Новые плагины отключены до ручного включения.",
            reply_markup=plugin_list_menu(plugin_manager),
        )


@router.callback_query(F.data == "plugins:add")
async def callback_plugin_add(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(PluginUpload.waiting_file)
    builder = InlineKeyboardBuilder()
    builder.button(text="Отмена", callback_data="plugins:add:cancel")
    if callback.message:
        await callback.message.edit_text(
            "<b>Добавление плагина</b>\n\n"
            "Отправьте плагин документом в формате <code>.py</code>. "
            "Максимальный размер — 2 МБ. После проверки плагин будет добавлен выключенным.",
            reply_markup=builder.as_markup(),
        )


@router.callback_query(F.data == "plugins:add:cancel")
async def callback_plugin_add_cancel(
    callback: CallbackQuery,
    state: FSMContext,
    plugin_manager: PluginManager,
) -> None:
    await callback.answer("Отменено")
    await state.clear()
    if callback.message:
        enabled = sum(1 for item in plugin_manager.plugins.values() if item.enabled)
        await callback.message.edit_text(
            f"<b>Плагины Cardinal</b>\n\n"
            f"Найдено: {len(plugin_manager.plugins)}\n"
            f"Включено: {enabled}",
            reply_markup=plugin_list_menu(plugin_manager),
        )


@router.message(PluginUpload.waiting_file, F.document)
async def receive_plugin_file(
    message: Message,
    state: FSMContext,
    bot: Bot,
    plugin_manager: PluginManager,
) -> None:
    document = message.document
    if document is None:
        return
    file_name = str(document.file_name or "plugin.py")
    if not file_name.lower().endswith(".py"):
        await message.answer("Нужен файл с расширением <code>.py</code>.")
        return
    if document.file_size and document.file_size > 2 * 1024 * 1024:
        await message.answer("Файл слишком большой. Максимальный размер — 2 МБ.")
        return
    wait = await message.answer("Проверяю плагин...")
    buffer = io.BytesIO()
    try:
        await bot.download(document.file_id, destination=buffer)
        record = await asyncio.to_thread(
            plugin_manager.install_plugin, file_name, buffer.getvalue()
        )
    except Exception as error:
        reason = html.escape(str(error).splitlines()[0][:500])
        await wait.edit_text(f"<b>Плагин не добавлен</b>\n{reason}")
        return
    await state.clear()
    text, markup = plugin_detail(record)
    await wait.edit_text(
        "<b>Плагин добавлен и выключен.</b>\n\n" + text,
        reply_markup=markup,
    )


@router.message(PluginUpload.waiting_file)
async def receive_plugin_file_invalid(message: Message) -> None:
    await message.answer("Отправьте Python-плагин документом с расширением <code>.py</code>.")


@router.callback_query(F.data.startswith("plugin:view:"))
async def callback_plugin_view(
    callback: CallbackQuery, plugin_manager: PluginManager
) -> None:
    await callback.answer()
    uuid = str(callback.data or "").split(":", maxsplit=2)[-1]
    plugin = plugin_manager.plugins.get(uuid)
    if not plugin or not callback.message:
        return
    text, markup = plugin_detail(plugin)
    await callback.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith(f"{CARDINAL_CBT.PLUGINS_LIST}:"))
async def callback_cardinal_plugins_list(
    callback: CallbackQuery, plugin_manager: PluginManager
) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "<b>Плагины Cardinal</b>",
            reply_markup=plugin_list_menu(plugin_manager),
        )


@router.callback_query(F.data.startswith(f"{CARDINAL_CBT.EDIT_PLUGIN}:"))
async def callback_cardinal_plugin_view(
    callback: CallbackQuery, plugin_manager: PluginManager
) -> None:
    parts = str(callback.data or "").split(":")
    uuid = parts[1] if len(parts) > 1 else ""
    plugin = plugin_manager.plugins.get(uuid)
    if not plugin or not callback.message:
        await callback.answer("Плагин не найден", show_alert=True)
        return
    await callback.answer()
    text, markup = plugin_detail(plugin)
    await callback.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith(f"{CARDINAL_CBT.TOGGLE_PLUGIN}:"))
async def callback_cardinal_plugin_toggle(
    callback: CallbackQuery, plugin_manager: PluginManager, bot: Bot
) -> None:
    parts = str(callback.data or "").split(":")
    uuid = parts[1] if len(parts) > 1 else ""
    if uuid not in plugin_manager.plugins or not callback.message:
        await callback.answer("Плагин не найден", show_alert=True)
        return
    await callback.answer("Применяю...")
    plugin = await asyncio.to_thread(plugin_manager.toggle, uuid)
    await configure_commands(bot, plugin_manager.owner_id, plugin_manager)
    text, markup = plugin_detail(plugin)
    await callback.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith(f"{CARDINAL_CBT.PIN_PLUGIN}:"))
async def callback_cardinal_plugin_pin(
    callback: CallbackQuery, plugin_manager: PluginManager
) -> None:
    parts = str(callback.data or "").split(":")
    uuid = parts[1] if len(parts) > 1 else ""
    if uuid not in plugin_manager.plugins or not callback.message:
        await callback.answer("Плагин не найден", show_alert=True)
        return
    plugin = await asyncio.to_thread(plugin_manager.pin_plugin, uuid)
    await callback.answer("Закреплено" if plugin.pinned else "Откреплено")
    text, markup = plugin_detail(plugin)
    await callback.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith(f"{CARDINAL_CBT.DELETE_PLUGIN}:"))
async def callback_cardinal_plugin_delete_prompt(
    callback: CallbackQuery, plugin_manager: PluginManager
) -> None:
    parts = str(callback.data or "").split(":")
    uuid = parts[1] if len(parts) > 1 else ""
    plugin = plugin_manager.plugins.get(uuid)
    if not plugin or not callback.message:
        await callback.answer("Плагин не найден", show_alert=True)
        return
    await callback.answer()
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Удалить",
        callback_data=f"{CARDINAL_CBT.CONFIRM_DELETE_PLUGIN}:{uuid}:0",
    )
    builder.button(
        text="Отмена",
        callback_data=f"{CARDINAL_CBT.CANCEL_DELETE_PLUGIN}:{uuid}:0",
    )
    builder.adjust(2)
    await callback.message.edit_text(
        f"<b>Удалить плагин {html.escape(plugin.name)}?</b>",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith(f"{CARDINAL_CBT.CANCEL_DELETE_PLUGIN}:"))
async def callback_cardinal_plugin_delete_cancel(
    callback: CallbackQuery, plugin_manager: PluginManager
) -> None:
    parts = str(callback.data or "").split(":")
    uuid = parts[1] if len(parts) > 1 else ""
    plugin = plugin_manager.plugins.get(uuid)
    if not plugin or not callback.message:
        await callback.answer("Плагин не найден", show_alert=True)
        return
    await callback.answer()
    text, markup = plugin_detail(plugin)
    await callback.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith(f"{CARDINAL_CBT.CONFIRM_DELETE_PLUGIN}:"))
async def callback_cardinal_plugin_delete_confirm(
    callback: CallbackQuery, plugin_manager: PluginManager, bot: Bot
) -> None:
    parts = str(callback.data or "").split(":")
    uuid = parts[1] if len(parts) > 1 else ""
    if uuid not in plugin_manager.plugins or not callback.message:
        await callback.answer("Плагин не найден", show_alert=True)
        return
    await callback.answer("Удаляю...")
    try:
        await asyncio.to_thread(plugin_manager.delete_plugin, uuid)
        await configure_commands(bot, plugin_manager.owner_id, plugin_manager)
        await callback.message.edit_text(
            "<b>Плагин удалён</b>",
            reply_markup=plugin_list_menu(plugin_manager),
        )
    except Exception as error:
        await callback.message.edit_text(
            "<b>Не удалось удалить плагин</b>\n"
            + html.escape(str(error).splitlines()[0][:300]),
            reply_markup=plugin_list_menu(plugin_manager),
        )


@router.callback_query(F.data == CARDINAL_CBT.CLEAR_STATE)
async def callback_cardinal_clear_state(
    callback: CallbackQuery, plugin_manager: PluginManager
) -> None:
    if callback.message and callback.from_user:
        plugin_manager.telegram.clear_state(
            callback.message.chat.id, callback.from_user.id
        )
    await callback.answer("Отменено")


@router.callback_query(F.data.startswith("plugin:toggle:"))
async def callback_plugin_toggle(
    callback: CallbackQuery, plugin_manager: PluginManager, bot: Bot
) -> None:
    await callback.answer("Применяю...")
    uuid = str(callback.data or "").split(":", maxsplit=2)[-1]
    if uuid not in plugin_manager.plugins or not callback.message:
        return
    plugin = await asyncio.to_thread(plugin_manager.toggle, uuid)
    await configure_commands(bot, plugin_manager.owner_id, plugin_manager)
    text, markup = plugin_detail(plugin)
    await callback.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith("plugin:delete:confirm:"))
async def callback_plugin_delete_confirm(
    callback: CallbackQuery, plugin_manager: PluginManager, bot: Bot
) -> None:
    uuid = str(callback.data or "").split(":", maxsplit=3)[-1]
    plugin = plugin_manager.plugins.get(uuid)
    if not plugin or not callback.message:
        await callback.answer("Плагин не найден", show_alert=True)
        return
    await callback.answer("Удаляю плагин...")
    try:
        path = await asyncio.to_thread(plugin_manager.delete_plugin, uuid)
        await configure_commands(bot, plugin_manager.owner_id, plugin_manager)
        await callback.message.edit_text(
            f"<b>Плагин удалён</b>\n<code>{html.escape(path.name)}</code>",
            reply_markup=plugin_list_menu(plugin_manager),
        )
    except Exception as error:
        await callback.message.edit_text(
            "<b>Не удалось удалить плагин</b>\n"
            + html.escape(str(error).splitlines()[0][:300]),
            reply_markup=plugin_list_menu(plugin_manager),
        )


@router.callback_query(F.data.startswith("plugin:delete:"))
async def callback_plugin_delete_prompt(
    callback: CallbackQuery, plugin_manager: PluginManager
) -> None:
    uuid = str(callback.data or "").split(":", maxsplit=2)[-1]
    plugin = plugin_manager.plugins.get(uuid)
    if not plugin or not callback.message:
        await callback.answer("Плагин не найден", show_alert=True)
        return
    await callback.answer()
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Удалить",
        callback_data=f"plugin:delete:confirm:{uuid}",
    )
    builder.button(text="Отмена", callback_data=f"plugin:view:{uuid}")
    builder.adjust(2)
    await callback.message.edit_text(
        f"<b>Удалить плагин {html.escape(plugin.name)}?</b>\n\n"
        "Будет вызван BIND_TO_DELETE, затем файл плагина будет удалён.",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data == "menu:settings")
async def callback_settings(
    callback: CallbackQuery,
    state: FSMContext,
    automation: AutomationService,
    system_settings: SystemSettingsService,
) -> None:
    await callback.answer()
    await state.clear()
    if callback.message:
        settings = automation.get_settings()
        messages_status = "включён" if settings["messages"]["enabled"] else "выключен"
        reviews_status = "включён" if settings["reviews"]["enabled"] else "выключен"
        notifications_status = (
            "включены" if settings["notifications"]["enabled"] else "выключены"
        )
        logs_status = (
            "включены"
            if system_settings.get_settings()["logs_enabled"]
            else "выключены"
        )
        await callback.message.edit_text(
            "<b>Настройки</b>\n\n"
            f"Ответ на сообщения: {messages_status}\n"
            f"Ответ на отзывы: {reviews_status}\n"
            f"Уведомления: {notifications_status}\n"
            f"Логи: {logs_status}",
            reply_markup=settings_menu(),
        )


@router.callback_query(F.data == "settings:system")
async def callback_system_settings(
    callback: CallbackQuery,
    state: FSMContext,
    system_settings: SystemSettingsService,
) -> None:
    await callback.answer()
    await state.clear()
    if callback.message:
        settings = system_settings.get_settings()
        await callback.message.edit_text(
            format_system_settings(system_settings),
            reply_markup=system_settings_menu(settings),
        )


@router.callback_query(F.data == "settings:system:toggle_logs")
async def callback_system_toggle_logs(
    callback: CallbackQuery, system_settings: SystemSettingsService
) -> None:
    enabled = system_settings.toggle_logs()
    await callback.answer("Логи включены" if enabled else "Логи выключены")
    if callback.message:
        await callback.message.edit_text(
            format_system_settings(system_settings),
            reply_markup=system_settings_menu(system_settings.get_settings()),
        )


@router.callback_query(F.data == "settings:system:resources")
async def callback_system_resources(callback: CallbackQuery) -> None:
    await callback.answer("Обновляю...")
    if not callback.message:
        return
    await callback.message.edit_text("Собираю информацию о системе...")
    try:
        text = await asyncio.to_thread(collect_system_info)
    except Exception as error:
        text = (
            "<b>Не удалось получить информацию о системе</b>\n"
            + html.escape(str(error).splitlines()[0][:300])
        )
    await callback.message.edit_text(text, reply_markup=system_resources_menu())


@router.callback_query(F.data == "settings:system:cleanup")
async def callback_system_cleanup(
    callback: CallbackQuery, system_settings: SystemSettingsService
) -> None:
    await callback.answer("Очищаю...")
    result = await asyncio.to_thread(system_settings.cleanup_logs, True)
    if callback.message:
        await callback.message.edit_text(
            format_system_settings(system_settings)
            + "\n\n<b>Результат очистки</b>\n"
            + format_cleanup_result(result),
            reply_markup=system_settings_menu(system_settings.get_settings()),
        )


@router.callback_query(F.data == "system:resources:cleanup")
async def callback_system_temporary_cleanup(
    callback: CallbackQuery, system_settings: SystemSettingsService
) -> None:
    await callback.answer("Очищаю кеши и логи...")
    if not callback.message:
        return
    await callback.message.edit_text("Очищаю временные файлы MoneyBot...")
    result = await asyncio.to_thread(system_settings.cleanup_temporary_files)
    await callback.message.edit_text(
        format_temporary_cleanup_result(result),
        reply_markup=system_resources_menu(),
    )


@router.callback_query(F.data == "settings:lots:raise")
async def callback_raise_lots_prompt(
    callback: CallbackQuery,
    state: FSMContext,
    auto_delivery: AutoDeliveryService,
) -> None:
    await callback.answer()
    await state.clear()
    if not callback.message:
        return
    try:
        lots = await asyncio.to_thread(auto_delivery.get_lots, True)
        if not lots:
            await callback.message.edit_text(
                "Активные лоты не найдены.", reply_markup=settings_menu()
            )
            return
        await callback.message.edit_text(
            "<b>Поднять все лоты?</b>\n\n"
            f"Активных лотов: {len(lots)}\n"
            "Будут подняты все категории, в которых есть ваши активные лоты.\n"
            "FunPay может ограничить частоту поднятия.",
            reply_markup=raise_lots_confirm_menu(),
        )
    except Exception as error:
        await callback.message.edit_text(
            "<b>Не удалось получить активные лоты</b>\n"
            + html.escape(str(error).splitlines()[0][:300]),
            reply_markup=settings_menu(),
        )


@router.callback_query(F.data == "settings:lots:auto_toggle")
async def callback_auto_raise_toggle(
    callback: CallbackQuery,
) -> None:
    enabled = not auto_raise_enabled()
    set_auto_raise_enabled(enabled)
    await callback.answer("Автоподнятие включено" if enabled else "Автоподнятие выключено")
    if callback.message:
        marker = "🟢" if enabled else "🔴"
        await callback.message.edit_text(
            f"{marker} <b>Поднятие всех лотов {'включено' if enabled else 'выключено'}</b>\n\n"
            "При включении бот будет пытаться поднять все лоты один раз в час.",
            reply_markup=settings_menu(),
        )
@router.callback_query(F.data == "settings:lots:raise:confirm")
async def callback_raise_lots_confirm(
    callback: CallbackQuery, auto_delivery: AutoDeliveryService
) -> None:
    await callback.answer("Поднимаю лоты...")
    if not callback.message:
        return
    await callback.message.edit_text("Поднимаю все активные лоты через FunPay...")
    result = await asyncio.to_thread(auto_delivery.raise_all_lots)
    await callback.message.edit_text(
        format_raise_lots_result(result), reply_markup=settings_menu()
    )


@router.callback_query(F.data == "settings:system:log_limit")
async def callback_system_log_limit(
    callback: CallbackQuery,
    state: FSMContext,
    system_settings: SystemSettingsService,
) -> None:
    await callback.answer()
    await state.set_state(SystemSettings.waiting_log_limit)
    current = system_settings.get_settings()["max_log_size_mb"]
    if callback.message:
        await callback.message.edit_text(
            "<b>Лимит размера логов</b>\n\n"
            f"Сейчас: {current} МБ на один файл.\n"
            "Отправьте новый лимит от 1 до 1024 МБ. "
            "Файлы больше лимита автоматически очищаются раз в час.",
            reply_markup=settings_cancel_menu("system"),
        )


@router.message(SystemSettings.waiting_log_limit, F.text)
async def receive_system_log_limit(
    message: Message,
    state: FSMContext,
    system_settings: SystemSettingsService,
) -> None:
    value = str(message.text or "").strip()
    if not value.isdigit() or not 1 <= int(value) <= 1024:
        await message.answer("Введите целое число от 1 до 1024.")
        return
    system_settings.set_max_log_size(int(value))
    await state.clear()
    await message.answer(
        format_system_settings(system_settings),
        reply_markup=system_settings_menu(system_settings.get_settings()),
    )


@router.callback_query(F.data == "settings:notifications")
async def callback_notification_settings(
    callback: CallbackQuery, state: FSMContext, automation: AutomationService
) -> None:
    await callback.answer()
    await state.clear()
    if callback.message:
        settings = automation.get_settings()["notifications"]
        await callback.message.edit_text(
            format_notification_settings(automation),
            reply_markup=notifications_settings_menu(settings),
        )


@router.callback_query(F.data.startswith("settings:notifications:toggle:"))
async def callback_notification_toggle(
    callback: CallbackQuery, automation: AutomationService
) -> None:
    key = str(callback.data or "").rsplit(":", maxsplit=1)[-1]
    if key not in {"enabled", *NOTIFICATION_KEYS}:
        await callback.answer("Неизвестная настройка", show_alert=True)
        return
    enabled = automation.toggle_notification(key)
    await callback.answer("Включено" if enabled else "Выключено")
    if callback.message:
        settings = automation.get_settings()["notifications"]
        await callback.message.edit_text(
            format_notification_settings(automation),
            reply_markup=notifications_settings_menu(settings),
        )


@router.callback_query(F.data == "settings:messages")
async def callback_message_settings(
    callback: CallbackQuery, state: FSMContext, automation: AutomationService
) -> None:
    await callback.answer()
    await state.clear()
    if callback.message:
        enabled = bool(automation.get_settings()["messages"]["enabled"])
        await callback.message.edit_text(
            format_message_settings(automation),
            reply_markup=message_settings_menu(enabled),
        )


@router.callback_query(F.data == "settings:messages:toggle")
async def callback_message_settings_toggle(
    callback: CallbackQuery, automation: AutomationService
) -> None:
    enabled = automation.toggle_messages()
    await callback.answer("Автоответ включён" if enabled else "Автоответ выключен")
    if callback.message:
        await callback.message.edit_text(
            format_message_settings(automation),
            reply_markup=message_settings_menu(enabled),
        )


@router.callback_query(F.data == "settings:messages:template")
async def callback_message_template(
    callback: CallbackQuery, state: FSMContext, automation: AutomationService
) -> None:
    await callback.answer()
    await state.set_state(AutomationSettings.waiting_message_template)
    if callback.message:
        current = html.escape(str(automation.get_settings()["messages"]["template"]))
        await callback.message.edit_text(
            "<b>Шаблон ответа на сообщения</b>\n\n"
            f"Текущий шаблон:\n<code>{current}</code>\n\n"
            "Отправьте новый шаблон одним сообщением. Максимум 4000 символов.",
            reply_markup=settings_cancel_menu("messages"),
        )


@router.message(AutomationSettings.waiting_message_template, F.text)
async def receive_message_template(
    message: Message, state: FSMContext, automation: AutomationService
) -> None:
    template = str(message.text or "").strip()
    if not template:
        await message.answer("Шаблон не может быть пустым.")
        return
    if len(template) > 4000:
        await message.answer("Шаблон слишком длинный. Максимум 4000 символов.")
        return
    automation.set_message_template(template)
    await state.clear()
    await message.answer(
        format_message_settings(automation),
        reply_markup=message_settings_menu(
            bool(automation.get_settings()["messages"]["enabled"])
        ),
    )


@router.callback_query(F.data == "settings:messages:cooldown")
async def callback_message_cooldown(
    callback: CallbackQuery, state: FSMContext, automation: AutomationService
) -> None:
    await callback.answer()
    await state.set_state(AutomationSettings.waiting_message_cooldown)
    if callback.message:
        cooldown = int(automation.get_settings()["messages"]["cooldown_seconds"])
        await callback.message.edit_text(
            "<b>КД автоответа</b>\n\n"
            f"Сейчас: {format_cooldown(cooldown)}.\n"
            "Отправьте новую задержку: секунды числом или значение вида "
            "<code>5m</code>, <code>2h</code>. Значение <code>0</code> отключает КД. "
            "Максимум 24 часа.",
            reply_markup=settings_cancel_menu("messages"),
        )


@router.message(AutomationSettings.waiting_message_cooldown, F.text)
async def receive_message_cooldown(
    message: Message, state: FSMContext, automation: AutomationService
) -> None:
    cooldown = parse_cooldown(str(message.text or ""))
    if cooldown is None:
        await message.answer(
            "Некорректное значение. Примеры: <code>300</code>, <code>5m</code>, "
            "<code>2h</code>. Допустимо от 0 до 24 часов."
        )
        return
    automation.set_message_cooldown(cooldown)
    await state.clear()
    await message.answer(
        format_message_settings(automation),
        reply_markup=message_settings_menu(
            bool(automation.get_settings()["messages"]["enabled"])
        ),
    )


def format_command_settings(automation: AutomationService) -> str:
    commands = automation.get_commands()
    lines = ["<b>Команды FunPay</b>", ""]
    if not commands:
        lines.append("Команды ещё не созданы.")
    for command, data in commands.items():
        lines.append(f"<b>{html.escape(command)}</b> → <code>{html.escape(data['response'][:120])}</code>")
    return "\n".join(lines)


def command_settings_menu(automation: AutomationService) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    commands = list(automation.get_commands())
    builder.button(text="Добавить команду", callback_data="settings:messages:commands:add")
    for index, command in enumerate(commands):
        builder.button(
            text=f"Удалить {command}",
            callback_data=f"settings:messages:commands:delete:{index}",
        )
    builder.button(text="Назад", callback_data="settings:messages")
    builder.adjust(1)
    return builder.as_markup()


@router.callback_query(F.data == "settings:messages:commands")
async def callback_message_commands(
    callback: CallbackQuery, state: FSMContext, automation: AutomationService
) -> None:
    await callback.answer()
    await state.clear()
    if callback.message:
        await callback.message.edit_text(
            format_command_settings(automation),
            reply_markup=command_settings_menu(automation),
        )


@router.callback_query(F.data == "settings:messages:commands:add")
async def callback_message_command_add(
    callback: CallbackQuery, state: FSMContext
) -> None:
    await callback.answer()
    await state.set_state(AutomationSettings.waiting_command_name)
    if callback.message:
        await callback.message.edit_text(
            "<b>Новая команда</b>\n\n"
            "Отправьте команду, например <code>!вызов</code>.\n"
            "Допустимы буквы, цифры и символ <code>_</code>.",
            reply_markup=settings_cancel_menu("messages:commands"),
        )


@router.message(AutomationSettings.waiting_command_name, F.text)
async def receive_message_command_name(
    message: Message, state: FSMContext
) -> None:
    command = str(message.text or "").strip().lower()
    if not re.fullmatch(r"![a-zа-яё0-9_]{1,32}", command, re.IGNORECASE):
        await message.answer("Некорректная команда. Пример: <code>!вызов</code>.")
        return
    await state.update_data(command_name=command)
    await state.set_state(AutomationSettings.waiting_command_response)
    await message.answer(
        "Отправьте текст, который бот напишет покупателю в FunPay.\n"
        "Можно использовать <code>{username}</code>, <code>{message}</code>, <code>{command}</code>, <code>{account}</code> и <code>{chat_id}</code>."
    )


@router.message(AutomationSettings.waiting_command_response, F.text)
async def receive_message_command_response(
    message: Message, state: FSMContext
) -> None:
    response = str(message.text or "").strip()
    if not response or len(response) > 4000:
        await message.answer("Ответ должен быть непустым и не длиннее 4000 символов.")
        return
    await state.update_data(command_response=response)
    await state.set_state(AutomationSettings.waiting_command_notification)
    await message.answer(
        "Отправьте текст уведомления владельцу в Telegram.\n"
        "Можно использовать те же переменные."
    )


@router.message(AutomationSettings.waiting_command_notification, F.text)
async def receive_message_command_notification(
    message: Message, state: FSMContext, automation: AutomationService
) -> None:
    notification = str(message.text or "").strip()
    if not notification or len(notification) > 4000:
        await message.answer("Уведомление должно быть непустым и не длиннее 4000 символов.")
        return
    data = await state.get_data()
    try:
        automation.set_command(
            str(data.get("command_name") or ""),
            str(data.get("command_response") or ""),
            notification,
        )
    except ValueError as error:
        await state.clear()
        await message.answer(f"Команда не сохранена: {html.escape(str(error))}")
        return
    await state.clear()
    await message.answer(
        format_command_settings(automation),
        reply_markup=command_settings_menu(automation),
    )


@router.callback_query(F.data.startswith("settings:messages:commands:delete:"))
async def callback_message_command_delete(
    callback: CallbackQuery, automation: AutomationService
) -> None:
    index_text = str(callback.data or "").rsplit(":", maxsplit=1)[-1]
    commands = list(automation.get_commands())
    if not index_text.isdigit() or int(index_text) >= len(commands):
        await callback.answer("Команда не найдена", show_alert=True)
        return
    automation.delete_command(commands[int(index_text)])
    await callback.answer("Команда удалена")
    if callback.message:
        await callback.message.edit_text(
            format_command_settings(automation),
            reply_markup=command_settings_menu(automation),
        )


@router.callback_query(F.data == "settings:reviews")
async def callback_review_settings(
    callback: CallbackQuery, state: FSMContext, automation: AutomationService
) -> None:
    await callback.answer()
    await state.clear()
    if callback.message:
        enabled = bool(automation.get_settings()["reviews"]["enabled"])
        await callback.message.edit_text(
            format_review_settings(automation),
            reply_markup=review_settings_menu(enabled),
        )


@router.callback_query(F.data == "settings:reviews:toggle")
async def callback_review_settings_toggle(
    callback: CallbackQuery, automation: AutomationService
) -> None:
    enabled = automation.toggle_reviews()
    await callback.answer("Ответы включены" if enabled else "Ответы выключены")
    if callback.message:
        await callback.message.edit_text(
            format_review_settings(automation),
            reply_markup=review_settings_menu(enabled),
        )


@router.callback_query(F.data.startswith("settings:reviews:template:"))
async def callback_review_template(
    callback: CallbackQuery, state: FSMContext, automation: AutomationService
) -> None:
    stars_text = str(callback.data or "").rsplit(":", maxsplit=1)[-1]
    if not stars_text.isdigit() or int(stars_text) not in range(1, 6):
        await callback.answer("Некорректная оценка", show_alert=True)
        return
    stars = int(stars_text)
    await callback.answer()
    await state.set_state(AutomationSettings.waiting_review_template)
    await state.update_data(review_template_stars=stars)
    if callback.message:
        current = html.escape(
            str(automation.get_settings()["reviews"]["templates"][str(stars)])
        )
        await callback.message.edit_text(
            f"<b>Шаблон для отзыва на {stars} зв.</b>\n\n"
            f"Текущий шаблон:\n<code>{current}</code>\n\n"
            "Отправьте новый шаблон одним сообщением. Максимум 1000 символов.",
            reply_markup=settings_cancel_menu("reviews"),
        )


@router.message(AutomationSettings.waiting_review_template, F.text)
async def receive_review_template(
    message: Message, state: FSMContext, automation: AutomationService
) -> None:
    template = str(message.text or "").strip()
    if not template:
        await message.answer("Шаблон не может быть пустым.")
        return
    if len(template) > 1000:
        await message.answer("Шаблон слишком длинный. Максимум 1000 символов.")
        return
    data = await state.get_data()
    stars = int(data.get("review_template_stars") or 0)
    if stars not in range(1, 6):
        await state.clear()
        await message.answer("Не удалось определить оценку. Откройте настройку шаблона заново.")
        return
    automation.set_review_template(stars, template)
    await state.clear()
    await message.answer(
        format_review_settings(automation),
        reply_markup=review_settings_menu(
            bool(automation.get_settings()["reviews"]["enabled"])
        ),
    )


@router.callback_query(F.data == "menu:tickets")
async def callback_tickets(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    if callback.message:
        await callback.message.edit_text(
            "<b>Тикеты FunPay Support</b>", reply_markup=tickets_menu()
        )


@router.callback_query(F.data == "tickets:settings")
async def callback_auto_ticket_settings(
    callback: CallbackQuery,
    state: FSMContext,
    auto_tickets: AutoTicketService,
) -> None:
    await callback.answer()
    await state.clear()
    if callback.message:
        await callback.message.edit_text(
            format_auto_ticket_settings(auto_tickets),
            reply_markup=auto_ticket_settings_menu(auto_tickets.get_settings()),
        )


@router.callback_query(F.data == "tickets:settings:toggle")
async def callback_auto_ticket_toggle(
    callback: CallbackQuery, auto_tickets: AutoTicketService
) -> None:
    enabled = auto_tickets.toggle()
    await callback.answer("Автотикеты включены" if enabled else "Автотикеты выключены")
    if callback.message:
        await callback.message.edit_text(
            format_auto_ticket_settings(auto_tickets),
            reply_markup=auto_ticket_settings_menu(auto_tickets.get_settings()),
        )


@router.callback_query(F.data == "tickets:settings:scope")
async def callback_auto_ticket_scope(
    callback: CallbackQuery, auto_tickets: AutoTicketService
) -> None:
    scope = auto_tickets.toggle_scope()
    await callback.answer(
        "Все заказы" if scope == "all" else "Только заказы автовыдачи"
    )
    if callback.message:
        await callback.message.edit_text(
            format_auto_ticket_settings(auto_tickets),
            reply_markup=auto_ticket_settings_menu(auto_tickets.get_settings()),
        )


@router.callback_query(F.data == "tickets:settings:delay")
async def callback_auto_ticket_delay(
    callback: CallbackQuery, state: FSMContext, auto_tickets: AutoTicketService
) -> None:
    await callback.answer()
    await state.set_state(AutoTicketSettings.waiting_delay)
    current = auto_tickets.get_settings()["delay_hours"]
    if callback.message:
        await callback.message.edit_text(
            "<b>Срок ожидания подтверждения</b>\n\n"
            f"Сейчас: {current} ч. Отправьте количество часов от 1 до 720.",
            reply_markup=auto_ticket_cancel_menu(),
        )


@router.message(AutoTicketSettings.waiting_delay, F.text)
async def receive_auto_ticket_delay(
    message: Message, state: FSMContext, auto_tickets: AutoTicketService
) -> None:
    value = str(message.text or "").strip()
    if not value.isdigit() or not 1 <= int(value) <= 720:
        await message.answer("Введите целое число от 1 до 720.")
        return
    auto_tickets.set_delay_hours(int(value))
    await state.clear()
    await message.answer(
        format_auto_ticket_settings(auto_tickets),
        reply_markup=auto_ticket_settings_menu(auto_tickets.get_settings()),
    )


@router.callback_query(F.data == "tickets:settings:interval")
async def callback_auto_ticket_interval(
    callback: CallbackQuery, state: FSMContext, auto_tickets: AutoTicketService
) -> None:
    await callback.answer()
    await state.set_state(AutoTicketSettings.waiting_interval)
    current = auto_tickets.get_settings()["check_interval_minutes"]
    if callback.message:
        await callback.message.edit_text(
            "<b>Интервал проверки</b>\n\n"
            f"Сейчас: {current} мин. Отправьте значение от 10 до 1440 минут.",
            reply_markup=auto_ticket_cancel_menu(),
        )


@router.message(AutoTicketSettings.waiting_interval, F.text)
async def receive_auto_ticket_interval(
    message: Message, state: FSMContext, auto_tickets: AutoTicketService
) -> None:
    value = str(message.text or "").strip()
    if not value.isdigit() or not 10 <= int(value) <= 1440:
        await message.answer("Введите целое число от 10 до 1440.")
        return
    auto_tickets.set_interval_minutes(int(value))
    await state.clear()
    await message.answer(
        format_auto_ticket_settings(auto_tickets),
        reply_markup=auto_ticket_settings_menu(auto_tickets.get_settings()),
    )


@router.callback_query(F.data == "tickets:settings:limit")
async def callback_auto_ticket_limit(
    callback: CallbackQuery, state: FSMContext, auto_tickets: AutoTicketService
) -> None:
    await callback.answer()
    await state.set_state(AutoTicketSettings.waiting_limit)
    current = auto_tickets.get_settings()["max_orders_per_ticket"]
    if callback.message:
        await callback.message.edit_text(
            "<b>Лимит заказов</b>\n\n"
            f"Сейчас: {current}. Отправьте число от 1 до 100.",
            reply_markup=auto_ticket_cancel_menu(),
        )


@router.message(AutoTicketSettings.waiting_limit, F.text)
async def receive_auto_ticket_limit(
    message: Message, state: FSMContext, auto_tickets: AutoTicketService
) -> None:
    value = str(message.text or "").strip()
    if not value.isdigit() or not 1 <= int(value) <= 100:
        await message.answer("Введите целое число от 1 до 100.")
        return
    auto_tickets.set_max_orders(int(value))
    await state.clear()
    await message.answer(
        format_auto_ticket_settings(auto_tickets),
        reply_markup=auto_ticket_settings_menu(auto_tickets.get_settings()),
    )


@router.callback_query(F.data == "tickets:settings:template")
async def callback_auto_ticket_template(
    callback: CallbackQuery, state: FSMContext, auto_tickets: AutoTicketService
) -> None:
    await callback.answer()
    await state.set_state(AutoTicketSettings.waiting_template)
    current = html.escape(auto_tickets.get_settings()["message_template"])
    if callback.message:
        await callback.message.edit_text(
            "<b>Текст автоматического тикета</b>\n\n"
            f"Сейчас:\n<code>{current}</code>\n\n"
            "Отправьте новый текст до 2000 символов. Обязательная переменная: "
            "<code>{order_ids}</code>. Также доступны <code>{orders_count}</code> "
            "и <code>{account}</code>.",
            reply_markup=auto_ticket_cancel_menu(),
        )


@router.message(AutoTicketSettings.waiting_template, F.text)
async def receive_auto_ticket_template(
    message: Message, state: FSMContext, auto_tickets: AutoTicketService
) -> None:
    template = str(message.text or "").strip()
    if not template or len(template) > 2000:
        await message.answer("Текст должен содержать от 1 до 2000 символов.")
        return
    if "{order_ids}" not in template:
        await message.answer("Добавьте обязательную переменную <code>{order_ids}</code>.")
        return
    auto_tickets.set_message_template(template)
    await state.clear()
    await message.answer(
        format_auto_ticket_settings(auto_tickets),
        reply_markup=auto_ticket_settings_menu(auto_tickets.get_settings()),
    )


@router.callback_query(F.data == "tickets:settings:check")
async def callback_auto_ticket_check(
    callback: CallbackQuery, auto_tickets: AutoTicketService
) -> None:
    await callback.answer("Проверяю заказы...")
    if not callback.message:
        return
    await callback.message.edit_text("Проверяю неподтверждённые заказы...")
    result = await asyncio.to_thread(auto_tickets.run_check)
    if result.status == "pending":
        await callback.message.edit_text(
            format_auto_ticket_settings(auto_tickets),
            reply_markup=auto_ticket_settings_menu(auto_tickets.get_settings()),
        )
        await callback.message.answer(
            format_auto_ticket_result(result),
            reply_markup=auto_ticket_confirmation_menu(result.confirmation_id),
        )
        return
    await callback.message.edit_text(
        format_auto_ticket_settings(auto_tickets)
        + "\n\n"
        + format_auto_ticket_result(result),
        reply_markup=auto_ticket_settings_menu(auto_tickets.get_settings()),
    )


@router.callback_query(F.data == "tickets:settings:test")
async def callback_auto_ticket_test(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "<b>Отправить автотикет?</b>\n\n"
            "Заказы: <code>#TEST0001, #TEST0002</code>\n"
            "Это тест: Support вызван не будет.",
            reply_markup=auto_ticket_confirmation_menu("test", test=True),
        )


@router.callback_query(F.data == "autoticket:test:confirm")
async def callback_auto_ticket_test_confirm(callback: CallbackQuery) -> None:
    await callback.answer("Тест: отправка подтверждена", show_alert=True)
    if callback.message:
        await callback.message.edit_text(
            "<b>Тестовое подтверждение принято.</b>\nSupport не вызывался."
        )


@router.callback_query(F.data == "autoticket:test:cancel")
async def callback_auto_ticket_test_cancel(callback: CallbackQuery) -> None:
    await callback.answer("Тест: отправка отменена", show_alert=True)
    if callback.message:
        await callback.message.edit_text(
            "<b>Тестовое подтверждение отменено.</b>\nSupport не вызывался."
        )


@router.callback_query(F.data.startswith("autoticket:confirm:"))
async def callback_auto_ticket_confirm(
    callback: CallbackQuery, auto_tickets: AutoTicketService
) -> None:
    confirmation_id = str(callback.data or "").rsplit(":", maxsplit=1)[-1]
    await callback.answer("Отправляю тикет...")
    if not callback.message:
        return
    await callback.message.edit_text("Отправляю подтверждённый тикет в Support...")
    result = await asyncio.to_thread(auto_tickets.confirm, confirmation_id)
    markup = (
        auto_ticket_confirmation_menu(confirmation_id)
        if result.status == "error"
        else None
    )
    await callback.message.edit_text(format_auto_ticket_result(result), reply_markup=markup)


@router.callback_query(F.data.startswith("autoticket:cancel:"))
async def callback_auto_ticket_cancel(
    callback: CallbackQuery, auto_tickets: AutoTicketService
) -> None:
    confirmation_id = str(callback.data or "").rsplit(":", maxsplit=1)[-1]
    result = await asyncio.to_thread(auto_tickets.cancel, confirmation_id)
    await callback.answer("Автотикет отменён" if result.status == "cancelled" else "Уже обработан")
    if callback.message:
        await callback.message.edit_text(format_auto_ticket_result(result))


@router.callback_query(F.data == "tickets:list")
async def callback_ticket_list(
    callback: CallbackQuery, ticket_client: TicketClient
) -> None:
    await callback.answer("Загружаю...")
    if not callback.message:
        return
    await callback.message.edit_text("Получаю тикеты FunPay Support...")
    try:
        tickets = await asyncio.to_thread(ticket_client.get_tickets)
    except (TicketError, OSError) as error:
        await callback.message.edit_text(
            f"<b>Ошибка Support</b>\n{html.escape(support_error(error))}",
            reply_markup=tickets_menu(),
        )
        return

    builder = InlineKeyboardBuilder()
    lines = ["<b>Мои тикеты</b>", ""]
    if not tickets:
        lines.append("Тикетов пока нет.")
    for ticket in tickets[:10]:
        marker = "● " if ticket.get("unread") else ""
        subject = html.escape(str(ticket.get("subject") or "Без темы")[:120])
        status = html.escape(str(ticket.get("status") or ""))
        date = html.escape(str(ticket.get("date") or ""))
        lines.append(f"{marker}<b>#{ticket['id']}</b> {subject}")
        if status or date:
            lines.append(" · ".join(x for x in (status, date) if x))
        builder.button(
            text=f"#{ticket['id']} {str(ticket.get('subject') or '')[:28]}",
            callback_data=f"ticket:open:{ticket['id']}",
        )
    builder.button(text="Обновить", callback_data="tickets:list")
    builder.button(text="Назад", callback_data="menu:tickets")
    builder.adjust(1)
    await callback.message.edit_text("\n".join(lines), reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("ticket:open:"))
async def callback_ticket_open(
    callback: CallbackQuery, ticket_client: TicketClient
) -> None:
    await callback.answer("Загружаю...")
    if not callback.message:
        return
    ticket_id = str(callback.data or "").rsplit(":", maxsplit=1)[-1]
    await callback.message.edit_text(f"Получаю тикет #{html.escape(ticket_id)}...")
    try:
        ticket = await asyncio.to_thread(ticket_client.get_ticket, ticket_id)
    except (TicketError, OSError) as error:
        await callback.message.edit_text(
            f"<b>Ошибка Support</b>\n{html.escape(support_error(error))}",
            reply_markup=tickets_menu(),
        )
        return

    lines = [
        f"<b>Тикет #{html.escape(ticket_id)}</b>",
        html.escape(str(ticket.get("subject") or "Без темы")),
    ]
    if ticket.get("status"):
        lines.append(f"Статус: {html.escape(str(ticket['status']))}")
    lines.append("")
    messages = ticket.get("messages") or []
    for item in messages[-5:]:
        author = html.escape(str(item.get("author") or "—"))
        date = html.escape(str(item.get("date") or ""))
        raw_body = str(item.get("body") or "")
        body = html.escape(raw_body[:650] + ("..." if len(raw_body) > 650 else ""))
        lines.extend([f"<b>{author}</b> {date}".strip(), body, ""])
    text = "\n".join(lines).strip()
    builder = InlineKeyboardBuilder()
    builder.button(text="К списку", callback_data="tickets:list")
    builder.button(text="Назад", callback_data="menu:tickets")
    builder.adjust(1)
    await callback.message.edit_text(text, reply_markup=builder.as_markup())


@router.callback_query(F.data == "tickets:new")
async def callback_ticket_new(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    if callback.message:
        await callback.message.edit_text(
            "<b>Новый тикет</b>\n\nВыберите тип обращения.",
            reply_markup=ticket_type_menu(),
        )


@router.callback_query(F.data == "ticket:type:seller")
async def callback_ticket_seller(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "<b>Тикет продавца</b>\n\nВыберите тему.",
            reply_markup=seller_topics_menu(),
        )


@router.callback_query(F.data == "ticket:type:buyer")
async def callback_ticket_buyer(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "<b>Тикет покупателя</b>\n\nВыберите тему.",
            reply_markup=buyer_topics_menu(),
        )


@router.callback_query(F.data == "ticket:type:account")
async def callback_ticket_account(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "<b>Проблема аккаунта</b>\n\nВыберите тему.",
            reply_markup=account_topics_menu(),
        )


@router.callback_query(F.data.startswith("ticket:topic:"))
async def callback_ticket_topic(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    parts = str(callback.data or "").split(":")
    if len(parts) != 4 or parts[2] not in {"seller", "buyer", "account"}:
        return
    role, topic_id = parts[2], parts[3]
    topic_labels = {
        "201": "Покупатель забыл подтвердить заказ",
        "202": "Проблема с покупателем",
        "203": "Жалоба на отзыв покупателя",
        "101": "Продавец не отвечает",
        "102": "Жалоба на продавца",
        "404": "Аккаунт взломан",
        "405": "Восстановить доступ к аккаунту",
        "409": "Аккаунт заблокирован по другой причине",
        "411": "Ограничена возможность писать в чат",
    }
    form_id = "3" if role == "account" else "1"
    await state.update_data(
        ticket_role=role,
        ticket_topic_id=topic_id,
        ticket_topic_label=topic_labels.get(topic_id, "Обращение"),
        ticket_form_id=form_id,
        ticket_order_id="",
    )
    if not callback.message:
        return
    if form_id == "1":
        await state.set_state(NewTicket.waiting_order_id)
        await callback.message.edit_text(
            "<b>Номер заказа</b>\n\n"
            "Отправьте номер заказа FunPay, например <code>#ABCDEF12</code>.",
            reply_markup=ticket_cancel_menu(),
        )
    else:
        await state.set_state(NewTicket.waiting_text)
        await callback.message.edit_text(
            "<b>Текст обращения</b>\n\nОтправьте описание проблемы одним сообщением.",
            reply_markup=ticket_cancel_menu(),
        )


@router.message(NewTicket.waiting_order_id, F.text)
async def receive_ticket_order(message: Message, state: FSMContext) -> None:
    order_id = str(message.text or "").strip().upper()
    normalized = order_id[1:] if order_id.startswith("#") else order_id
    if not (4 <= len(normalized) <= 32 and normalized.replace("-", "").isalnum()):
        await message.answer("Некорректный номер заказа. Пример: <code>#ABCDEF12</code>")
        return
    await state.update_data(ticket_order_id=order_id)
    await state.set_state(NewTicket.waiting_text)
    await message.answer(
        "<b>Текст обращения</b>\n\nОтправьте описание проблемы одним сообщением.",
        reply_markup=ticket_cancel_menu(),
    )


@router.message(NewTicket.waiting_text, F.text)
async def receive_ticket_text(message: Message, state: FSMContext) -> None:
    text = str(message.text or "").strip()
    if len(text) < 5:
        await message.answer("Текст слишком короткий. Отправьте минимум 5 символов.")
        return
    if len(text) > 3500:
        await message.answer("Текст слишком длинный. Максимум 3500 символов.")
        return
    await state.update_data(ticket_text=text)
    await state.set_state(NewTicket.waiting_confirmation)
    data = await state.get_data()
    preview = html.escape(text[:1200])
    topic = html.escape(str(data.get("ticket_topic_label") or "Обращение"))
    order_id = html.escape(str(data.get("ticket_order_id") or ""))
    order_line = f"\n<b>Заказ:</b> <code>{order_id}</code>" if order_id else ""
    await message.answer(
        f"<b>Подтвердите отправку тикета</b>\n\n"
        f"<b>Тема:</b> {topic}{order_line}\n\n{preview}",
        reply_markup=ticket_confirm_menu(),
    )


@router.callback_query(F.data == "tickets:send", NewTicket.waiting_confirmation)
async def callback_ticket_send(
    callback: CallbackQuery,
    state: FSMContext,
    ticket_client: TicketClient,
    funpay: FunPayService,
) -> None:
    await callback.answer("Отправляю...")
    if not callback.message:
        return
    data = await state.get_data()
    text = str(data.get("ticket_text") or "")
    await callback.message.edit_text("Отправляю тикет в FunPay Support...")
    try:
        profile = await asyncio.to_thread(funpay.get_profile)
        ticket_id = await asyncio.to_thread(
            ticket_client.send_ticket,
            text,
            profile.username,
            str(data.get("ticket_order_id") or ""),
            str(data.get("ticket_form_id") or "1"),
            str(data.get("ticket_topic_id") or "202"),
            str(data.get("ticket_role") or "seller"),
        )
    except (TicketError, FunPayServiceError, OSError) as error:
        await callback.message.edit_text(
            f"<b>Тикет не отправлен</b>\n{html.escape(support_error(error))}",
            reply_markup=ticket_confirm_menu(),
        )
        return
    await state.clear()
    suffix = f" #{html.escape(ticket_id)}" if ticket_id else ""
    await callback.message.edit_text(
        f"<b>Тикет{suffix} создан.</b>", reply_markup=tickets_menu()
    )


@router.callback_query(F.data == "tickets:cancel")
async def callback_ticket_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer("Отменено")
    await state.clear()
    if callback.message:
        await callback.message.edit_text(
            "<b>Тикеты FunPay Support</b>", reply_markup=tickets_menu()
        )


@router.callback_query()
async def plugin_callback_fallback(
    callback: CallbackQuery, plugin_manager: PluginManager
) -> None:
    if not callback.message:
        return
    await asyncio.to_thread(
        plugin_manager.dispatch_callback,
        callback.model_dump_json(by_alias=True, exclude_none=True),
    )


@router.message()
async def fallback_message(
    message: Message, state: FSMContext, plugin_manager: PluginManager
) -> None:
    await state.clear()
    if any(item.enabled for item in plugin_manager.plugins.values()):
        await asyncio.to_thread(
            plugin_manager.dispatch_message,
            message.model_dump_json(by_alias=True, exclude_none=True),
        )
    else:
        await message.answer("<b>Главное меню</b>", reply_markup=main_menu())


async def configure_commands(
    bot: Bot, owner_id: int, plugin_manager: PluginManager
) -> None:
    commands: dict[str, str] = {
        "start": "Главное меню",
        "system": "Ресурсы системы",
        "update": "Проверить обновления",
        "log": "Скачать логи",
    }
    for plugin in plugin_manager.plugins.values():
        if not plugin.enabled:
            continue
        for command, description in plugin.commands.items():
            command = command.strip().lower().lstrip("/")
            if not re.fullmatch(r"[a-z0-9_]{1,32}", command):
                logger.warning(
                    "Команда плагина %s пропущена: %s", plugin.name, command
                )
                continue
            commands.setdefault(command, description.strip()[:256] or plugin.name)

    scope = BotCommandScopeChat(chat_id=owner_id)
    await bot.delete_my_commands()
    await bot.delete_my_commands(scope=scope)
    await bot.set_my_commands(
        [
            BotCommand(command=command, description=description)
            for command, description in list(commands.items())[:100]
        ],
        scope=scope,
    )


async def run_bot(config: BotConfig) -> None:
    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
            link_preview_is_disabled=True,
        ),
    )
    dispatcher = Dispatcher(storage=MemoryStorage())
    owner_middleware = OwnerOnlyMiddleware(config.owner_id)
    router.message.outer_middleware(owner_middleware)
    router.callback_query.outer_middleware(owner_middleware)
    dispatcher.include_router(router)
    dispatcher["config"] = config
    system_settings = SystemSettingsService()
    system_settings.cleanup_logs()
    system_settings.apply_logging()
    system_settings.start_cleanup_worker()
    dispatcher["system_settings"] = system_settings
    update_service = UpdateService()
    dispatcher["update_service"] = update_service
    dispatcher["funpay"] = FunPayService(config.golden_key)
    ticket_client = TicketClient(
        config.golden_key, config.support_phpsessid
    )
    dispatcher["ticket_client"] = ticket_client
    plugin_manager = PluginManager(config.bot_token, config.owner_id, config.golden_key)
    await asyncio.to_thread(plugin_manager.initialize)
    dispatcher["plugin_manager"] = plugin_manager
    automation = AutomationService(plugin_manager.account)
    dispatcher["automation"] = automation
    auto_delivery = AutoDeliveryService(plugin_manager.account)
    dispatcher["auto_delivery"] = auto_delivery
    auto_tickets = AutoTicketService(
        plugin_manager.account, ticket_client, auto_delivery
    )
    dispatcher["auto_tickets"] = auto_tickets

    event_loop = asyncio.get_running_loop()

    def notify_update_release(release: ReleaseInfo) -> None:
        future = asyncio.run_coroutine_threadsafe(
            bot.send_message(
                config.owner_id,
                format_update_release(release, __version__),
                reply_markup=update_menu(release),
            ),
            event_loop,
        )

        def log_update_notification_result(notification: Any) -> None:
            try:
                notification.result()
                update_service.mark_notified(release)
            except Exception:
                logger.exception("Не удалось отправить уведомление об обновлении")

        future.add_done_callback(log_update_notification_result)

    def notify_resource_warning(warning: ResourceWarning) -> None:
        future = asyncio.run_coroutine_threadsafe(
            bot.send_message(
                config.owner_id,
                format_resource_warning(warning),
                reply_markup=resource_warning_menu(),
            ),
            event_loop,
        )

        def log_resource_warning_result(notification: Any) -> None:
            try:
                notification.result()
            except Exception:
                logger.exception(
                    "Не удалось отправить предупреждение о ресурсах в Telegram"
                )

        future.add_done_callback(log_resource_warning_result)

    def notify_funpay_message(event: Any) -> None:
        command_result = automation.handle_command(event.message)
        if command_result is not None:
            future = asyncio.run_coroutine_threadsafe(
                bot.send_message(
                    config.owner_id,
                    "👤 <b>Команда FunPay</b>\n"
                    f"Покупатель: {html.escape(command_result['username'])}\n"
                    f"Команда: <code>{html.escape(command_result['command'])}</code>\n\n"
                    + html.escape(command_result["notification"]),
                ),
                event_loop,
            )
            def log_command_notification_result(result: Any) -> None:
                try:
                    result.result()
                except Exception:
                    logger.exception(
                        "Не удалось отправить уведомление о команде FunPay в Telegram"
                    )

            future.add_done_callback(log_command_notification_result)
            return
        if not automation.should_notify(event.message):
            return
        account_id = plugin_manager.account.id if plugin_manager.account else None
        future = asyncio.run_coroutine_threadsafe(
            send_funpay_notification(
                bot, config.owner_id, event.message, account_id
            ),
            event_loop,
        )

        def log_notification_result(result: Any) -> None:
            try:
                result.result()
            except Exception:
                logger.exception("Не удалось отправить уведомление FunPay в Telegram")

        future.add_done_callback(log_notification_result)

    def handle_auto_delivery(event: Any) -> None:
        plugin_manager.run_handlers(
            plugin_manager.pre_delivery_handlers, (plugin_manager, event)
        )
        result = auto_delivery.handle_event(event)
        if result is None:
            return
        plugin_manager.run_handlers(
            plugin_manager.post_delivery_handlers, (plugin_manager, event)
        )
        future = asyncio.run_coroutine_threadsafe(
            send_delivery_result(bot, config.owner_id, result), event_loop
        )

        def log_delivery_result(notification: Any) -> None:
            try:
                notification.result()
            except Exception:
                logger.exception("Не удалось отправить результат автовыдачи в Telegram")

        future.add_done_callback(log_delivery_result)

    def notify_auto_ticket(result: AutoTicketResult) -> None:
        markup = (
            auto_ticket_confirmation_menu(result.confirmation_id)
            if result.status == "pending"
            else None
        )
        future = asyncio.run_coroutine_threadsafe(
            bot.send_message(
                config.owner_id,
                format_auto_ticket_result(result),
                reply_markup=markup,
            ),
            event_loop,
        )

        def log_auto_ticket_result(notification: Any) -> None:
            try:
                notification.result()
            except Exception:
                logger.exception("Не удалось отправить результат автотикета в Telegram")

        future.add_done_callback(log_auto_ticket_result)

    async def auto_raise_worker() -> None:
        while True:
            await asyncio.sleep(3600)
            if not auto_raise_enabled():
                continue
            try:
                result = await asyncio.to_thread(auto_delivery.raise_all_lots)
                if result.categories_raised > 0:
                    await bot.send_message(
                        config.owner_id,
                        "✅ <b>Лоты подняты автоматически</b>\n\n"
                        f"Категорий поднято: {result.categories_raised} из {result.categories_total}\n"
                        f"Активных лотов: {result.total_lots}"
                        + ("\n\n<b>Поднятые категории:</b>\n" + "\n".join(
                            f"• {html.escape(category)}" for category in result.raised_categories
                        ) if result.raised_categories else "")
                        + ("\n\n<b>Не подняты:</b>\n" + "\n".join(
                            f"• {html.escape(category)}: {html.escape(reason)}"
                            for category, reason in result.errors
                        ) if result.errors else ""),
                    )
            except Exception:
                logger.exception("Не удалось автоматически поднять лоты")

    plugin_manager.add_funpay_message_observer(automation.handle_event)
    plugin_manager.add_funpay_message_observer(notify_funpay_message)
    plugin_manager.add_funpay_order_observer(handle_auto_delivery)

    def notify_funpay_connection(connected: bool, error: Exception | None) -> None:
        if connected:
            text = "✅ <b>Соединение с FunPay восстановлено</b>"
        else:
            error_text = html.escape(str(error).splitlines()[0][:300]) if error else "неизвестная ошибка"
            text = (
                "⚠️ <b>Соединение с FunPay потеряно</b>\n"
                f"Причина: {error_text}\n"
                "Повторная попытка через 10 секунд."
            )
        future = asyncio.run_coroutine_threadsafe(
            bot.send_message(config.owner_id, text),
            event_loop,
        )

        def log_connection_notification(result: Any) -> None:
            try:
                result.result()
            except Exception:
                logger.exception("Не удалось отправить уведомление о соединении с FunPay")

        future.add_done_callback(log_connection_notification)

    plugin_manager.set_connection_observer(notify_funpay_connection)
    plugin_manager.activate_runner()
    system_settings.start_resource_monitor(notify_resource_warning)
    auto_tickets.set_result_callback(notify_auto_ticket)
    auto_tickets.start()

    await configure_commands(bot, config.owner_id, plugin_manager)
    me = await bot.get_me()
    logger.info("Aiogram-бот @%s запущен", me.username)
    funpay_profile = plugin_manager.profile
    balance_text = "не удалось получить"
    if plugin_manager.account and funpay_profile:
        try:
            balance = read_balance(plugin_manager.account, funpay_profile)
            balance_text = (
                f"{money(balance.available_rub, 'RUB')} доступно"
            )
        except Exception as error:
            balance_text = html.escape(str(error).splitlines()[0][:200])
    initialized_plugins = [
        record.name
        for record in plugin_manager.plugins.values()
        if record.loaded
    ]
    plugins_text = (
        "\n\n<b>Инициализированные плагины:</b>\n"
        + "\n".join(f"• {html.escape(name)}" for name in initialized_plugins)
        if initialized_plugins
        else "\n\n<b>Инициализированные плагины:</b> нет"
    )
    try:
        await bot.send_message(
            config.owner_id,
            "✅ <b>Бот запущен</b>\n\n"
            f"FunPay: @{html.escape(getattr(funpay_profile, 'username', 'неизвестно'))}\n"
            f"Баланс: {balance_text}\n"
            f"Версия: <code>{html.escape(__version__)}</code>"
            + plugins_text,
        )
    except Exception:
        logger.exception("Не удалось отправить уведомление о запуске бота в Telegram")
    update_service.start(notify_update_release)
    auto_raise_task = asyncio.create_task(auto_raise_worker())
    for pending in auto_tickets.list_pending():
        pending_result = AutoTicketResult(
            "pending",
            tuple(pending["order_ids"]),
            confirmation_id=str(pending["id"]),
        )
        await bot.send_message(
            config.owner_id,
            format_auto_ticket_result(pending_result),
            reply_markup=auto_ticket_confirmation_menu(str(pending["id"])),
        )
    try:
        await dispatcher.start_polling(
            bot, allowed_updates=dispatcher.resolve_used_update_types()
        )
    finally:
        auto_raise_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await auto_raise_task
        await asyncio.to_thread(plugin_manager.shutdown)
        await bot.session.close()


def main() -> int:
    try:
        config = load_config()
        asyncio.run(run_bot(config))
    except ConfigError as error:
        print(f"Ошибка конфигурации: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
