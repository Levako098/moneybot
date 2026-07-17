from telebot.types import InlineKeyboardButton as B
from telebot.types import InlineKeyboardMarkup as K

from bot.compat.tg_bot import CBT


def CLEAR_STATE_BTN() -> K:
    return K().add(B("Отмена", callback_data=CBT.CLEAR_STATE))


def REFRESH_BTN(callback_data: str = CBT.UPDATE_PROFILE) -> K:
    return K().add(B("Обновить", callback_data=callback_data))


def UPLOAD_PLUGIN() -> K:
    return K().add(B("Отмена", callback_data=f"{CBT.PLUGINS_LIST}:0"))
