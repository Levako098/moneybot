from __future__ import annotations

from telebot.types import InlineKeyboardButton as B
from telebot.types import InlineKeyboardMarkup as K

from bot.compat.tg_bot import CBT


def edit_plugin(cardinal, uuid: str, offset: int = 0, ask_to_delete: bool = False) -> K:
    plugin = cardinal.plugins.get(uuid)
    keyboard = K()
    if plugin is None:
        return keyboard
    if ask_to_delete:
        keyboard.row(
            B("Удалить", callback_data=f"{CBT.CONFIRM_DELETE_PLUGIN}:{uuid}:{offset}"),
            B("Отмена", callback_data=f"{CBT.EDIT_PLUGIN}:{uuid}:{offset}"),
        )
        return keyboard
    if plugin.settings_page:
        keyboard.row(
            B("Настройки", callback_data=f"{CBT.PLUGIN_SETTINGS}:{uuid}:{offset}")
        )
    keyboard.row(
        B(
            "Выключить" if plugin.enabled else "Включить",
            callback_data=f"{CBT.TOGGLE_PLUGIN}:{uuid}:{offset}",
        )
    )
    keyboard.row(
        B(
            "Открепить" if plugin.pinned else "Закрепить",
            callback_data=f"{CBT.PIN_PLUGIN}:{uuid}:{offset}",
        )
    )
    keyboard.row(
        B("Удалить", callback_data=f"{CBT.DELETE_PLUGIN}:{uuid}:{offset}"),
        B("Назад", callback_data=f"{CBT.PLUGINS_LIST}:{offset}"),
    )
    return keyboard
