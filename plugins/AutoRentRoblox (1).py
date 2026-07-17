
import logging
import sqlite3
import threading
import time
import json
import os
import requests
import uuid
import re
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from FunPayAPI.updater.events import NewMessageEvent, NewOrderEvent
from FunPayAPI.types import MessageTypes

# --- Configuration ---
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(PLUGIN_DIR, "AutoRentVipRoblox.db")
LOG_FILE = os.path.join(PLUGIN_DIR, "AutoRentVipRoblox.log")

# --- Logger Setup ---
logger = logging.getLogger("AutoRentVipRoblox")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    fh = logging.FileHandler(LOG_FILE, encoding='utf-8')
    fh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(fh)

# --- Global State ---
bot = None
cardinal_instance = None
bg_worker_running = False
bg_stop_event = threading.Event()

# --- Command Recognition ---
# Группы синонимов для распознавания команд с опечатками
COMMAND_LINK = [
    "!ссылка", "!link", "ссылка", "ссылку", "сылка", "сылку", "линк", "линка",
    "link", "лінк", "ссылки", "получить ссылку", "дай ссылку", "скинь ссылку",
    "кинь ссылку", "новая ссылка", "новую ссылку", "обнови ссылку", "url"
]

COMMAND_HELP = [
    "!помощь", "!help", "помощь", "помоги", "помогите", "help", "хелп",
    "продавец", "продовец", "позови продавца", "позвать продавца", "админ",
    "администратор", "поддержка", "support", "sos", "сос", "проблема",
    "не работает", "помощ", "помочь"
]

COMMAND_SERVERS = [
    "!сервера", "!servers", "!бронь", "сервера", "серверы", "сервер", "servers",
    "свободно", "свободные", "статус", "status", "бронь", "брони", "занято",
    "сколько серверов", "есть сервера", "есть серверы", "доступные", "доступно"
]

COMMAND_TIME = [
    "!время", "!time", "время", "таймер", "сколько осталось", "осталось",
    "сколько времени", "оставшееся время", "time", "timer", "когда конец",
    "до конца", "истекает"
]

COMMAND_INFO = [
    "!инфо", "!info", "инфо", "информация", "info", "мой заказ", "моя аренда",
    "статус аренды", "детали", "подробности"
]

def match_command(text, command_list):
    """Проверяет, совпадает ли текст с одной из команд в списке"""
    text_lower = text.lower().strip()
    
    # Игнорируем слишком длинные сообщения (это явно не команда)
    if len(text_lower) > 50:
        return False
    
    # Точное совпадение имеет приоритет
    if text_lower in command_list:
        return True
    
    # Проверяем частичное совпадение только для коротких сообщений
    if len(text_lower) <= 30:
        for cmd in command_list:
            if text_lower == cmd:
                return True
            # Проверяем что команда это отдельное слово в начале
            if text_lower.startswith(cmd + " ") or text_lower.startswith(cmd + "?") or text_lower.startswith(cmd + "!"):
                return True
    
    return False

# --- Database Manager ---
class DBManager:
    def __init__(self, db_path):
        self.db_path = db_path
        self.init_db()

    def get_conn(self):
        return sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)

    def init_db(self):
        with self.get_conn() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            # Settings
            conn.execute("""CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )""")
            
            # Accounts
            conn.execute("""CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cookie TEXT,
                proxy TEXT
            )""")

            # Servers
            conn.execute("""CREATE TABLE IF NOT EXISTS servers (
                server_id TEXT PRIMARY KEY,
                status TEXT DEFAULT 'free'
            )""")

            # Lots configuration (mapping LotID -> Duration)
            conn.execute("""CREATE TABLE IF NOT EXISTS lots (
                lot_id INTEGER PRIMARY KEY,
                hours INTEGER,
                active INTEGER DEFAULT 1,
                lot_title TEXT
            )""")
            
            # Добавляем поле lot_title если его нет (для существующих БД)
            try:
                conn.execute("ALTER TABLE lots ADD COLUMN lot_title TEXT")
            except sqlite3.OperationalError:
                pass  # Поле уже существует

            # Orders
            conn.execute("""CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                buyer_id TEXT,
                chat_id TEXT,
                server_id TEXT,
                hours INTEGER,
                start_time REAL,
                end_time REAL,
                status TEXT,
                price REAL DEFAULT 0,
                review_bonus_given INTEGER DEFAULT 0,
                notified_15_min INTEGER DEFAULT 0
            )""")
            
            # Default Settings - УНИКАЛЬНЫЕ СООБЩЕНИЯ
            defaults = {
                "game_id": "109983668079237",
                "admin_id": "0",
                "msg_purchase": "🎉 Ура! Твой VIP сервер активирован!\n\n🔗 {link}\n\n⏱️ Доступно: {hours} ч. аренды\n\n💬 Быстрые команды:\n• \"ссылка\" — обновить ссылку\n• \"время\" — сколько осталось\n• \"помощь\" — связаться с нами\n\n💎 Оставь отзыв и получи бонусное время!\n\nУдачной игры! 🎮✨",
                "msg_expired": "⏳ Время истекло!\n\nБыло круто играть вместе! 🎯\n\nХочешь продолжить? Просто оформи новый заказ — всё автоматически!\n\nЖдём тебя снова! 👋💫",
                "msg_15_min": "⏰ Осталось 15 минут!\n\nТвоя аренда скоро закончится.\n\n🔄 Продлить? Оформи новый заказ — время добавится к текущему!\n\nНе упусти момент! ⚡",
                "msg_review_bonus": "💎 Спасибо за отзыв!\n\n🎁 Тебе начислено: +{hours} ч. бесплатно!\n\nВремя добавлено к твоей аренде. Наслаждайся игрой! 🚀",
                "loyalty_review_hours": "1",
                "loyalty_review_enabled": "1",
                "loyalty_rule_buy": "5",
                "loyalty_rule_get": "1",
                "loyalty_rule_enabled": "0",
                "auto_disable_lots": "1"
            }
            for k, v in defaults.items():
                conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

    def get_setting(self, key, default=None):
        with self.get_conn() as conn:
            cur = conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
            res = cur.fetchone()
            return res[0] if res else default

    def set_setting(self, key, value):
        with self.get_conn() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))

    def add_account(self, cookie):
        with self.get_conn() as conn:
            conn.execute("INSERT INTO accounts (cookie) VALUES (?)", (cookie,))

    def get_accounts(self):
        with self.get_conn() as conn:
            return conn.execute("SELECT id, cookie, proxy FROM accounts").fetchall()
            
    def delete_account(self, acc_id):
        with self.get_conn() as conn:
            conn.execute("DELETE FROM accounts WHERE id = ?", (acc_id,))

    def add_server(self, server_id):
        with self.get_conn() as conn:
            conn.execute("INSERT OR IGNORE INTO servers (server_id) VALUES (?)", (server_id,))

    def get_servers(self):
        with self.get_conn() as conn:
            return conn.execute("SELECT server_id, status FROM servers").fetchall()
            
    def delete_server(self, server_id):
        with self.get_conn() as conn:
            conn.execute("DELETE FROM servers WHERE server_id = ?", (server_id,))

    def add_lot(self, lot_id, hours, lot_title=None):
        with self.get_conn() as conn:
            conn.execute("INSERT OR REPLACE INTO lots (lot_id, hours, lot_title) VALUES (?, ?, ?)", (lot_id, hours, lot_title))

    def get_lots(self):
        with self.get_conn() as conn:
            return conn.execute("SELECT lot_id, hours, active, lot_title FROM lots").fetchall()
    
    def get_lot_by_title(self, title):
        """Находит лот по названию"""
        if not title:
            return None
        title = title.strip()
        if not title:
            return None
        with self.get_conn() as conn:
            # Получаем все активные лоты с названиями
            lots = conn.execute("SELECT lot_id, hours, active, lot_title FROM lots WHERE lot_title IS NOT NULL AND lot_title != '' AND active = 1").fetchall()
            
            # Сначала ищем точное совпадение (без учета регистра)
            for lot in lots:
                lot_title = lot[3].strip() if lot[3] else ""
                if lot_title and lot_title.lower() == title.lower():
                    return lot
            
            # Затем ищем частичное совпадение (название заказа содержит название лота или наоборот)
            for lot in lots:
                lot_title = lot[3].strip() if lot[3] else ""
                if lot_title:
                    if lot_title.lower() in title.lower() or title.lower() in lot_title.lower():
                        return lot
            
            return None

    def toggle_lot(self, lot_id, active):
        with self.get_conn() as conn:
            conn.execute("UPDATE lots SET active = ? WHERE lot_id = ?", (1 if active else 0, lot_id))

    def delete_lot(self, lot_id):
        with self.get_conn() as conn:
            conn.execute("DELETE FROM lots WHERE lot_id = ?", (lot_id,))

    def create_order(self, order_id, buyer_id, chat_id, server_id, hours, price):
        now = time.time()
        end_time = now + (hours * 3600)
        with self.get_conn() as conn:
            exists = conn.execute("SELECT 1 FROM orders WHERE order_id = ?", (order_id,)).fetchone()
            if exists:
                logger.warning(f"Order {order_id} already exists in DB. Skipping insert.")
                return

            conn.execute("""INSERT INTO orders (order_id, buyer_id, chat_id, server_id, hours, start_time, end_time, status, price) 
                            VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)""", 
                            (order_id, buyer_id, chat_id, server_id, hours, now, end_time, price))
            conn.execute("UPDATE servers SET status = 'occupied' WHERE server_id = ?", (server_id,))

    def get_active_order_by_server(self, server_id):
        with self.get_conn() as conn:
            return conn.execute("SELECT * FROM orders WHERE server_id = ? AND status = 'active'", (server_id,)).fetchone()

    def get_active_orders(self):
        with self.get_conn() as conn:
            return conn.execute("SELECT * FROM orders WHERE status = 'active'").fetchall()

    def get_order_by_buyer(self, buyer_id):
        with self.get_conn() as conn:
            return conn.execute("SELECT * FROM orders WHERE buyer_id = ? AND status = 'active'", (buyer_id,)).fetchone()
            
    def get_order_by_chat(self, chat_id, author_id=None):
        """Ищет заказ по chat_id, author_id (buyer_id) или частичному совпадению"""
        with self.get_conn() as conn:
            # 1. Сначала ищем по author_id = buyer_id (самый надежный способ)
            if author_id:
                result = conn.execute("SELECT * FROM orders WHERE buyer_id = ? AND status = 'active'", (str(author_id),)).fetchone()
                if result:
                    return result
            
            # 2. Ищем по точному chat_id
            result = conn.execute("SELECT * FROM orders WHERE chat_id = ? AND status = 'active'", (str(chat_id),)).fetchone()
            if result:
                return result
            
            # 3. Ищем по buyer_id = chat_id
            result = conn.execute("SELECT * FROM orders WHERE buyer_id = ? AND status = 'active'", (str(chat_id),)).fetchone()
            if result:
                return result
                
            # 4. Ищем где chat_id содержит наш ID (для формата "users-xxx-yyy")
            result = conn.execute("SELECT * FROM orders WHERE chat_id LIKE ? AND status = 'active'", (f"%{chat_id}%",)).fetchone()
            return result

    def get_order_by_id(self, order_id):
        with self.get_conn() as conn:
            return conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()

    def end_order(self, order_id):
        with self.get_conn() as conn:
            ord = conn.execute("SELECT server_id FROM orders WHERE order_id = ?", (order_id,)).fetchone()
            if ord:
                server_id = ord[0]
                conn.execute("UPDATE orders SET status = 'completed' WHERE order_id = ?", (order_id,))
                conn.execute("UPDATE servers SET status = 'free' WHERE server_id = ?", (server_id,))

    def update_order_time(self, order_id, extra_hours):
        with self.get_conn() as conn:
            ord = conn.execute("SELECT end_time, hours FROM orders WHERE order_id = ?", (order_id,)).fetchone()
            if ord:
                new_end = ord[0] + (extra_hours * 3600)
                new_hours = ord[1] + extra_hours
                conn.execute("UPDATE orders SET end_time = ?, hours = ? WHERE order_id = ?", (new_end, new_hours, order_id))

    def set_review_bonus_given(self, order_id, given=True):
        with self.get_conn() as conn:
            conn.execute("UPDATE orders SET review_bonus_given = ? WHERE order_id = ?", (1 if given else 0, order_id))

    def set_notified_15_min(self, order_id):
        with self.get_conn() as conn:
            conn.execute("UPDATE orders SET notified_15_min = 1 WHERE order_id = ?", (order_id,))

    def get_free_server(self):
        with self.get_conn() as conn:
            res = conn.execute("SELECT server_id FROM servers WHERE status = 'free'").fetchone()
            if res:
                return res[0]
            return None

    def get_total_earnings(self):
        with self.get_conn() as conn:
            res = conn.execute("SELECT SUM(price) FROM orders WHERE price > 0").fetchone()
            return res[0] if res[0] else 0

    def get_total_orders_count(self):
         with self.get_conn() as conn:
            res = conn.execute("SELECT COUNT(*) FROM orders").fetchone()
            return res[0] if res else 0

    def get_completed_orders_count(self):
         with self.get_conn() as conn:
            res = conn.execute("SELECT COUNT(*) FROM orders WHERE status = 'completed'").fetchone()
            return res[0] if res else 0

    def get_active_orders_count(self):
         with self.get_conn() as conn:
            res = conn.execute("SELECT COUNT(*) FROM orders WHERE status = 'active'").fetchone()
            return res[0] if res else 0

db = DBManager(DB_FILE)

# --- Roblox API ---
class RobloxAPI:
    @staticmethod
    def prepare_request(cookie, proxy=None):
        cookie = cookie.strip()
        if "_|WARNING:-DO-NOT-SHARE" in cookie:
             try:
                parts = cookie.split("_|WARNING:-DO-NOT-SHARE")
                cookie = parts[0] + "_|WARNING:-DO-NOT-SHARE" + parts[1]
             except: pass
        
        headers = {
            "Cookie": f".ROBLOSECURITY={cookie}" if not cookie.startswith(".ROBLOSECURITY=") else cookie,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Content-Type": "application/json",
            "Origin": "https://www.roblox.com",
            "Referer": "https://www.roblox.com/"
        }
        proxies = {"http": proxy, "https": proxy} if proxy else None
        return headers, proxies

    @staticmethod
    def get_username(cookie, proxy=None):
        headers, proxies = RobloxAPI.prepare_request(cookie, proxy)
        try:
            r = requests.get("https://users.roblox.com/v1/users/authenticated", headers=headers, proxies=proxies, timeout=10)
            if r.status_code == 200:
                data = r.json()
                return data.get("name") or data.get("displayName") or "Unknown"
        except: pass
        return "Unknown"

    @staticmethod
    def get_game_name(game_id):
        if str(game_id) == "109983668079237":
            return "Steal a Brainrot"
        return str(game_id)

    @staticmethod
    def regenerate_link(cookie, server_id):
        headers, proxies = RobloxAPI.prepare_request(cookie)
        url = f"https://games.roblox.com/v1/vip-servers/{server_id}"
        
        for _ in range(3):
            try:
                r = requests.patch(url, headers=headers, json={"newJoinCode": True}, proxies=proxies, timeout=10)
                if r.status_code == 403 and "x-csrf-token" in r.headers:
                    headers["x-csrf-token"] = r.headers["x-csrf-token"]
                    continue
                if r.status_code == 200:
                    return r.json().get("link")
            except Exception as e:
                logger.error(f"Regen link error: {e}")
            time.sleep(1)
        return None

    @staticmethod
    def shutdown_server(cookie, server_id, place_id):
        headers, proxies = RobloxAPI.prepare_request(cookie)
        url = "https://apis.roblox.com/matchmaking-api/v1/game-instances/shutdown"
        game_id = str(uuid.uuid4())
        payload = {"placeId": int(place_id), "privateServerId": int(server_id), "gameId": game_id}

        for _ in range(3):
            try:
                r = requests.post(url, headers=headers, json=payload, proxies=proxies, timeout=10)
                if r.status_code == 403 and "x-csrf-token" in r.headers:
                    headers["x-csrf-token"] = r.headers["x-csrf-token"]
                    continue
                if r.status_code == 200:
                    return True
            except Exception as e:
                logger.error(f"Shutdown error: {e}")
            time.sleep(1)
        return False


# ═══════════════════════════════════════════════════════════════
#                      КРАСИВАЯ АДМИН-ПАНЕЛЬ
# ═══════════════════════════════════════════════════════════════

def format_time_left(seconds):
    """Форматирует время в читаемый вид"""
    if seconds <= 0:
        return "Истекло"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    if hours > 0:
        return f"{hours}ч {minutes}м"
    return f"{minutes}м"

def create_progress_bar(current, total, length=10):
    """Создает визуальную шкалу прогресса"""
    if total == 0:
        return "░" * length
    filled = int(length * current / total)
    return "█" * filled + "░" * (length - filled)

def send_main_menu(chat_id):
    """Главное меню админ-панели"""
    # Собираем статистику
    accs = db.get_accounts()
    srvs = db.get_servers()
    total_srv = len(srvs)
    free_srv = len([s for s in srvs if s[1] == 'free'])
    occupied_srv = total_srv - free_srv
    
    earnings = db.get_total_earnings()
    total_orders = db.get_total_orders_count()
    active_orders = db.get_active_orders_count()
    completed_orders = db.get_completed_orders_count()
    
    # Получаем имя аккаунта если есть
    acc_info = "Не добавлен"
    if accs:
        acc_name = RobloxAPI.get_username(accs[0][1], accs[0][2])
        acc_info = f"{acc_name}" if acc_name != "Unknown" else f"{len(accs)} шт."
    
    # Формируем красивое сообщение
    text = """
╔══════════════════════════════════╗
       🎮  AUTO RENT VIP ROBLOX
╚══════════════════════════════════╝

┌─────────── 📊 СТАТИСТИКА ───────────┐

   👤  Аккаунт:  {acc_info}
   
   🖥️  Сервера:  {free}/{total} свободно
       {srv_bar}
   
   💰  Заработано:  {earnings} ₽
   
   📦  Заказы:
       ├ Активных: {active}
       ├ Завершено: {completed}
       └ Всего: {total_orders}

└─────────────────────────────────────┘
""".format(
        acc_info=acc_info,
        free=free_srv,
        total=total_srv,
        srv_bar=create_progress_bar(free_srv, total_srv) if total_srv > 0 else "нет серверов",
        earnings=int(earnings),
        active=active_orders,
        completed=completed_orders,
        total_orders=total_orders
    )

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("👤 Аккаунты", callback_data="ar_accs"),
        InlineKeyboardButton("🎮 Игра", callback_data="ar_game")
    )
    kb.add(
        InlineKeyboardButton("🖥️ Сервера", callback_data="ar_srvs"),
        InlineKeyboardButton("📦 Лоты", callback_data="ar_lots")
    )
    kb.add(
        InlineKeyboardButton("💬 Сообщения", callback_data="ar_msgs"),
        InlineKeyboardButton("🎁 Лояльность", callback_data="ar_loyal")
    )
    kb.add(
        InlineKeyboardButton("📋 Активные заказы", callback_data="ar_active_orders")
    )
    kb.add(
        InlineKeyboardButton("📄 Логи", callback_data="ar_logs"),
    )
    
    bot.send_message(chat_id, text, reply_markup=kb, parse_mode=None)


def handle_admin_callback(c):
    cid = c.message.chat.id
    mid = c.message.message_id
    data = c.data

    # ══════════════════════════════════════
    #            ГЛАВНОЕ МЕНЮ
    # ══════════════════════════════════════
    
    if data == "ar_menu":
        bot.delete_message(cid, mid)
        send_main_menu(cid)
    
    # ══════════════════════════════════════
    #              АККАУНТЫ
    # ══════════════════════════════════════
    
    elif data == "ar_accs":
        accs = db.get_accounts()
        
        text = """
╔═══════════════════════════════════╗
          👤  УПРАВЛЕНИЕ АККАУНТАМИ
╚═══════════════════════════════════╝

"""
        if not accs:
            text += """
   ⚠️  Аккаунты не добавлены
   
   Добавьте Roblox аккаунт для работы
   плагина. Нужен Cookie (.ROBLOSECURITY)
"""
        else:
            for i, a in enumerate(accs, 1):
                name = RobloxAPI.get_username(a[1], a[2])
                cookie_preview = a[1][:20] + "..." if len(a[1]) > 20 else a[1]
                status = "🟢" if name != "Unknown" else "🔴"
                text += f"""   {status}  #{i}  {name}
       ID: {a[0]}
       Cookie: {cookie_preview}
       
"""
        
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(InlineKeyboardButton("➕ Добавить аккаунт", callback_data="ar_acc_add"))
        if accs:
            kb.add(InlineKeyboardButton("🗑️ Удалить аккаунт", callback_data="ar_acc_del"))
        kb.add(InlineKeyboardButton("🔙 Назад", callback_data="ar_menu"))
        
        bot.edit_message_text(text, cid, mid, reply_markup=kb, parse_mode=None)

    elif data == "ar_acc_add":
        msg = bot.send_message(cid, """
📝 Отправьте Cookie аккаунта Roblox

Формат: .ROBLOSECURITY=_|WARNING:...

Как получить:
1. Откройте roblox.com
2. F12 → Application → Cookies
3. Скопируйте значение .ROBLOSECURITY
""")
        bot.register_next_step_handler(msg, process_add_acc)
    
    elif data == "ar_acc_del":
        accs = db.get_accounts()
        kb = InlineKeyboardMarkup(row_width=1)
        for a in accs:
            name = RobloxAPI.get_username(a[1], a[2])
            kb.add(InlineKeyboardButton(f"🗑️ {name} (ID: {a[0]})", callback_data=f"ar_acc_del_{a[0]}"))
        kb.add(InlineKeyboardButton("🔙 Назад", callback_data="ar_accs"))
        bot.edit_message_text("Выберите аккаунт для удаления:", cid, mid, reply_markup=kb)

    elif data.startswith("ar_acc_del_"):
        acc_id = data.split("_")[3]
        db.delete_account(acc_id)
        bot.answer_callback_query(c.id, "✅ Аккаунт удален")
        # Возвращаемся к списку аккаунтов
        c.data = "ar_accs"
        handle_admin_callback(c)

    # ══════════════════════════════════════
    #                 ИГРА
    # ══════════════════════════════════════
    
    elif data == "ar_game":
        curr = db.get_setting("game_id")
        name = RobloxAPI.get_game_name(curr)
        
        text = f"""
╔═══════════════════════════════════╗
            🎮  НАСТРОЙКИ ИГРЫ
╚═══════════════════════════════════╝

   📍  Текущая игра:
   
       {name}
       ID: {curr}
       
   ℹ️  Game ID используется для
       управления VIP серверами
"""
        
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("✏️ Изменить Game ID", callback_data="ar_game_edit"))
        kb.add(InlineKeyboardButton("🔙 Назад", callback_data="ar_menu"))
        bot.edit_message_text(text, cid, mid, reply_markup=kb, parse_mode=None)

    elif data == "ar_game_edit":
        msg = bot.send_message(cid, """
📝 Введите новый Game ID

Как узнать Game ID:
1. Откройте игру на Roblox
2. Скопируйте ID из URL или настроек VIP сервера
""")
        bot.register_next_step_handler(msg, lambda m: [db.set_setting("game_id", m.text.strip()), bot.send_message(cid, "✅ Game ID обновлен!"), send_main_menu(cid)])

    # ══════════════════════════════════════
    #              СЕРВЕРА
    # ══════════════════════════════════════
    
    elif data == "ar_srvs":
        srvs = db.get_servers()
        total = len(srvs)
        free = len([s for s in srvs if s[1] == 'free'])
        occupied = total - free
        
        text = f"""
╔═══════════════════════════════════╗
          🖥️  УПРАВЛЕНИЕ СЕРВЕРАМИ
╚═══════════════════════════════════╝

   📊  Статистика:
       🟢 Свободно: {free}
       🔴 Занято: {occupied}
       📦 Всего: {total}

   ─────────── СПИСОК ───────────
   
"""
        if not srvs:
            text += "   ⚠️  Сервера не добавлены\n"
        else:
            for s in srvs:
                status_icon = "🟢" if s[1] == 'free' else "🔴"
                status_text = "Свободен" if s[1] == 'free' else "Занят"
                
                # Если занят - показываем время до конца
                time_info = ""
                if s[1] != 'free':
                    order = db.get_active_order_by_server(s[0])
                    if order:
                        time_left = int(order[6] - time.time())
                        time_info = f" ({format_time_left(time_left)})"
                
                text += f"   {status_icon}  {s[0][:15]}...  {status_text}{time_info}\n"
            
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("➕ Добавить", callback_data="ar_srv_add"),
            InlineKeyboardButton("⚙️ Управление", callback_data="ar_srv_manage")
        )
        kb.add(InlineKeyboardButton("🗑️ Удалить сервер", callback_data="ar_srv_del_menu"))
        kb.add(InlineKeyboardButton("🔙 Назад", callback_data="ar_menu"))
        bot.edit_message_text(text, cid, mid, reply_markup=kb, parse_mode=None)

    elif data == "ar_srv_add":
        msg = bot.send_message(cid, """
📝 Введите Server ID

Как узнать Server ID:
1. Откройте настройки VIP сервера на Roblox
2. Server ID находится в URL страницы настроек
   (например: .../configure?id=XXXXX)
""")
        bot.register_next_step_handler(msg, lambda m: [db.add_server(m.text.strip()), bot.send_message(cid, "✅ Сервер добавлен!"), send_main_menu(cid)])

    elif data == "ar_srv_del_menu":
        srvs = db.get_servers()
        if not srvs:
            bot.answer_callback_query(c.id, "Нет серверов для удаления")
            return
        kb = InlineKeyboardMarkup(row_width=1)
        for s in srvs:
            status_icon = "🟢" if s[1] == 'free' else "🔴"
            kb.add(InlineKeyboardButton(f"{status_icon} {s[0][:20]}...", callback_data=f"ar_srv_del_{s[0]}"))
        kb.add(InlineKeyboardButton("🔙 Назад", callback_data="ar_srvs"))
        bot.edit_message_text("🗑️ Выберите сервер для удаления:", cid, mid, reply_markup=kb)

    elif data.startswith("ar_srv_del_") and not data.startswith("ar_srv_del_menu"):
        sid = data[11:]  # Убираем "ar_srv_del_"
        db.delete_server(sid)
        bot.answer_callback_query(c.id, "✅ Сервер удален")
        c.data = "ar_srvs"
        handle_admin_callback(c)
    
    elif data == "ar_srv_manage":
        srvs = db.get_servers()
        if not srvs:
            bot.answer_callback_query(c.id, "Нет серверов")
            return
            
        kb = InlineKeyboardMarkup(row_width=1)
        for s in srvs:
            sid = s[0]
            status = s[1]
            
            time_str = ""
            buyer_info = ""
            if status != 'free':
                order = db.get_active_order_by_server(sid)
                if order:
                    time_left = int(order[6] - time.time())
                    time_str = format_time_left(time_left)
                    buyer_info = f" | {order[1][:10]}..."
            
            status_icon = "🟢" if status == 'free' else "🔴"
            display = f"{status_icon} {sid[:12]}..."
            if time_str:
                display += f" | ⏱ {time_str}{buyer_info}"
            
            kb.add(InlineKeyboardButton(display, callback_data=f"ar_sm_{sid}"))
            
        kb.add(InlineKeyboardButton("🔙 Назад", callback_data="ar_srvs"))
        
        text = """
╔═══════════════════════════════════╗
           ⚙️  УПРАВЛЕНИЕ СЕРВЕРАМИ
╚═══════════════════════════════════╝

   Выберите сервер для управления:
"""
        bot.edit_message_text(text, cid, mid, reply_markup=kb, parse_mode=None)

    elif data.startswith("ar_sm_"):
        sid = data[6:]  # Убираем "ar_sm_"
        order = db.get_active_order_by_server(sid)
        
        if order:
            time_left = int(order[6] - time.time())
            text = f"""
╔═══════════════════════════════════╗
           ⚙️  СЕРВЕР: {sid[:15]}...
╚═══════════════════════════════════╝

   📋  Информация о заказе:
   
       🆔 Заказ: {order[0]}
       👤 Покупатель: {order[1]}
       ⏱️ Осталось: {format_time_left(time_left)}
       💰 Цена: {order[8]} ₽
       
   🎮  Статус: 🔴 Занят
"""
        else:
            text = f"""
╔═══════════════════════════════════╗
           ⚙️  СЕРВЕР: {sid[:15]}...
╚═══════════════════════════════════╝

   🎮  Статус: 🟢 Свободен
   
   Нет активных заказов
"""
        
        kb = InlineKeyboardMarkup(row_width=2)
        if order:
            kb.add(
                InlineKeyboardButton("➕ Время (+1ч)", callback_data=f"ar_st_{sid}_add"),
                InlineKeyboardButton("➖ Время (-1ч)", callback_data=f"ar_st_{sid}_sub")
            )
            kb.add(
                InlineKeyboardButton("💰 Завершить (Возврат)", callback_data=f"ar_sf_{sid}_ref")
            )
            kb.add(
                InlineKeyboardButton("🛑 Завершить (Без возврата)", callback_data=f"ar_sf_{sid}_noref")
            )
        kb.add(InlineKeyboardButton("🔄 Обновить ссылку", callback_data=f"ar_srl_{sid}"))
        kb.add(InlineKeyboardButton("🔙 Назад", callback_data="ar_srv_manage"))
        bot.edit_message_text(text, cid, mid, reply_markup=kb, parse_mode=None)

    elif data.startswith("ar_srl_"):
        sid = data[7:]
        accs = db.get_accounts()
        if accs:
            link = RobloxAPI.regenerate_link(accs[0][1], sid)
            if link:
                bot.answer_callback_query(c.id, "✅ Ссылка обновлена!")
            else:
                bot.answer_callback_query(c.id, "❌ Ошибка обновления ссылки")
        else:
            bot.answer_callback_query(c.id, "❌ Нет аккаунтов")

    elif data.startswith("ar_st_"):
        parts = data.split("_")
        sid = parts[2]
        action = parts[3]
        
        order = db.get_active_order_by_server(sid)
        if not order:
            bot.answer_callback_query(c.id, "❌ Нет активного заказа!")
            return

        chat_id = order[2]
        if action == "add":
            db.update_order_time(order[0], 1)
            try:
                cardinal_instance.account.send_message(chat_id, "🎊 Подарок от администратора!\n\n➕ К твоей аренде добавлен 1 час!\n\nПродолжай наслаждаться игрой! 🎮💫")
            except: pass
            bot.answer_callback_query(c.id, "✅ +1 час добавлен")
        
        elif action == "sub":
            db.update_order_time(order[0], -1)
            try:
                cardinal_instance.account.send_message(chat_id, "📉 Изменение времени\n\nВремя аренды уменьшено на 1 час администратором.\n\nЕсли есть вопросы — напиши \"помощь\".")
            except: pass
            bot.answer_callback_query(c.id, "✅ -1 час")
        
        # Обновляем меню сервера
        c.data = f"ar_sm_{sid}"
        handle_admin_callback(c)

    elif data.startswith("ar_sf_"):
        parts = data.split("_")
        sid = parts[2]
        action = parts[3]

        order = db.get_active_order_by_server(sid)
        if not order:
            bot.answer_callback_query(c.id, "❌ Нет активного заказа!")
            return

        oid = order[0]
        chat_id = order[2]

        if action == "ref":
            try:
                cardinal_instance.account.refund(oid)
                cardinal_instance.account.send_message(chat_id, "🔄 Аренда остановлена\n\n💵 Твои деньги возвращены на баланс FunPay.\n\nИзвиняемся за неудобства! Если что-то не так — напиши нам.")
            except Exception as e:
                bot.send_message(cid, f"❌ Ошибка возврата: {e}")
        else:
            try:
                cardinal_instance.account.send_message(chat_id, "🏁 Аренда завершена\n\nАдминистратор завершил твою аренду.\n\nСпасибо за использование нашего сервиса! До новых встреч! 👋")
            except: pass

        db.end_order(oid)
        
        # Выключаем сервер и обновляем ссылку
        accs = db.get_accounts()
        if accs:
            cookie = accs[0][1]
            place_id = db.get_setting("game_id")
            RobloxAPI.shutdown_server(cookie, sid, place_id)
            RobloxAPI.regenerate_link(cookie, sid)
        
        bot.answer_callback_query(c.id, "✅ Заказ завершен")
        bot.delete_message(cid, mid)
        send_main_menu(cid)

    # ══════════════════════════════════════
    #                 ЛОТЫ
    # ══════════════════════════════════════
    
    elif data == "ar_lots":
        lots = db.get_lots()
        
        text = """
╔═══════════════════════════════════╗
            📦  УПРАВЛЕНИЕ ЛОТАМИ
╚═══════════════════════════════════╝

   ℹ️  Лоты связывают товары FunPay
       с количеством часов аренды

   ─────────── СПИСОК ───────────
   
"""
        if not lots:
            text += "   ⚠️  Лоты не добавлены\n"
        else:
            for l in lots:
                status = "🟢 ВКЛ" if l[2] else "🔴 ВЫКЛ"
                title = l[3] if len(l) > 3 and l[3] else "Без названия"
                text += f"   📦  ID: {l[0]}  |  {l[1]} ч.  |  {status}\n"
                if title and title != "Без названия":
                    text += f"       📝 {title[:40]}{'...' if len(title) > 40 else ''}\n"
        
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(InlineKeyboardButton("➕ Добавить лот", callback_data="ar_lot_add"))
        if lots:
            kb.add(
                InlineKeyboardButton("🔄 Вкл/Выкл", callback_data="ar_lot_tog"),
                InlineKeyboardButton("🗑️ Удалить", callback_data="ar_lot_del_menu")
            )
        kb.add(InlineKeyboardButton("🔙 Назад", callback_data="ar_menu"))
        bot.edit_message_text(text, cid, mid, reply_markup=kb, parse_mode=None)

    elif data == "ar_lot_add":
        msg = bot.send_message(cid, """
📝 Введите данные лота

Формат: ID_ЛОТА ЧАСЫ

Пример: 123456 24

Где взять ID лота:
Откройте лот на FunPay, ID в URL
""")
        bot.register_next_step_handler(msg, process_add_lot)

    elif data == "ar_lot_tog":
        lots = db.get_lots()
        kb = InlineKeyboardMarkup(row_width=1)
        for l in lots:
            status = "🟢" if l[2] else "🔴"
            action = "выкл" if l[2] else "вкл"
            title = l[3] if len(l) > 3 and l[3] else ""
            title_text = f" - {title[:25]}..." if title and len(title) > 25 else (f" - {title}" if title else "")
            kb.add(InlineKeyboardButton(f"{status} ID: {l[0]} ({l[1]}ч){title_text} → {action}", callback_data=f"ar_lt_{l[0]}"))
        kb.add(InlineKeyboardButton("🔙 Назад", callback_data="ar_lots"))
        bot.edit_message_text("Выберите лот для переключения:", cid, mid, reply_markup=kb)

    elif data.startswith("ar_lt_"):
        lot_id = int(data[6:])
        lots = db.get_lots()
        for l in lots:
            if l[0] == lot_id:
                new_state = not bool(l[2])
                db.toggle_lot(lot_id, new_state)
                try:
                    lf = cardinal_instance.account.get_lot_fields(lot_id)
                    lf.active = new_state
                    cardinal_instance.account.save_lot(lf)
                except: pass
                break
        bot.answer_callback_query(c.id, "✅ Статус изменен")
        c.data = "ar_lots"
        handle_admin_callback(c)

    elif data == "ar_lot_del_menu":
        lots = db.get_lots()
        kb = InlineKeyboardMarkup(row_width=1)
        for l in lots:
            title = l[3] if len(l) > 3 and l[3] else ""
            title_text = f" - {title[:25]}..." if title and len(title) > 25 else (f" - {title}" if title else "")
            kb.add(InlineKeyboardButton(f"🗑️ ID: {l[0]} ({l[1]}ч){title_text}", callback_data=f"ar_ld_{l[0]}"))
        kb.add(InlineKeyboardButton("🔙 Назад", callback_data="ar_lots"))
        bot.edit_message_text("Выберите лот для удаления:", cid, mid, reply_markup=kb)

    elif data.startswith("ar_ld_"):
        lot_id = int(data[6:])
        db.delete_lot(lot_id)
        bot.answer_callback_query(c.id, "✅ Лот удален")
        c.data = "ar_lots"
        handle_admin_callback(c)

    # ══════════════════════════════════════
    #              ЛОЯЛЬНОСТЬ
    # ══════════════════════════════════════
    
    elif data == "ar_loyal":
        text = """
╔═══════════════════════════════════╗
           🎁  ПРОГРАММА ЛОЯЛЬНОСТИ
╚═══════════════════════════════════╝

   Настройте бонусы для покупателей
"""
        kb = InlineKeyboardMarkup(row_width=1)
        
        # Статус бонуса за отзыв
        rev_en = db.get_setting("loyalty_review_enabled") == "1"
        rev_hrs = db.get_setting("loyalty_review_hours")
        rev_status = f"🟢 +{rev_hrs}ч" if rev_en else "🔴 ВЫКЛ"
        kb.add(InlineKeyboardButton(f"⭐ Бонус за отзыв ({rev_status})", callback_data="ar_loy_rev"))
        
        # Статус правила покупок
        rule_en = db.get_setting("loyalty_rule_enabled") == "1"
        rule_buy = db.get_setting("loyalty_rule_buy")
        rule_get = db.get_setting("loyalty_rule_get")
        rule_status = f"🟢 {rule_buy}ч→+{rule_get}ч" if rule_en else "🔴 ВЫКЛ"
        kb.add(InlineKeyboardButton(f"📈 Бонус за покупку ({rule_status})", callback_data="ar_loy_rule"))
        
        kb.add(InlineKeyboardButton("🔙 Назад", callback_data="ar_menu"))
        bot.edit_message_text(text, cid, mid, reply_markup=kb, parse_mode=None)

    elif data == "ar_loy_rev":
        en = db.get_setting("loyalty_review_enabled") == "1"
        hrs = db.get_setting("loyalty_review_hours")
        
        status = "🟢 Включено" if en else "🔴 Выключено"
        text = f"""
╔═══════════════════════════════════╗
           ⭐  БОНУС ЗА ОТЗЫВ
╚═══════════════════════════════════╝

   📊  Статус: {status}
   ⏰  Бонус: +{hrs} час(ов)
   
   ℹ️  Покупатель получает бонусное
       время после оставления отзыва
"""
        
        kb = InlineKeyboardMarkup(row_width=2)
        toggle_text = "🔴 Выключить" if en else "🟢 Включить"
        kb.add(InlineKeyboardButton(toggle_text, callback_data="ar_lr_tog"))
        kb.add(InlineKeyboardButton("✏️ Изменить часы", callback_data="ar_lr_set"))
        kb.add(InlineKeyboardButton("🔙 Назад", callback_data="ar_loyal"))
        bot.edit_message_text(text, cid, mid, reply_markup=kb, parse_mode=None)

    elif data == "ar_lr_tog":
        curr = db.get_setting("loyalty_review_enabled") == "1"
        db.set_setting("loyalty_review_enabled", "0" if curr else "1")
        bot.answer_callback_query(c.id, "✅ Статус изменен")
        c.data = "ar_loy_rev"
        handle_admin_callback(c)

    elif data == "ar_lr_set":
        msg = bot.send_message(cid, "📝 Введите количество бонусных часов за отзыв:")
        bot.register_next_step_handler(msg, lambda m: [db.set_setting("loyalty_review_hours", m.text.strip()), bot.send_message(cid, "✅ Сохранено!"), send_main_menu(cid)])

    elif data == "ar_loy_rule":
        en = db.get_setting("loyalty_rule_enabled") == "1"
        buy = db.get_setting("loyalty_rule_buy")
        get = db.get_setting("loyalty_rule_get")
        
        status = "🟢 Включено" if en else "🔴 Выключено"
        text = f"""
╔═══════════════════════════════════╗
           📈  БОНУС ЗА ПОКУПКУ
╚═══════════════════════════════════╝

   📊  Статус: {status}
   
   📋  Правило:
       Купи {buy}+ часов → получи +{get} ч. бесплатно
   
   ℹ️  Автоматически добавляет бонус
       при покупке от указанного кол-ва
"""
        
        kb = InlineKeyboardMarkup(row_width=2)
        toggle_text = "🔴 Выключить" if en else "🟢 Включить"
        kb.add(InlineKeyboardButton(toggle_text, callback_data="ar_lru_tog"))
        kb.add(InlineKeyboardButton("✏️ Изменить правило", callback_data="ar_lru_set"))
        kb.add(InlineKeyboardButton("🔙 Назад", callback_data="ar_loyal"))
        bot.edit_message_text(text, cid, mid, reply_markup=kb, parse_mode=None)

    elif data == "ar_lru_tog":
        curr = db.get_setting("loyalty_rule_enabled") == "1"
        db.set_setting("loyalty_rule_enabled", "0" if curr else "1")
        bot.answer_callback_query(c.id, "✅ Статус изменен")
        c.data = "ar_loy_rule"
        handle_admin_callback(c)

    elif data == "ar_lru_set":
        msg = bot.send_message(cid, """
📝 Введите правило бонуса

Формат: КУПИ ПОЛУЧИ

Пример: 5 1
(при покупке 5+ часов → +1 час бесплатно)
""")
        bot.register_next_step_handler(msg, process_loyalty_rule)

    # ══════════════════════════════════════
    #              СООБЩЕНИЯ
    # ══════════════════════════════════════
    
    elif data == "ar_msgs":
        text = """
╔═══════════════════════════════════╗
          💬  НАСТРОЙКА СООБЩЕНИЙ
╚═══════════════════════════════════╝

   Настройте автоматические сообщения
   покупателям

   Доступные переменные:
   {link} — ссылка на сервер
   {hours} — количество часов
"""
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(InlineKeyboardButton("🛒 После покупки", callback_data="ar_msg_buy"))
        kb.add(InlineKeyboardButton("⏰ Окончание аренды", callback_data="ar_msg_end"))
        kb.add(InlineKeyboardButton("⚠️ 15 минут до конца", callback_data="ar_msg_15"))
        kb.add(InlineKeyboardButton("⭐ Бонус за отзыв", callback_data="ar_msg_rev"))
        kb.add(InlineKeyboardButton("🔙 Назад", callback_data="ar_menu"))
        bot.edit_message_text(text, cid, mid, reply_markup=kb, parse_mode=None)
    
    elif data == "ar_msg_buy":
        current = db.get_setting('msg_purchase')
        msg = bot.send_message(cid, f"📝 Текущее сообщение после покупки:\n\n{current}\n\n─────────────────\n\nВведите новое сообщение (или /cancel для отмены):\n\nПеременные: {{link}}, {{hours}}")
        bot.register_next_step_handler(msg, lambda m: process_msg_update(m, 'msg_purchase'))

    elif data == "ar_msg_end":
        current = db.get_setting('msg_expired')
        msg = bot.send_message(cid, f"📝 Текущее сообщение окончания:\n\n{current}\n\n─────────────────\n\nВведите новое сообщение (или /cancel для отмены):")
        bot.register_next_step_handler(msg, lambda m: process_msg_update(m, 'msg_expired'))

    elif data == "ar_msg_15":
        current = db.get_setting('msg_15_min')
        msg = bot.send_message(cid, f"📝 Текущее сообщение 15 минут:\n\n{current}\n\n─────────────────\n\nВведите новое сообщение (или /cancel для отмены):")
        bot.register_next_step_handler(msg, lambda m: process_msg_update(m, 'msg_15_min'))
    
    elif data == "ar_msg_rev":
        current = db.get_setting('msg_review_bonus')
        msg = bot.send_message(cid, f"📝 Текущее сообщение бонуса:\n\n{current}\n\n─────────────────\n\nВведите новое сообщение (или /cancel для отмены):\n\nПеременные: {{hours}}")
        bot.register_next_step_handler(msg, lambda m: process_msg_update(m, 'msg_review_bonus'))

    # ══════════════════════════════════════
    #           АКТИВНЫЕ ЗАКАЗЫ
    # ══════════════════════════════════════
    
    elif data == "ar_active_orders":
        orders = db.get_active_orders()
        
        text = """
╔═══════════════════════════════════╗
           📋  АКТИВНЫЕ ЗАКАЗЫ
╚═══════════════════════════════════╝

"""
        if not orders:
            text += "   ℹ️  Нет активных заказов\n"
        else:
            for o in orders:
                time_left = int(o[6] - time.time())
                time_str = format_time_left(time_left)
                text += f"""   ─────────────────────────────
   🆔 {o[0][:15]}...
   👤 {o[1][:20]}
   ⏱️ Осталось: {time_str}
   💰 {o[8]} ₽
"""
        
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔄 Обновить", callback_data="ar_active_orders"))
        kb.add(InlineKeyboardButton("🔙 Назад", callback_data="ar_menu"))
        bot.edit_message_text(text, cid, mid, reply_markup=kb, parse_mode=None)

    # ══════════════════════════════════════
    #                ЛОГИ
    # ══════════════════════════════════════
    
    elif data == "ar_logs":
        text = """
╔═══════════════════════════════════╗
              📄  ЛОГИ ПЛАГИНА
╚═══════════════════════════════════╝

   Управление файлом логов
"""
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("📥 Скачать", callback_data="ar_log_dl"),
            InlineKeyboardButton("🗑️ Очистить", callback_data="ar_log_clr")
        )
        kb.add(InlineKeyboardButton("⚠️ Последние ошибки", callback_data="ar_log_err"))
        kb.add(InlineKeyboardButton("🔙 Назад", callback_data="ar_menu"))
        bot.edit_message_text(text, cid, mid, reply_markup=kb, parse_mode=None)
    
    elif data == "ar_log_dl":
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 0:
            with open(LOG_FILE, 'rb') as f:
                bot.send_document(cid, f, caption="📄 Лог файл AutoRentVipRoblox")
        else:
            bot.answer_callback_query(c.id, "📄 Лог файл пуст")

    elif data == "ar_log_clr":
        try:
            with open(LOG_FILE, 'w', encoding='utf-8') as f:
                f.write("")
            bot.answer_callback_query(c.id, "✅ Логи очищены")
        except:
            bot.answer_callback_query(c.id, "❌ Ошибка очистки")

    elif data == "ar_log_err":
        try:
            errors = []
            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, 'r', encoding='utf-8') as f:
                    for line in f:
                        if "ERROR" in line:
                            errors.append(line.strip())
            
            if errors:
                msg_text = "⚠️ Последние 5 ошибок:\n\n" + "\n\n".join(errors[-5:])
            else:
                msg_text = "✅ Ошибок не найдено!"
            
            bot.send_message(cid, msg_text)
        except Exception as e:
            bot.send_message(cid, f"❌ Ошибка чтения логов: {e}")


def process_add_acc(m):
    cookie = m.text.strip()
    if cookie.startswith("/"):
        return
    db.add_account(cookie)
    name = RobloxAPI.get_username(cookie)
    bot.send_message(m.chat.id, f"✅ Аккаунт добавлен!\n\n👤 Имя: {name}")
    send_main_menu(m.chat.id)

def process_add_lot(m):
    try:
        parts = m.text.strip().split()
        if len(parts) < 2:
            bot.send_message(m.chat.id, "❌ Неверный формат. Используйте: ID_ЛОТА ЧАСЫ")
            return
        lid = int(parts[0])
        hrs = int(parts[1])
        
        # Получаем название лота
        lot_title = None
        try:
            if cardinal_instance and cardinal_instance.account:
                lot_fields = cardinal_instance.account.get_lot_fields(lid)
                # Пробуем получить русское название, если нет - английское
                lot_title = lot_fields.title_ru or lot_fields.title_en
                if not lot_title:
                    # Пробуем получить из fields напрямую
                    fields = lot_fields.fields
                    lot_title = fields.get("fields[summary][ru]") or fields.get("fields[summary][en]")
        except Exception as e:
            logger.warning(f"Не удалось получить название лота {lid}: {e}")
            bot.send_message(m.chat.id, f"⚠️ Не удалось получить название лота. Лот будет добавлен без названия.\n\nПродолжаю...")
        
        db.add_lot(lid, hrs, lot_title)
        if lot_title:
            bot.send_message(m.chat.id, f"✅ Лот добавлен!\n\n📦 ID: {lid}\n📝 Название: {lot_title}\n⏰ Часов: {hrs}")
        else:
            bot.send_message(m.chat.id, f"✅ Лот добавлен!\n\n📦 ID: {lid}\n⏰ Часов: {hrs}\n⚠️ Название не получено")
        send_main_menu(m.chat.id)
    except ValueError:
        bot.send_message(m.chat.id, "❌ Ошибка! ID и часы должны быть числами.")

def process_loyalty_rule(m):
    try:
        parts = m.text.strip().split()
        if len(parts) < 2:
            bot.send_message(m.chat.id, "❌ Неверный формат. Используйте: КУПИ ПОЛУЧИ")
            return
        db.set_setting("loyalty_rule_buy", parts[0])
        db.set_setting("loyalty_rule_get", parts[1])
        bot.send_message(m.chat.id, f"✅ Правило сохранено!\n\nКупи {parts[0]}+ ч. → получи +{parts[1]} ч.")
        send_main_menu(m.chat.id)
    except:
        bot.send_message(m.chat.id, "❌ Ошибка формата.")

def process_msg_update(m, key):
    if m.text.startswith("/cancel"):
        bot.send_message(m.chat.id, "❌ Отменено")
        send_main_menu(m.chat.id)
        return
    db.set_setting(key, m.text)
    bot.send_message(m.chat.id, "✅ Сообщение обновлено!")
    send_main_menu(m.chat.id)


# ═══════════════════════════════════════════════════════════════
#                    ФОНОВЫЙ ПРОЦЕСС
# ═══════════════════════════════════════════════════════════════

def worker_loop():
    while not bg_stop_event.is_set():
        try:
            orders = db.get_active_orders()
            now = time.time()
            
            for o in orders:
                oid, bid, cid, sid, hrs, st, et = o[0], o[1], o[2], o[3], o[4], o[5], o[6]
                notified_15 = o[10] if len(o) > 10 else 0
                
                time_left = et - now
                
                # Уведомление за 15 минут
                if 0 < time_left <= 900 and notified_15 == 0:
                    msg_15 = db.get_setting("msg_15_min")
                    if msg_15:
                        try:
                            cardinal_instance.account.send_message(cid, msg_15)
                            db.set_notified_15_min(oid)
                        except: pass

                # Заказ истек
                if now >= et:
                    logger.info(f"Order {oid} expired.")
                    db.end_order(oid)
                    
                    msg = db.get_setting("msg_expired")
                    try:
                        cardinal_instance.account.send_message(cid, msg)
                    except: pass
                    
                    # Уведомление админа
                    aid = db.get_setting("admin_id")
                    if aid != "0":
                        price = o[8]
                        bot.send_message(aid, f"🏁 Аренда завершена!\n\n📦 Заказ: {oid}\n💰 Прибыль: {price} ₽\n\n⚙️ Сервер выключен и ссылка обновлена.")
                    
                    # Выключаем сервер
                    accs = db.get_accounts()
                    if accs:
                        cookie = accs[0][1]
                        place_id = db.get_setting("game_id")
                        RobloxAPI.shutdown_server(cookie, sid, place_id)
                        RobloxAPI.regenerate_link(cookie, sid)

            # Автоотключение лотов
            if db.get_setting("auto_disable_lots") == "1":
                free = db.get_free_server()
                lots = db.get_lots()
                if not free:
                    for l in lots:
                        if l[2] == 1:
                            db.toggle_lot(l[0], False)
                            try:
                                lf = cardinal_instance.account.get_lot_fields(l[0])
                                lf.active = False
                                cardinal_instance.account.save_lot(lf)
                            except: pass
                else:
                    for l in lots:
                        if l[2] == 0:
                            db.toggle_lot(l[0], True)
                            try:
                                lf = cardinal_instance.account.get_lot_fields(l[0])
                                lf.active = True
                                cardinal_instance.account.save_lot(lf)
                            except: pass

        except Exception as e:
            logger.error(f"Worker loop error: {e}")
        
        for _ in range(60):
            if bg_stop_event.is_set():
                break
            time.sleep(1)


# ═══════════════════════════════════════════════════════════════
#                   ОБРАБОТЧИКИ FUNPAY
# ═══════════════════════════════════════════════════════════════

def on_new_order(cardinal, event: NewOrderEvent):
    order = event.order
    
    # Получаем количество товаров
    amount = 1
    try:
        if hasattr(order, 'amount') and order.amount:
            amount = int(order.amount)
        elif hasattr(order, 'parse_amount'):
            amount = order.parse_amount() or 1
    except:
        amount = 1
    
    logger.info(f"New order: {order.id}, amount={amount}")
    
    if db.get_order_by_id(order.id):
        logger.info(f"Order {order.id} already exists. Skipping.")
        return

    try:
        try:
            full_order = cardinal.account.get_order(order.id)
        except Exception:
            logger.warning(f"Cannot fetch full order {order.id}. Assuming default parameters.")
            full_order = None
        
        # Получаем название заказа (название лота, который купили)
        order_title = None
        if full_order:
            order_title = full_order.short_description or full_order.title
        if not order_title:
            order_title = order.description
        
        logger.info(f"Order {order.id} title: {order_title}")
        
        # Ищем лот по названию
        lot_info = None
        if order_title:
            lot_info = db.get_lot_by_title(order_title)
        
        if not lot_info:
            logger.info(f"Order {order.id}: No matching lot found by title '{order_title}'. Skipping.")
            return
        
        lot_id, hours_per_item, lot_active, lot_title = lot_info
        
        if not lot_active:
            logger.info(f"Order {order.id}: Lot {lot_id} is inactive. Skipping.")
            return
        
        logger.info(f"Order {order.id}: Matched lot {lot_id} '{lot_title}' -> {hours_per_item}h per item")
        
        # Умножаем часы на количество!
        hours = hours_per_item * amount
        logger.info(f"Order {order.id}: {hours_per_item}h x {amount} = {hours}h total")
        
        # Проверяем есть ли уже активный заказ у покупателя
        active_order = db.get_order_by_buyer(order.buyer_id)
        
        if active_order:
            # Продлеваем существующий заказ
            old_oid = active_order[0]
            current_hours = active_order[4]
            current_chat_id = active_order[2]
            
            total_added = hours
            bonus_added = 0
            
            if db.get_setting("loyalty_rule_enabled") == "1":
                buy = int(db.get_setting("loyalty_rule_buy"))
                get = int(db.get_setting("loyalty_rule_get"))
                if hours >= buy:
                    total_added += get
                    bonus_added = get

            db.update_order_time(old_oid, total_added)
            
            new_total_hours = current_hours + total_added
            
            # Сообщение о продлении
            if amount > 1:
                msg = f"⏰ Время продлено!\n\n➕ Добавлено: {hours_per_item}ч × {amount}шт = {hours} ч."
            else:
                msg = f"⏰ Время продлено!\n\n➕ К аренде добавлено: {hours} ч."
            if bonus_added > 0:
                msg += f"\n🎁 Бонус за покупку: +{bonus_added} ч."
            msg += f"\n\n⏱️ Общее время аренды: {new_total_hours} ч.\n\nИграй на здоровье! 🎮🔥"
            
            cardinal.account.send_message(current_chat_id, msg)
            
            aid = db.get_setting("admin_id")
            if aid != "0":
                qty_info = f" ({amount}шт)" if amount > 1 else ""
                bot.send_message(aid, f"🔄 Продление аренды!\n\n👤 Покупатель: {order.buyer_id}\n➕ Добавлено: +{total_added} ч.{qty_info}\n💰 Сумма: {order.price} ₽")
            return

        # Новый заказ
        server_id = db.get_free_server()
        if not server_id:
            try:
                cardinal.account.refund(order.id)
                cardinal.account.send_message(order.chat_id, "😕 К сожалению, все серверы заняты\n\n💸 Твои деньги уже возвращены на баланс.\n\n🕐 Попробуй оформить заказ чуть позже или напиши нам — мы поможем!")
            except Exception as e:
                logger.error(f"Failed to refund order {order.id}: {e}")
            return

        # Бонус за покупку
        bonus_added = 0
        if db.get_setting("loyalty_rule_enabled") == "1":
            buy = int(db.get_setting("loyalty_rule_buy"))
            get = int(db.get_setting("loyalty_rule_get"))
            if hours >= buy:
                hours += get
                bonus_added = get

        accs = db.get_accounts()
        if not accs:
            logger.error("No accounts")
            return
            
        cookie = accs[0][1]
        link = RobloxAPI.regenerate_link(cookie, server_id)
        
        if link:
            db.create_order(order.id, order.buyer_id, order.chat_id, server_id, hours, order.price)
            logger.info(f"Created order: id={order.id}, buyer_id={order.buyer_id}, chat_id={order.chat_id}, server={server_id}")
            
            msg = db.get_setting("msg_purchase").format(link=link, hours=hours)
            if bonus_added > 0:
                msg += f"\n\nБонус: +{bonus_added} ч. бесплатно!"
            
            cardinal.account.send_message(order.chat_id, msg)
            
            aid = db.get_setting("admin_id")
            if aid != "0":
                qty_info = f" ({hours_per_item}ч × {amount}шт)" if amount > 1 else ""
                bot.send_message(aid, f"💰 Новый заказ!\n\n📦 Заказ: {order.id}\n⏰ Время: {hours} ч.{qty_info}\n💵 Сумма: {order.price} ₽\n🖥️ Сервер: {server_id}")
    
    except Exception as e:
        logger.error(f"Order handler error: {e}")


def on_new_message(cardinal, event: NewMessageEvent):
    msg = event.message
    
    # Игнорируем собственные сообщения продавца
    if msg.author_id == msg.chat_id or msg.author == cardinal.account.username:
        return
    # Дополнительная проверка - если автор это мы сами
    try:
        if hasattr(msg, 'author_id') and hasattr(cardinal.account, 'id'):
            if str(msg.author_id) == str(cardinal.account.id):
                return
    except:
        pass
    
    txt = msg.text.strip()
    chat_id = msg.chat_id
    author_id = getattr(msg, 'author_id', None)
    
    # Логируем для диагностики
    logger.debug(f"Message from chat_id={chat_id}, author_id={author_id}, text={txt[:50]}")
    
    # Проверка на отзыв (системное сообщение от FunPay)
    if db.get_setting("loyalty_review_enabled") == "1":
        txt_lower = txt.lower()
        
        # Извлекаем номер заказа из текста (формат: "к заказу #XXXXXX")
        order = None
        order_match = re.search(r'#([A-Z0-9]+)', txt)
        if order_match:
            order_id = order_match.group(1)
            order = db.get_order_by_id(order_id)
        
        # Если не нашли по номеру - ищем по chat_id/author_id
        if not order:
            order = db.get_order_by_chat(chat_id, author_id)
        
        # Отзыв ДОБАВЛЕН
        if "оставил отзыв" in txt_lower or "написал отзыв" in txt_lower or "changed the review" in txt_lower or "изменил отзыв" in txt_lower:
            if order and order[9] == 0:  # review_bonus_given == 0
                bonus = int(db.get_setting("loyalty_review_hours"))
                db.update_order_time(order[0], bonus)
                db.set_review_bonus_given(order[0])
                
                order_chat = order[2]
                msg_bonus = db.get_setting("msg_review_bonus").format(hours=bonus)
                cardinal.account.send_message(order_chat, msg_bonus)
                logger.info(f"Review bonus +{bonus}h given for order {order[0]}")
        
        # Отзыв УДАЛЕН - забираем бонус обратно
        elif "удалил отзыв" in txt_lower or "deleted the review" in txt_lower:
            if order and order[9] == 1:  # review_bonus_given == 1
                bonus = int(db.get_setting("loyalty_review_hours"))
                db.update_order_time(order[0], -bonus)  # Отнимаем время
                db.set_review_bonus_given(order[0], given=False)  # Сбрасываем флаг
                
                order_chat = order[2]
                cardinal.account.send_message(order_chat, f"🗑️ Отзыв удалён\n\n⏰ Бонусное время ({bonus} ч.) было снято с твоей аренды.\n\nЕсли это ошибка — напиши нам!")
                logger.info(f"Review bonus -{bonus}h removed for order {order[0]}")
    
    # ══════════════════════════════════════
    #    УМНАЯ ОБРАБОТКА КОМАНД
    # ══════════════════════════════════════
    
    # Команда: ССЫЛКА
    if match_command(txt, COMMAND_LINK):
        order = db.get_order_by_chat(chat_id, author_id)
        logger.debug(f"LINK command: chat_id={chat_id}, order_found={order is not None}")
        if order:
            accs = db.get_accounts()
            if accs:
                link = RobloxAPI.regenerate_link(accs[0][1], order[3])
                if link:
                    cardinal.account.send_message(chat_id, f"🔗 Ваша новая ссылка:\n\n{link}\n\n✅ Готово к использованию!")
                else:
                    cardinal.account.send_message(chat_id, "❌ Не удалось обновить ссылку.\n\nПопробуйте ещё раз или напишите \"помощь\".")
        else:
            cardinal.account.send_message(chat_id, "❌ У вас нет активной аренды.\n\nОформите заказ, чтобы получить VIP сервер!")
        return

    # Команда: ПОМОЩЬ
    if match_command(txt, COMMAND_HELP):
        aid = db.get_setting("admin_id")
        if aid != "0":
            order = db.get_order_by_chat(chat_id, author_id)
            order_info = ""
            if order:
                time_left = int(order[6] - time.time())
                order_info = f"\nЗаказ: {order[0]}\nОсталось: {format_time_left(time_left)}"
            
            bot.send_message(aid, f"🆘 Запрос помощи!\n\n💬 Чат: {chat_id}\n👤 Покупатель просит помощи!{order_info}")
        cardinal.account.send_message(chat_id, "📨 Твой запрос отправлен!\n\n👨‍💼 Продавец получил уведомление и скоро с тобой свяжется.\n\n⏳ Пожалуйста, подожди немного!")
        return

    # Команда: СЕРВЕРА / СТАТУС
    if match_command(txt, COMMAND_SERVERS):
        srvs = db.get_servers()
        free = len([s for s in srvs if s[1] == 'free'])
        total = len(srvs)
        occupied = total - free
        
        if free > 0:
            status_msg = f"Есть свободные сервера!"
        else:
            status_msg = "Все сервера заняты"
        
        cardinal.account.send_message(chat_id, f"📈 Статистика серверов:\n\n🟢 Свободных: {free}\n🔴 Занятых: {occupied}\n📊 Всего серверов: {total}\n\n{status_msg}")
        return

    # Команда: ВРЕМЯ
    if match_command(txt, COMMAND_TIME):
        order = db.get_order_by_chat(chat_id, author_id)
        if order:
            time_left = int(order[6] - time.time())
            time_str = format_time_left(time_left)
            
            if time_left > 3600:
                status = "Всё отлично! Времени достаточно."
            elif time_left > 900:
                status = "Скоро закончится. Не забудьте продлить!"
            else:
                status = "Осталось мало времени! Срочно продлевайте!"
            
            cardinal.account.send_message(chat_id, f"⏱️ Твоя аренда:\n\n⏳ Осталось времени: {time_str}\n\n{status}")
        else:
            cardinal.account.send_message(chat_id, "📭 Нет активной аренды\n\nОформи заказ, чтобы получить VIP сервер и начать играть!")
        return

    # Команда: ИНФО
    if match_command(txt, COMMAND_INFO):
        order = db.get_order_by_chat(chat_id, author_id)
        if order:
            time_left = int(order[6] - time.time())
            time_str = format_time_left(time_left)
            
            cardinal.account.send_message(chat_id, f"📄 Информация о заказе:\n\n🆔 ID заказа: {order[0]}\n⏰ Время аренды: {order[4]} ч.\n⏳ Осталось: {time_str}\n\n💬 Доступные команды:\n• \"ссылка\" — получить ссылку\n• \"время\" — узнать остаток\n• \"помощь\" — связаться с нами")
        else:
            cardinal.account.send_message(chat_id, "📭 Нет активной аренды\n\nОформи заказ, чтобы получить VIP сервер и начать играть!")
        return


# ═══════════════════════════════════════════════════════════════
#                   ЖИЗНЕННЫЙ ЦИКЛ ПЛАГИНА
# ═══════════════════════════════════════════════════════════════

def init_plugin(cardinal):
    global bot, cardinal_instance, bg_worker_running
    
    logger.info("AutoRentVipRoblox: Init started")
    if not cardinal.telegram:
        logger.error("AutoRentVipRoblox: Telegram not initialized!")
        return

    cardinal_instance = cardinal
    bot = cardinal.telegram.bot
    
    try:
        cardinal.add_telegram_commands(UUID, [
            ("autovip", "AutoRentVipRoblox Panel", True)
        ])
    except Exception as e:
        logger.error(f"AutoRentVipRoblox: Failed to register command: {e}")

    @bot.message_handler(commands=["autovip"])
    def open_panel(m):
        logger.info(f"AutoRentVipRoblox: /autovip command from {m.from_user.id}")
        try:
            user_id = m.from_user.id
            admin_id_cfg = None
            
            try:
                if hasattr(cardinal, "MAIN_CFG") and "Telegram" in cardinal.MAIN_CFG:
                    admin_id_cfg = cardinal.MAIN_CFG["Telegram"].get("admin_id")
            except: pass
            
            admin_id_db = db.get_setting("admin_id")
            
            is_admin = False
            if admin_id_cfg and str(user_id) == str(admin_id_cfg):
                is_admin = True
            elif admin_id_db and str(user_id) == str(admin_id_db):
                is_admin = True
            elif str(admin_id_db) == "0":
                is_admin = True
            
            if is_admin:
                db.set_setting("admin_id", user_id)
                send_main_menu(m.chat.id)
            else:
                bot.send_message(m.chat.id, "⛔ Доступ запрещен. Вы не администратор.")
                logger.warning(f"Unauthorized access attempt by {user_id}")
                 
        except Exception as e:
            logger.error(f"AutoRentVipRoblox: Error in open_panel: {e}")
            bot.send_message(m.chat.id, f"❌ Ошибка: {e}")

    @bot.callback_query_handler(func=lambda c: c.data.startswith("ar_"))
    def panel_cb(c):
        try:
            handle_admin_callback(c)
        except Exception as e:
            logger.error(f"AutoRentVipRoblox: Callback error: {e}")

    if not bg_worker_running:
        bg_stop_event.clear()
        t = threading.Thread(target=worker_loop, daemon=True)
        t.start()
        bg_worker_running = True

def stop_plugin(cardinal, *args):
    global bg_worker_running
    bg_stop_event.set()
    bg_worker_running = False


# ═══════════════════════════════════════════════════════════════
#                      МЕТАДАННЫЕ ПЛАГИНА
# ═══════════════════════════════════════════════════════════════

NAME = "VIP Аренда Roblox"
VERSION = "2.1.0"
DESCRIPTION = "Auto-аренда VIP-серверов Roblox"
CREDITS = ""
UUID = "109ac297-e530-4da0-a00e-6783c6770fbd"
SETTINGS_PAGE = False

BIND_TO_PRE_INIT = [init_plugin]
BIND_TO_NEW_ORDER = [on_new_order]
BIND_TO_NEW_MESSAGE = [on_new_message]
BIND_TO_DELETE = stop_plugin
