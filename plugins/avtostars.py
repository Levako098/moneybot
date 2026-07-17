# Deobfuscated from 'autostars (3).py' (base64+zlib payload). 
# Лицензия ПОЛНОСТЬЮ ОТКЛЮЧЕНА

from __future__ import annotations

import asyncio
import threading
import logging
from logging import Filter
import requests
import json
import os
import base64
import re
import FunPayAPI.types
import datetime
import time
import random
import subprocess
import sys
import atexit
import hashlib
import hmac
import uuid
from typing import TYPE_CHECKING, Dict, List, Tuple, Optional, Any

NAME = "Auto Stars"
VERSION = "2.0"
DESCRIPTION = "Плагин для авто-продажи Stars через Fragment.\n\n👨‍💻 Разработчик: @veemp\n🛒 Магазин: @veemp_shop"
CREDITS = "@veemp | https://t.me/FunPay_plugin"
UUID = "6f1c9c20-8b3d-4a7f-bc3e-2d9f8c1e5a77"
SETTINGS_PAGE = False

# ========= СИСТЕМА ЛИЦЕНЗИЙ (ПОЛНОСТЬЮ ОТКЛЮЧЕНА) =========
class LicenseManager:
    """Заглушка для системы лицензий. Всегда возвращает, что лицензия активна."""
    def __init__(self):
        self.license_valid = True
        self.license_checked = True
        self.last_check = time.time()
        self.retry_count = 0
        self.license_data = {
            "activated_until": "Навсегда",
            "activated_at": "Активировано",
            "status": "active"
        }
        self.check_lock = threading.Lock()
        
    def check_license(self, tg_id: str) -> Tuple[bool, str]:
        """Заглушка проверки лицензии. Всегда возвращает успех."""
        return True, "Лицензия активна (режим без проверки)"
    
    def is_valid(self) -> bool:
        """Всегда возвращает True"""
        return True
    
    def should_check_license(self) -> bool:
        """Никогда не требует проверки"""
        return False
    
    def get_license_data(self) -> dict:
        """Возвращает тестовые данные лицензии"""
        return self.license_data.copy()

license_manager = LicenseManager()

def get_tg_id_from_cache() -> str:
    """Получает TG ID из файла авторизованных пользователей."""
    cache_file = "storage/cache/tg_authorized_users.json"
    try:
        if os.path.exists(cache_file):
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data and isinstance(data, dict):
                    tg_ids = list(data.keys())
                    if tg_ids:
                        return tg_ids[0]
        return "123456789"  # Тестовый ID если файла нет
    except Exception as e:
        print(f"Ошибка при чтении файла авторизованных пользователей: {e}")
        return "123456789"

def license_required(func):
    """Декоратор-заглушка. Просто вызывает функцию без проверки лицензии."""
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    return wrapper

def critical_license_check(c = None) -> bool:
    """Критическая проверка лицензии. Всегда возвращает True."""
    return True

# ========= ИМПОРТЫ ЗАВИСИМОСТЕЙ =========
try:
    from tonutils.client import TonapiClient
    from tonutils.wallet import WalletV4R2
except ImportError:
    print("Установка модуля tonutils...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "tonutils"])
    from tonutils.client import TonapiClient
    from tonutils.wallet import WalletV4R2

try:
    import httpx
except ImportError:
    print("Установка модуля httpx...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "httpx"])
    import httpx

try:
    from FunPayAPI.updater.events import NewOrderEvent, NewMessageEvent
    from FunPayAPI import Account, enums
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
    import telebot
    import FunPayAPI
except ImportError as e:
    print(f"Ошибка импорта: {e}")
    print("Установка недостающих модулей...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pyTelegramBotAPI"])
    from FunPayAPI.updater.events import NewOrderEvent, NewMessageEvent
    from FunPayAPI import Account, enums
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
    import telebot
    import FunPayAPI

if TYPE_CHECKING:
    from cardinal import Cardinal

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

try:
    from telegram import types
except ImportError:
    pass

# ========= КОНФИГУРАЦИЯ =========
CONFIG_FILE = "storage/stars/cfg.json"
STATS_FILE = "storage/stars/stats.json"
LOG_FILE = "storage/stars/auto.log"

default_config = {
    "API_KEY": "YOUR-API-KEY",
    "IS_TESTNET": False,
    "MNEMONIC": [
        "word1", "word2", "word3", "word4", "word5", "word6", 
        "word7", "word8", "word9", "word10", "word11", "word12",
        "word13", "word14", "word15", "word16", "word17", "word18",
        "word19", "word20", "word21", "word22", "word23", "word24"
    ],
    "ADDRESS": "YOUR-CRYPTO-ADDRESS-WALLET",
    "DESTINATION_ADDRESS": "UQCFJEP4WZ_mpdo0_kMEmsTgvrMHG7K_tWY16pQhKHwoOtFz",
    "MIN_STARS": 50,
    "MAX_STARS": 100000,
    "fragment_api": {
        "hash": "YOUR-HASH",
        "cookie": "YOUR-COOKIE",
        "url": "https://fragment.com/api",
        "subcategory_id": 2418,
    },
    "AUTO_REFUND": False,
    "SEND_SUCCESS_NOTIFICATION": True,
    "TON_RATE_API": "https://api.coingecko.com/api/v3/simple/price?ids=the-open-network&vs_currencies=rub",
    "SHOW_SENDER": "0",
    "completed_order_message": "Ваш заказ выполнен!"
}

orders_info: Dict[int, List[Dict]] = {}
RUNNING = True
RUNNING_GET_LOTS = False
user_editing_state = {}

logger = logging.getLogger("FPC.autostars")
logger.setLevel(logging.DEBUG)

class PluginFilter(logging.Filter):
    def filter(self, record):
        return record.name == "FPC.autostars"

os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
os.makedirs(os.path.dirname(STATS_FILE), exist_ok=True)
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
file_handler.setLevel(logging.DEBUG)
file_handler.addFilter(PluginFilter())
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.DEBUG)
stream_handler.setFormatter(formatter)
logger.addHandler(stream_handler)

def safe_edit_message_text(bot, chat_id, message_id, text, reply_markup=None, parse_mode=None):
    try:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )
        return True
    except Exception as e:
        if "message is not modified" in str(e):
            return True
        logger.error(f"Ошибка при редактировании сообщения: {e}")
        return False

def cancel_editing(chat_id: int):
    if chat_id in user_editing_state:
        del user_editing_state[chat_id]

def mask_username(username: str) -> str:
    if not username:
        return ""
    if len(username) <= 3:
        return username[0] + "*" * (len(username) - 1)
    
    masked = username[0]
    for i in range(1, len(username)):
        if i % 2 == 1: 
            masked += "*"
        else:
            masked += username[i]
    return masked

def sanitize_telegram_text(text: str) -> str:
    text = text.replace("<", "&lt;").replace(">", "&gt;")
    text = text.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
    return text

def remove_at_symbol(username: str) -> str:
    if username.startswith("@"):
        return username[1:]
    return username

def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(default_config, f, indent=4, ensure_ascii=False)
        return default_config.copy()
    
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)
    
    if "ALLOWED_QUANTITIES" in config:
        config["MIN_STARS"] = min(config["ALLOWED_QUANTITIES"])
        config["MAX_STARS"] = max(config["ALLOWED_QUANTITIES"])
        del config["ALLOWED_QUANTITIES"]
    
    for key in default_config:
        if key not in config:
            config[key] = default_config[key]
    
    if "fragment_api" not in config:
        config["fragment_api"] = default_config["fragment_api"]
    else:
        for frag_key in default_config["fragment_api"]:
            if frag_key not in config["fragment_api"]:
                config["fragment_api"][frag_key] = default_config["fragment_api"][frag_key]
    
    if isinstance(config["SHOW_SENDER"], bool):
        config["SHOW_SENDER"] = "1" if config["SHOW_SENDER"] else "0"
    
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)
    
    return config

config = load_config()
API_KEY = config["API_KEY"]
IS_TESTNET = config["IS_TESTNET"]
MNEMONIC = config["MNEMONIC"]
ADDRESS = config["ADDRESS"]
DESTINATION_ADDRESS = config["DESTINATION_ADDRESS"]
MIN_STARS = config["MIN_STARS"]
MAX_STARS = config["MAX_STARS"]
COMPLETED_ORDER_MESSAGE = config["completed_order_message"]
TON_RATE_API = config["TON_RATE_API"]
SHOW_SENDER = config["SHOW_SENDER"]

FRAGMENT_HASH = config["fragment_api"]["hash"]
FRAGMENT_COOKIE = config["fragment_api"]["cookie"]
FRAGMENT_URL = config["fragment_api"]["url"]
SUBCATEGORY_ID = config["fragment_api"].get("subcategory_id", 2418)

url = f"{FRAGMENT_URL}?hash={FRAGMENT_HASH}"
headers = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "ru",
    "Connection": "keep-alive",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Cookie": FRAGMENT_COOKIE,
    "Host": "fragment.com",
    "Origin": "https://fragment.com",
    "Referer": "https://fragment.com/stars/buy",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
}

def load_stats() -> dict:
    if not os.path.exists(STATS_FILE):
        with open(STATS_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f, ensure_ascii=False)
        return {}
    
    with open(STATS_FILE, "r", encoding="utf-8") as f:
        stats = json.load(f)
    
    for date_data in stats.values():
        if "total_profit" not in date_data:
            date_data["total_profit"] = 0.0
        if "total_revenue" not in date_data:
            date_data["total_revenue"] = 0.0
        if "total_cost" not in date_data:
            date_data["total_cost"] = 0.0
        for tx in date_data.get("transactions", []):
            if "profit" not in tx:
                tx["profit"] = 0.0
            if "revenue" not in tx:
                tx["revenue"] = 0.0
            if "cost" not in tx:
                tx["cost"] = 0.0
    
    return stats

def save_stats(stats_data: dict):
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats_data, f, indent=4, ensure_ascii=False)

stats_data = load_stats()

def update_stats(success: bool, quantity: int, amount_rub: float = None, 
                spent_ton: float = None, ton_rate: float = None, profit: float = None):
    date_str = datetime.datetime.now().strftime("%d.%m.%Y")
    
    if date_str not in stats_data:
        stats_data[date_str] = {
            "successful_transactions": 0,
            "unsuccessful_transactions": 0,
            "quantities_sold": {},
            "transactions": [],
            "total_profit": 0.0,
            "total_revenue": 0.0,
            "total_cost": 0.0
        }
    
    if success:
        stats_data[date_str]["successful_transactions"] += 1
    else:
        stats_data[date_str]["unsuccessful_transactions"] += 1
    
    q_str = str(quantity)
    if q_str not in stats_data[date_str]["quantities_sold"]:
        stats_data[date_str]["quantities_sold"][q_str] = 0
    stats_data[date_str]["quantities_sold"][q_str] += 1
    
    now_time = datetime.datetime.now().strftime("%H:%M:%S")
    transaction_data = {
        "time": now_time,
        "quantity": quantity,
        "status": "success" if success else "fail",
        "profit": profit if profit is not None else 0.0
    }
    
    if success and amount_rub is not None and spent_ton is not None and ton_rate is not None and profit is not None:
        cost_rub = spent_ton * ton_rate
        transaction_data.update({
            "amount_rub": amount_rub,
            "spent_ton": spent_ton,
            "ton_rate": ton_rate,
            "revenue": amount_rub,
            "cost": cost_rub,
            "profit": profit
        })
        stats_data[date_str]["total_profit"] += profit
        stats_data[date_str]["total_revenue"] += amount_rub
        stats_data[date_str]["total_cost"] += cost_rub
    
    stats_data[date_str]["transactions"].append(transaction_data)
    save_stats(stats_data)

def get_ton_rate() -> float:
    try:
        response = requests.get(TON_RATE_API, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data["the-open-network"]["rub"]
    except Exception as e:
        logger.error(f"Ошибка при получении курса TON: {e}")
        return None

def get_account_info(address: str, api_token: str) -> dict:
    url = f"https://tonapi.io/v2/accounts/{address}"
    headers = {"Authorization": f"Bearer {api_token}"}
    response = requests.get(url, headers=headers, timeout=10)
    response.raise_for_status()
    return response.json()

async def check_wallet_balance() -> float:
    try:
        client = TonapiClient(api_key=API_KEY, is_testnet=IS_TESTNET)
        wallet, public_key, private_key, mnemonic = WalletV4R2.from_mnemonic(
            client, MNEMONIC
        )
        account_info = get_account_info(ADDRESS, API_KEY)
        raw_balance = int(account_info.get("balance", 0))
        balance_ton = raw_balance / 1e9
        return balance_ton
    except Exception as e:
        logger.error(f"Ошибка при проверке баланса кошелька: {e}")
        return 0.0

async def send_ton_transaction(amount: float, comment: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    client = TonapiClient(api_key=API_KEY, is_testnet=IS_TESTNET)
    wallet, public_key, private_key, mnemonic = WalletV4R2.from_mnemonic(
        client, MNEMONIC
    )
    
    balance_ton = await check_wallet_balance()
    if balance_ton < amount:
        error_msg = f"Недостаточно средств на кошельке. Требуется: {amount} TON, доступно: {balance_ton} TON."
        logger.warning(error_msg)
        return None, None, error_msg

    try:
        tx_hash = await wallet.transfer(
            destination=DESTINATION_ADDRESS,
            amount=amount,
            body=comment,
        )
        logger.info(f"Успешно переведено {amount} TON! TX Hash: {tx_hash}")
        await asyncio.sleep(random.randint(2, 10))
        logger.debug(f"Ссылка Tonviewer: https://tonviewer.com/transaction/{tx_hash}")
        
        ref_id = comment.split("Ref#")[-1].strip()
        return tx_hash, ref_id, None
    except Exception as e:
        error_msg = f"Ошибка при отправке транзакции: {e}"
        logger.error(error_msg)
        return None, None, error_msg

def decoder(data: str) -> bytes:
    while len(data) % 4 != 0:
        data += "="
    return base64.b64decode(data)

def decoder2(data: bytes) -> str:
    decoded_data = data.decode("latin1")
    ref_id = decoded_data.split("Ref#")[-1]
    return ref_id

async def main_async(username: str, quantity: int) -> Tuple[Optional[str], Optional[str], int, float, Optional[str]]:
    if quantity < MIN_STARS or quantity > MAX_STARS:
        error_msg = f"Недопустимое количество Stars: {quantity}. Допустимо от {MIN_STARS} до {MAX_STARS}."
        logger.error(error_msg)
        return None, None, quantity, 0, error_msg
    
    clean_username = remove_at_symbol(username)
    logger.debug(f"Очистенный username: {clean_username}")
    
    payload_search = {
        "query": clean_username,
        "quantity": quantity,
        "method": "searchStarsRecipient",
    }
    
    try:
        response_search = requests.post(url, headers=headers, data=payload_search, timeout=30)
        response_search.raise_for_status()
        text_search = response_search.json()
        
        if 'found' in text_search and 'recipient' in text_search['found']:
            recipient = text_search['found']['recipient']
            if not recipient:
                error_msg = f"Recipient не найден в ответе: {text_search}"
                logger.error(error_msg)
                return None, None, quantity, 0, error_msg
        else:
            error_detail = text_search.get("error", "Неизвестная ошибка при поиске recipient.")
            error_msg = f"Ошибка при поиске recipient: {error_detail}"
            logger.error(error_msg)
            return None, None, quantity, 0, error_msg
    except Exception as e:
        error_msg = f"Ошибка при запросе поиска recipient: {e}"
        logger.error(error_msg)
        return None, None, quantity, 0, error_msg
    
    payload_init = {
        "recipient": recipient,
        "quantity": quantity,
        "method": "initBuyStarsRequest",
    }
    
    try:
        response_init = requests.post(url, headers=headers, data=payload_init, timeout=30)
        response_init.raise_for_status()
        text_init = response_init.json()
        
        if 'req_id' in text_init and 'amount' in text_init:
            req_id = text_init['req_id']
            try:
                AMOUNT = float(text_init['amount'])
                logger.debug(f"Требуемая сумма: {AMOUNT} TON")
            except (TypeError, ValueError):
                AMOUNT = 0
                logger.error("Не удалось конвертировать 'amount' в float.")
        else:
            error_msg = f"Не удалось получить req_id или amount: {text_init}"
            logger.error(error_msg)
            return None, None, quantity, 0, error_msg
    except Exception as e:
        error_msg = f"Ошибка при инициализации покупки Stars: {e}"
        logger.error(error_msg)
        return None, None, quantity, 0, error_msg
    
    payload_link = {
        "account": '{"address":"0:adc5b49f73e4796ecc3c290ad0d89f87fa552b515d173d5295469df9612c24a","chain":"-239","walletStateInit":"te6ccgECFgEAAwQAAgE0AQIBFP8A9KQT9LzyyAsDAFEAAAAAKamjF5hE%2BFriD8Ufe710n9USsAZBzBxLOlXNYCYDiPBRvJZXQAIBIAQFAgFIBgcE%2BPKDCNcYINMf0x%2FT%2F%2FQE0VFDuvKhUVG68qIF%2BQFUEGT5EPKj%2BAAkpMjLH1JAyx9SMMv%2FUhD0AMntVPgPAdMHIcAAn2xRkyDXSpbTB9QC%2BwDoMOAhwAHjACHAAuMAAcADkTDjDQOkyMsfEssfy%2F8SExQVAubQAdDTAyFxsJJfBOAi10nBIJJfBOAC0x8hghBwbHVnvSKCEGRzdHK9sJJfBeAD%2BkAwIPpEAcjKB8v%2FydDtRNCBAUDXIfQEMFyBAQj0Cm%2BhMbOSXwfgBdM%2FyCWCEHBsdWe6kjgw4w0DghBkc3RyupJfBuMNCAkCASAKCwB4AfoA9AQw%2BCdvIjBQCqEhvvLgUIIQcGx1Z4MesXCAGFAEywUmzxZY%2BgIZ9ADLaRfLH1Jgyz8gyYBA%2BwAGAIpQBIEBCPRZMO1E0IEBQNcgyAHPFvQAye1UAXKwjiOCEGRzdHKDHrFwgBhQBcsFUAPPFiP6AhPLassfyz%2FJgED7AJJfA%2BICASAMDQBZvSQrb2omhAgKBrkPoCGEcNQICEekk30pkQzmkD6f%2BYN4EoAbeBAUiYcVnzGEAgFYDg8AEbjJftRNDXCx%2BAA9sp37UTQgQFA1yH0BDACyMoHy%2F%2FJ0AGBAQj0Cm%2BhMYAIBIBARABmtznaiaEAga5Drhf%2FAABmvHfaiaEAQa5DrhY%2FAAG7SB%2FoA1NQi%2BQAFyMoHFcv%2FydB3dIAYyMsFywIizxZQBfoCFMtrEszMyXP7AMhAFIEBCPRR8qcCAHCBAQjXGPoA0z%2FIVCBHgQEI9FHyp4IQbm90ZXB0gBjIywXLAlAGzxZQBPoCE8tqEssfyz%2FJc%2FsAAAr0AMntVA%3D%3D"}',
        "device": '{"platform":"android","appName":"Tonkeeper","appVersion":"5.0.18","maxProtocolVersion":2,"features":["SendTransaction",{"name":"SendTransaction","maxMessages":4}]}',
        "transaction": "1",
        "id": req_id,
        "show_sender": SHOW_SENDER,
        "method": "getBuyStarsLink",
    }
    
    try:
        response_link = requests.post(url, headers=headers, data=payload_link, timeout=30)
        response_link.raise_for_status()
        text_link = response_link.json()
        
        if 'transaction' in text_link and 'messages' in text_link['transaction']:
            transaction_messages = text_link['transaction']['messages']
            
            if not transaction_messages:
                error_msg = f"Сообщения транзакции не найдены: {text_link}"
                logger.error(error_msg)
                return None, None, quantity, 0, error_msg
            
            payload_transaction = transaction_messages[0].get("payload")
            
            if not payload_transaction:
                error_msg = f"Payload сообщения транзакции не найден: {text_link}"
                logger.error(error_msg)
                return None, None, quantity, 0, error_msg
            
            try:
                decoded_payload = decoder(payload_transaction)
                ref_id = decoder2(data=decoded_payload)
                COMMENT = f"{quantity} Telegram Stars \n\nRef#{ref_id}"
                logger.debug(f"Комментарий для транзакции: {COMMENT}")
            except Exception as e:
                error_msg = f"Ошибка при обработке payload транзакции: {e}"
                logger.error(error_msg)
                return None, None, quantity, 0, error_msg
        else:
            error_detail = text_link.get("error", "Неизвестная ошибка при получении ссылки на покупку Stars.")
            error_msg = f"Ошибка при получении ссылки на покупку Stars: {error_detail}"
            logger.error(error_msg)
            return None, None, quantity, 0, error_msg
    except Exception as e:
        error_msg = f"Ошибка при получении ссылки на покупку Stars: {e}"
        logger.error(error_msg)
        return None, None, quantity, 0, error_msg
    
    try:
        tx_hash, ref_id, error_transaction = await send_ton_transaction(AMOUNT, COMMENT)
        if error_transaction:
            return None, None, quantity, AMOUNT, error_transaction
        
        if not tx_hash or not ref_id:
            error_msg = "Не удалось получить данные транзакции после отправки."
            logger.error(error_msg)
            return None, None, quantity, AMOUNT, error_msg
        
        return tx_hash, ref_id, quantity, AMOUNT, None
    except Exception as e:
        error_msg = f"Исключение при отправке транзакции: {e}"
        logger.error(error_msg)
        return None, None, quantity, AMOUNT, error_msg

class PaymentProcessor:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.run_loop, daemon=True)
        self.thread.start()
        logger.debug("Поток PaymentProcessor запущен.")
        self.task_queue = asyncio.Queue()
        asyncio.run_coroutine_threadsafe(self.queue_worker(), self.loop)

    def run_loop(self):
        asyncio.set_event_loop(self.loop)
        logger.debug("Асинхронный цикл PaymentProcessor запущен.")
        self.loop.run_forever()

    def enqueue_payment(self, c: Cardinal, buyer_chat_id: int, username: str, 
                       stars_quantity: int, orderID: str, amount_rub: float) -> int:
        task = (c, buyer_chat_id, username, stars_quantity, orderID, amount_rub)
        position_in_queue = self.task_queue.qsize() + 1
        asyncio.run_coroutine_threadsafe(self.task_queue.put(task), self.loop)
        return position_in_queue

    async def queue_worker(self):
        while True:
            task = await self.task_queue.get()
            try:
                c, buyer_chat_id, username, stars_quantity, orderID, amount_rub = task
                await self.process_payment(
                    c, buyer_chat_id, username, stars_quantity, orderID, amount_rub
                )
            except Exception as e:
                logger.error(f"[queue_worker] Ошибка при обработке задачи: {e}")
            finally:
                self.task_queue.task_done()

    async def process_payment(self, c: Cardinal, buyer_chat_id: int, username: str, 
                             stars_quantity: int, orderID: str, amount_rub: float):
        try:
            if stars_quantity < MIN_STARS or stars_quantity > MAX_STARS:
                error_msg = f"Недопустимое количество Stars: {stars_quantity}. Допустимо от {MIN_STARS} до {MAX_STARS}."
                logger.error(error_msg)
                c.send_message(
                    buyer_chat_id,
                    sanitize_telegram_text(error_msg),
                )
                return
            
            tx_hash, ref_id, quantity, spent_ton, error = await main_async(username, stars_quantity)
            
            if error:
                await self.handle_payment_error(c, buyer_chat_id, username, stars_quantity, 
                                              orderID, amount_rub, error)
                return
            
            found_success = await self.check_transaction_status(tx_hash)
            
            if not found_success:
                error_msg = "Не удалось получить подтверждение транзакции после 30 попыток."
                await self.handle_payment_error(c, buyer_chat_id, username, stars_quantity,
                                              orderID, amount_rub, error_msg)
                return
            
            await self.handle_successful_payment(c, buyer_chat_id, username, quantity, 
                                               tx_hash, ref_id, spent_ton, amount_rub, orderID)
            
        except Exception as e:
            logger.error(f"Ошибка при обработке платежа для {username}: {e}")
            try:
                c.send_message(
                    buyer_chat_id,
                    sanitize_telegram_text(f"❌ Ваш заказ не выполнен. Причина: {str(e)}."),
                )
            except Exception as send_error:
                logger.error(f"Не удалось отправить сообщение об ошибке пользователю {buyer_chat_id}: {send_error}")

    async def check_transaction_status(self, tx_hash: str) -> bool:
        for attempt in range(30):
            try:
                url = f"https://tonapi.io/v2/blockchain/transactions/{tx_hash}"
                headers = {"Authorization": f"Bearer {API_KEY}"}
                response = requests.get(url, headers=headers, timeout=10)
                if response.status_code == 200:
                    logger.info(f"Транзакция {tx_hash} найдена и успешна.")
                    return True
                elif response.status_code == 404:
                    logger.warning(f"Попытка {attempt+1}: транзакция не найдена (404).")
                else:
                    logger.warning(f"Попытка {attempt+1}: статус {response.status_code}")
                await asyncio.sleep(7)
            except Exception as e:
                logger.error(f"Ошибка при проверке транзакции (попытка {attempt+1}): {e}")
                await asyncio.sleep(7)
        return False

    async def handle_payment_error(self, c: Cardinal, buyer_chat_id: int, username: str,
                                 stars_quantity: int, orderID: str, amount_rub: float, error: str):
        update_stats(False, stars_quantity)
        
        if "Не удалось декодировать JSON" in error:
            logger.error(f"Платёж не удался для {username}: {error}")
            user_orders = orders_info.get(buyer_chat_id, [])
            for o in user_orders:
                if o["orderID"] == orderID and not o.get("completed", False):
                    o["username"] = None
                    o["confirmed"] = False
                    break
            c.send_message(
                buyer_chat_id,
                sanitize_telegram_text(
                    "❌ Произошла ошибка при обработке вашего заказа. "
                    "Пожалуйста, введите ваш @username снова."
                ),
            )
            return
        
        if "Недостаточно средств на кошельке" in error:
            await self.handle_insufficient_funds(c, buyer_chat_id, username, orderID, error)
        elif "No Telegram users found" in error:
            await self.handle_user_not_found(c, buyer_chat_id, username, orderID, error)
        else:
            await self.handle_generic_error(c, buyer_chat_id, username, orderID, error)

    async def handle_insufficient_funds(self, c: Cardinal, buyer_chat_id: int, 
                                      username: str, orderID: str, error: str):
        tg_id = get_tg_id_from_cache()
        if config["AUTO_REFUND"]:
            c.send_message(
                buyer_chat_id,
                sanitize_telegram_text(
                    "❌ На кошельке владельца недостаточно средств для выполнения транзакции, "
                    "возвращаю Вам деньги и приношу извинения."
                ),
            )
            c.account.refund(orderID)
            if tg_id:
                c.telegram.bot.send_message(
                    tg_id,
                    sanitize_telegram_text(f"Вернул пользователю: {username} деньги по причине: {error}"),
                )
            await self.deactivate_lots(c)
        else:
            c.send_message(
                buyer_chat_id,
                sanitize_telegram_text("❌ Увы все звездочки раскупили. Свяжитесь с продавцом для возврата средств."),
            )
            self.send_error_with_inline_url(c, tg_id, orderID, error)
            await self.deactivate_lots(c)

    async def handle_user_not_found(self, c: Cardinal, buyer_chat_id: int, 
                                  username: str, orderID: str, error: str):
        c.send_message(
            buyer_chat_id,
            sanitize_telegram_text("❌ Ваш заказ не выполнен. Попробуйте ещё раз"),
        )
        tg_id = get_tg_id_from_cache()
        if config["AUTO_REFUND"]:
            c.account.refund(orderID)
            if tg_id:
                c.telegram.bot.send_message(
                    tg_id,
                    sanitize_telegram_text(f"Вернул пользователю: {username} деньги по причине: {error}"),
                )

    async def handle_generic_error(self, c: Cardinal, buyer_chat_id: int, 
                                 username: str, orderID: str, error: str):
        c.send_message(
            buyer_chat_id,
            sanitize_telegram_text("❌ Ваш заказ не выполнен. Попробуйте ещё раз"),
        )
        tg_id = get_tg_id_from_cache()
        if config["AUTO_REFUND"]:
            c.account.refund(orderID)
            if tg_id:
                c.telegram.bot.send_message(
                    tg_id,
                    sanitize_telegram_text(f"Вернул пользователю: {username} деньги по причине: {error}"),
                )
        else:
            self.send_error_with_inline_url(c, tg_id, orderID, error)

    async def handle_successful_payment(self, c: Cardinal, buyer_chat_id: int, username: str,
                                      quantity: int, tx_hash: str, ref_id: str, spent_ton: float,
                                      amount_rub: float, orderID: str):
        logger.info(f"Платёж успешен для {username}: TX Hash: {tx_hash}, Ref ID: {ref_id}, Qty: {quantity}")
        
        ton_rate = None
        for _ in range(3):
            ton_rate = get_ton_rate()
            if ton_rate is not None:
                break
            await asyncio.sleep(1)

        profit = 0.0
        if ton_rate is not None:
            cost_rub = spent_ton * ton_rate
            profit = amount_rub - cost_rub
        
        update_stats(True, quantity, amount_rub, spent_ton, ton_rate, profit)
        
        c.send_message(
            buyer_chat_id,
            sanitize_telegram_text(
f"""✨ Транзакция подтверждена!
👤 Пользователь: {username}
⭐ Звёзды: {quantity}
🔖 Ref ID: Ref#{ref_id}
📎 Чек: tonviewer.com/transaction/{tx_hash}
"""
            ),
        )
        
        if config.get("SEND_SUCCESS_NOTIFICATION", True):
            await self.send_success_notification(c, username, quantity, tx_hash, ref_id, amount_rub, profit)
        
        orders = orders_info.get(buyer_chat_id, [])
        for order in orders:
            if order["orderID"] == orderID and not order.get("completed", False):
                order["completed"] = True
                break

    async def send_success_notification(self, c: Cardinal, username: str, quantity: int,
                                      tx_hash: str, ref_id: str, amount_rub: float, profit: float):
        masked_username = mask_username(username)
        tg_id = get_tg_id_from_cache()
        
        if tg_id:
            c.telegram.bot.send_message(
                tg_id,
                sanitize_telegram_text(
                    f"""
💙 Транзакция завершена!
🔗 Чек: tonviewer.com/transaction/{tx_hash}

👤 Покупатель: {masked_username}  
⭐ Stars: {quantity}  
🔑 Ref ID: Ref#{ref_id}  

💸 Заказ: {amount_rub} руб  
💰 Прибыль: {profit:.2f} руб  
🟢 Статус: Успешно
"""
                ),
                parse_mode="HTML",
            )

    async def deactivate_lots(self, c: Cardinal):
        c.update_lots_and_categories()
        try:
            subcategory = c.account.get_subcategory(
                FunPayAPI.types.SubCategoryTypes.COMMON, SUBCATEGORY_ID
            )
            my_lots = c.tg_profile.get_sorted_lots(2).get(subcategory, {})
            for lot_id, lot in my_lots.items():
                fields = c.account.get_lot_fields(lot_id)
                if fields:
                    fields.active = False
                    c.account.save_lot(fields)
                    logger.debug(f"Лот {lot_id} деактивирован из-за недостатка средств.")
        except Exception as e:
            logger.error(f"Ошибка при деактивации лотов: {e}")

    def send_error_with_inline_url(self, c: Cardinal, user_id: int, orderID: str, error: str):
        if not user_id:
            return
            
        keyboard = InlineKeyboardMarkup(row_width=1)
        keyboard.add(
            InlineKeyboardButton(
                text="Открыть заказ FunPay", 
                url=f"https://funpay.com/orders/{orderID}/"
            ),
            InlineKeyboardButton(
                text="Вернуть заказ", 
                callback_data=f"refund_order_{orderID}"
            )
        )
        
        text_message = (
            f"🔴 У вас произошла ошибка в заказе #{orderID}\n"
            f"Ошибка: {error}\n"
            "Просьба вернуть средства"
        )
        
        c.telegram.bot.send_message(
            user_id, 
            sanitize_telegram_text(text_message), 
            reply_markup=keyboard
        )

payment_processor = PaymentProcessor()

def handle_new_order_stars(c: Cardinal, e: NewOrderEvent, *args):
    global RUNNING, orders_info
    
    if not RUNNING:
        return
    
    OrderID = e.order.id
    buyer_chat_id_e = e.order.buyer_id
    
    match = re.search(
        r"(\d+)\s*(Stars?|Звёзд|Звезд|🌟)", e.order.description, re.IGNORECASE
    )
    if not match:
        return
    
    stars_count = int(match.group(1))
    if stars_count < MIN_STARS or stars_count > MAX_STARS:
        return
    
    total_stars = stars_count
    if e.order.amount >= 1:
        total_stars = stars_count * e.order.amount
        c.send_message(
            buyer_chat_id_e,
            sanitize_telegram_text(
                f"Вы приобрели {e.order.amount} лотов, общее количество Stars: {total_stars}."
            ),
        )
    
    try:
        buyer_chat = c.account.get_chat_by_name(e.order.buyer_username, True)
        if buyer_chat is None:
            raise AttributeError
        buyer_chat_id = buyer_chat.id
    except AttributeError:
        return
    
    username_from_order = None
    try:
        order_details = c.account.get_order(OrderID)
        if order_details:
            for param_name, param_value in order_details.buyer_params.items():
                if param_name == "Telegram Username" and param_value:
                    if not any(link in param_value.lower() for link in ["http://", "https://", "t.me/"]):
                        if not param_value.startswith('@'):
                            param_value = '@' + param_value
                        username_from_order = param_value
                        break
    except Exception as e:
        logger.error(f"Ошибка при получении расширенных данных заказа: {e}")
    
    if buyer_chat_id not in orders_info:
        orders_info[buyer_chat_id] = []
    
    order_info = {
        "username": username_from_order,
        "confirmed": False,
        "completed": False,
        "orderID": OrderID,
        "stars_count": total_stars,
        "amount_rub": e.order.price,
        "auto_detected": username_from_order is not None
    }
    
    orders_info[buyer_chat_id].append(order_info)
    
    if username_from_order:
        fragment_found_name = None
        try:
            search_payload = {
                "query": username_from_order.replace("@", ""),
                "quantity": 50,
                "method": "searchStarsRecipient",
            }
            response_search = requests.post(url, headers=headers, data=search_payload, timeout=10)
            response_search.raise_for_status()
            data_search = response_search.json()
            if data_search.get("ok"):
                fragment_found_name = data_search.get("found", {}).get("name")
        except Exception as err:
            logger.error(f"Ошибка при проверке username: {err}")
        
        order_info["fragment_found_name"] = fragment_found_name
        
        masked_username = username_from_order
        masked_fragment = mask_username(fragment_found_name) if fragment_found_name else "Не определен"
        
        message_text = (
            f"📃 Подтвердите данные:\n"
            "\n"
            f"👤 Юзернейм: {masked_username}\n"
            f"📝 Никнейм: {masked_fragment}\n"
            f"⭐️ Кол.во звезд: {total_stars}\n"
            "\n"
            "✅ 'Да' — подтвердить\n"
            "❌ 'Нет' — изменить\n"
            "🔄 '!бэк' — возврат\n"
        )
    else:
        message_text = (
            f"⭐️ Заказ на {total_stars} звезд принят!\n"
            "\n"
            "📤 Отправьте @username\n"
            "⚠️ Без @username пополнение невозможно!\n\n"
            "↩️ '!бэк' — отмена заказа\n"
        )
    
    c.send_message(buyer_chat_id, sanitize_telegram_text(message_text))

def handle_new_message_text(c: Cardinal, e: NewMessageEvent, *args):
    global RUNNING, orders_info
    
    if not RUNNING:
        return
    
    buyer_chat_id = e.message.chat_id
    my_user = c.account.username
    my_id = c.account.id

    if buyer_chat_id == my_id:
        return
    
    if e.message.author.lower() in ["funpay", my_user.lower()]:
        return
    
    if e.message.text.strip().lower().startswith("!status"):
        handle_status_command(c, e)
        return
    
    if buyer_chat_id not in orders_info or not orders_info[buyer_chat_id]:
        return
    
    current_order = next(
        (
            o
            for o in reversed(orders_info[buyer_chat_id])
            if not o.get("completed", False)
        ),
        None,
    )
    if current_order is None:
        return

    if e.message.text.strip().lower() == "!бэк":
        handle_refund_command(c, e, current_order, buyer_chat_id)
        return

    handle_user_response(c, e, current_order, buyer_chat_id)

def handle_refund_command(c: Cardinal, e: NewMessageEvent, current_order: dict, buyer_chat_id: int):
    if (current_order.get("completed", False) or 
        current_order.get("is_canceled", False) or 
        current_order.get("answered", False)):
        return
    
    try:
        c.account.refund(current_order["orderID"])
        current_order["is_canceled"] = True
        c.send_message(
            buyer_chat_id,
            sanitize_telegram_text(
                "❌ Заказ отменен, средства возвращены.\n"
                "Вы не можете возобновить этот заказ.\n"
                "Для нового заказа оформите оплату заново."
            ),
        )
        tg_id = get_tg_id_from_cache()
        if tg_id:
            c.telegram.bot.send_message(
                tg_id,
                sanitize_telegram_text(
                    f"🔄 Возврат средств по заказу #{current_order['orderID']}\n"
                    f"👤 Покупатель: {mask_username(e.message.author)}"
                )
            )
    except Exception as refund_err:
        logger.error(f"Ошибка при возврате средств: {refund_err}")
        c.send_message(
            buyer_chat_id,
            sanitize_telegram_text(
                "❌ Не удалось выполнить возврат средств."
                "Пожалуйста, свяжитесь с продавцом."
            ),
        )

def handle_user_response(c: Cardinal, e: NewMessageEvent, current_order: dict, buyer_chat_id: int):
    user_response = e.message.text.strip().lower()
    
    if (current_order.get("auto_detected") and 
        current_order["username"] is not None and 
        not current_order["confirmed"]):
        
        if user_response in ["да", "yes", "y", "д"]:
            current_order["confirmed"] = True
            current_order["answered"] = True
            process_confirmed_order(c, buyer_chat_id, current_order)
        elif user_response in ["нет", "no", "n", "н"]:
            current_order["answered"] = True
            current_order["auto_detected"] = False
            current_order["username"] = None
            c.send_message(
                e.message.chat_id,
                sanitize_telegram_text("Пожалуйста, введите @username ещё раз."),
            )
        else:
            c.send_message(
                e.message.chat_id,
                sanitize_telegram_text("Пожалуйста, ответьте 'да' или 'нет'."),
            )
        return
    
    if current_order["username"] is None:
        if not re.match(r"^@\w+$", e.message.text.strip()):
            c.send_message(
                e.message.chat_id,
                sanitize_telegram_text("❌ Неверный формат username. Пожалуйста, введите @username."),
            )
            return
        
        username = e.message.text.strip()
        current_order["username"] = username
        current_order["confirmed"] = False
        
        fragment_found_name = None
        try:
            search_payload = {
                "query": username.replace("@", ""),
                "quantity": 50,
                "method": "searchStarsRecipient",
            }
            response_search = requests.post(url, headers=headers, data=search_payload, timeout=10)
            response_search.raise_for_status()
            data_search = response_search.json()
            if data_search.get("ok"):
                fragment_found_name = data_search.get("found", {}).get("name")
        except:
            pass
        
        current_order["fragment_found_name"] = fragment_found_name
        additional_info = f"\n⁡🚀 Telegram ник: {mask_username(fragment_found_name)}" if fragment_found_name else ""
        
        c.send_message(
            e.message.chat_id,
            sanitize_telegram_text(
                f"⁡🚀  Username: {username} | {additional_info} \n\n"
                "Если информация верна, напишите 'Да'. Если хотите изменить никнейм, напишите 'Нет'. Для возврата средств введите '!бэк'.\n\nНик в блюре для того чтобы FunPay не блокировал."
            ),
        )
        return
    
    if current_order["username"] is not None and not current_order["confirmed"]:
        if user_response == "да":
            current_order["confirmed"] = True
            current_order["answered"] = True
            process_confirmed_order(c, buyer_chat_id, current_order)
        elif user_response == "нет":
            current_order["answered"] = True
            current_order["username"] = None
            c.send_message(
                e.message.chat_id,
                sanitize_telegram_text("Пожалуйста, введите @username ещё раз."),
            )
        else:
            c.send_message(
                e.message.chat_id,
                sanitize_telegram_text("Пожалуйста, ответьте 'Да' или 'Нет'."),
            )

def process_confirmed_order(c: Cardinal, buyer_chat_id: int, order: dict):
    orderID = order["orderID"]
    stars_quantity = order.get("stars_count", 50)
    amount_rub = order.get("amount_rub", 0)
    
    position = payment_processor.enqueue_payment(
        c, buyer_chat_id, order["username"], stars_quantity, orderID, amount_rub
    )
    
    c.send_message(
        buyer_chat_id,
        sanitize_telegram_text(
            f"✅ Ваш заказ подтверждён ✅\n"
            f"Позиция в очереди: {position}."
        ),
    )

def handle_status_command(c: Cardinal, e: NewMessageEvent):
    text = e.message.text.strip()
    parts = text.split(" ", 1)
    date_str = parts[1].strip() if len(parts) > 1 and parts[1].strip() else datetime.datetime.now().strftime("%d.%m.%Y")

    if date_str not in stats_data:
        msg = f"Статистика за {date_str} отсутствует.\nВероятно, не было транзакций или дата указана неверно."
        c.send_message(e.message.chat_id, msg)
        return

    day_stats = stats_data[date_str]
    successful = day_stats.get("successful_transactions", 0)
    unsuccessful = day_stats.get("unsuccessful_transactions", 0)
    quantities = day_stats.get("quantities_sold", {})
    total_stars = sum(int(qty) * count for qty, count in quantities.items())

    text = f"📊 Статистика за {date_str}:\n"
    text += f"✅ Успешные транзакции: {successful}\n"
    text += f"❌ Неуспешные: {unsuccessful}\n"
    text += f"⭐ Всего Stars: {total_stars}\n"
    
    total_profit = day_stats.get("total_profit", 0.0)
    text += f"💰 Прибыль: {total_profit:.2f} руб"

    c.send_message(e.message.chat_id, text)

def stars_auto(c: Cardinal, e, *args):
    if isinstance(e, NewOrderEvent):
        handle_new_order_stars(c, e, *args)
    elif isinstance(e, NewMessageEvent):
        handle_new_message_text(c, e, *args)

def activate_lots(c: Cardinal, chat_id: int):
    json_path = "storage/stars/lots.json"
    if not os.path.exists(json_path):
        logger.error(f"Файл {json_path} не найден.")
        c.send_message(chat_id, sanitize_telegram_text(f"❌ Файл {json_path} не найден."))
        return
    
    try:
        with open(json_path, "r", encoding="utf-8") as file:
            lot_ids: List[int] = json.load(file)
        logger.debug(f"Загруженные ID лотов: {lot_ids}")
    except Exception as e:
        logger.error(f"Не удалось прочитать файл {json_path}: {e}")
        c.send_message(chat_id, sanitize_telegram_text(f"❌ Не удалось прочитать файл: {e}"))
        return
    
    if not isinstance(lot_ids, list):
        logger.error(f"Неверный формат данных в {json_path}. Ожидался список ID.")
        c.send_message(chat_id, sanitize_telegram_text("❌ Неверный формат JSON."))
        return
    
    activated_lots = []
    already_active = []
    not_found = []
    invalid_ids = []
    errors = []
    
    for i, lot_id in enumerate(lot_ids):
        if not isinstance(lot_id, int):
            invalid_ids.append(lot_id)
            continue
        
        try:
            fields = c.account.get_lot_fields(lot_id)
            if fields is None:
                not_found.append(lot_id)
                continue
            
            if fields.active:
                already_active.append(lot_id)
                continue
            
            fields.active = True
            c.account.save_lot(fields)
            activated_lots.append(lot_id)
            
            if i < len(lot_ids) - 1:
                time.sleep(0.50)
                
        except Exception as e:
            errors.append((lot_id, str(e)))
    
    report_lines = ["✅ Активация лотов завершена.\n"]
    if activated_lots:
        report_lines.append(f"Активированы: {', '.join(map(str, activated_lots))}")
    if already_active:
        report_lines.append(f"Уже активны: {', '.join(map(str, already_active))}")
    if not_found:
        report_lines.append(f"Не найдены: {', '.join(map(str, not_found))}")
    if invalid_ids:
        report_lines.append(f"Неверные ID: {', '.join(map(str, invalid_ids))}")
    if errors:
        error_details = "; ".join([f"{lot_id}: {err}" for lot_id, err in errors])
        report_lines.append(f"Ошибки: {error_details}")
    
    tg_id = get_tg_id_from_cache()
    if tg_id:
        c.telegram.bot.send_message(tg_id, sanitize_telegram_text("\n".join(report_lines)))

def deactivate_lots(c: Cardinal, chat_id: int):
    c.update_lots_and_categories()
    try:
        subcategory = c.account.get_subcategory(
            FunPayAPI.types.SubCategoryTypes.COMMON, SUBCATEGORY_ID
        )
        my_lots = c.tg_profile.get_sorted_lots(2).get(subcategory, {})
    except Exception as e:
        c.send_message(chat_id, sanitize_telegram_text(f"❌ Ошибка получения лотов: {e}"))
        return
    
    if not my_lots:
        c.send_message(chat_id, sanitize_telegram_text("ℹ️ Нет лотов для деактивации."))
        return
    
    deactivated_lots = []
    already_inactive = []
    errors = []
    not_found = []
    
    lot_items = list(my_lots.items())
    for i, (lot_id, lot) in enumerate(lot_items):
        if not isinstance(lot_id, int):
            continue
        
        try:
            fields = c.account.get_lot_fields(lot_id)
            if fields is None:
                not_found.append(lot_id)
                continue
            
            if not fields.active:
                already_inactive.append(lot_id)
                continue
            
            fields.active = False
            c.account.save_lot(fields)
            deactivated_lots.append(lot_id)
            
            if i < len(lot_items) - 1:
                time.sleep(0.50)
                
        except Exception as e:
            errors.append((lot_id, str(e)))
    
    report_lines = ["✅ Деактивация лотов завершена.\n"]
    if deactivated_lots:
        report_lines.append(f"Деактивированы: {', '.join(map(str, deactivated_lots))}")
    if already_inactive:
        report_lines.append(f"Уже были неактивны: {', '.join(map(str, already_inactive))}")
    if not_found:
        report_lines.append(f"Не найдены: {', '.join(map(str, not_found))}")
    if errors:
        error_details = "; ".join([f"{lot_id}: {err}" for lot_id, err in errors])
        report_lines.append(f"Ошибки: {error_details}")
    
    tg_id = get_tg_id_from_cache()
    if tg_id:
        c.telegram.bot.send_message(tg_id, sanitize_telegram_text("\n".join(report_lines)))

def get_lot_ids_from_category(c: Cardinal, chat_id: int):
    global RUNNING_GET_LOTS
    
    if RUNNING_GET_LOTS:
        c.telegram.bot.send_message(chat_id, "❌ Процесс уже запущен! Пожалуйста, дождитесь завершения.")
        return
    
    RUNNING_GET_LOTS = True
    try:
        profile = c.account.get_user(c.account.id)
        lots = profile.get_lots()
        lot_ids = []

        for lot in lots:
            if lot.subcategory.id == 2418:
                lot_ids.append(lot.id)

        file_path = 'storage/stars/lots.json'
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(lot_ids, f, ensure_ascii=False, indent=4)

        if lot_ids:
            c.telegram.bot.send_message(chat_id, f"✅ Найдены лоты: {', '.join(map(str, lot_ids))}")
        else:
            c.telegram.bot.send_message(chat_id, "❌ Лоты не найдены.")
            
    except Exception as e:
        logger.error(f"Ошибка при получении лотов: {e}")
        c.telegram.bot.send_message(chat_id, f"❌ Произошла ошибка при получении лотов: {str(e)}")
    finally:
        RUNNING_GET_LOTS = False

def stars_config(c: Cardinal, m: types.Message, edit_message_id: int = None):
    cancel_editing(m.chat.id)
    
    try:
        subcategory = c.account.get_subcategory(
            FunPayAPI.types.SubCategoryTypes.COMMON, SUBCATEGORY_ID
        )
        my_lots = c.tg_profile.get_sorted_lots(2).get(subcategory, {})
        active_lots = []
        for lot_id, lot in my_lots.items():
            fields = c.account.get_lot_fields(lot_id)
            if fields and fields.active:
                active_lots.append(lot_id)

        today = datetime.datetime.now().strftime("%d %b")
        today_stats = stats_data.get(today, {})
        today_profit = today_stats.get("total_profit", 0.0)
        today_success = today_stats.get("successful_transactions", 0)
        today_failed = today_stats.get("unsuccessful_transactions", 0)

        total_profit = 0.0
        total_success = 0
        total_failed = 0
        total_stars = 0
        for date_data in stats_data.values():
            total_profit += date_data.get("total_profit", 0.0)
            total_success += date_data.get("successful_transactions", 0)
            total_failed += date_data.get("unsuccessful_transactions", 0)
            quantities = date_data.get("quantities_sold", {})
            for qty, count in quantities.items():
                total_stars += int(qty) * count

        async def get_balance():
            try:
                balance = await check_wallet_balance()
                return f"{balance:.2f} TON"
            except Exception as e:
                logger.error(f"Ошибка при получении баланса: {e}")
                return "Ошибка"

        future = asyncio.run_coroutine_threadsafe(get_balance(), payment_processor.loop)
        try:
            wallet_balance = future.result(timeout=10)
        except:
            wallet_balance = "Н/Д"

        license_status = "🟢 Активна"  # Всегда активно
        license_until = "Навсегда"
        
        keyboard = InlineKeyboardMarkup(row_width=2)

        if RUNNING:
            status_btn = InlineKeyboardButton(text="🟢 Система активна", callback_data="stop_stars")
        else:
            status_btn = InlineKeyboardButton(text="🔴 Система остановлена", callback_data="start_stars")
        keyboard.add(status_btn)

        lot_buttons = []
        if active_lots:
            lot_buttons.append(InlineKeyboardButton(
                text=f"💤 Выкл лоты ({len(active_lots)})", 
                callback_data="deactivate_lots"
            ))
        else:
            lot_buttons.append(InlineKeyboardButton(
                text="⚡️ Вкл лоты", 
                callback_data="activate_lots"
            ))
        
        lot_buttons.append(InlineKeyboardButton(
            text="📋 Получить ID лотов", 
            callback_data="get_lot_ids"
        ))
        keyboard.row(*lot_buttons)

        analytics_buttons = [
            InlineKeyboardButton(text="📦 Статистика", callback_data="autostars_show_stats"),
            InlineKeyboardButton(text="🧪 Тест заказа", callback_data="test_order")
        ]
        keyboard.row(*analytics_buttons)

        settings_buttons = [
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="open_settings"),
            InlineKeyboardButton(text="📃 Логи", callback_data="send_log")
        ]
        keyboard.row(*settings_buttons)

        keyboard.add(InlineKeyboardButton(
            text="🔍 Проверить систему", 
            callback_data="check_settings"
        ))

        keyboard.add(InlineKeyboardButton(
            text="🛒 Магазин плагинов", 
            url="t.me/veemp_shop"
        ))

        status_icon = "🟢" if RUNNING else "🔴"
        status_text = "АКТИВНА" if RUNNING else "ОСТАНОВЛЕНА"
        
        message_text = f"""
💼 <b>AUTOSTARS</b>

{status_icon} <b>Система: {status_text}</b>

🔐 <b>Лицензия:</b> <code>{license_status}</code>
⏰ <b>Действует до:</b> <code>{license_until}</code>
💰 <b>Баланс кошелька:</b> <code>{wallet_balance}</code>
📃 <b>Активных лотов:</b> <code>{len(active_lots)}</code>

<b>👨‍💻 Разработчик:</b> @veemp
"""

        if edit_message_id:
            safe_edit_message_text(
                c.telegram.bot,
                chat_id=m.chat.id,
                message_id=edit_message_id,
                text=message_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        else:
            c.telegram.bot.send_message(
                m.chat.id, 
                text=message_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
    except Exception as e:
        logger.error(f"Ошибка в stars_config: {e}")
        c.telegram.bot.send_message(
            m.chat.id, 
            sanitize_telegram_text("❌ Произошла ошибка при обновлении страницы. Поробуйте через пару секунд...")
        )

def create_settings_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=1)
    
    keyboard.add(InlineKeyboardButton(text="💼 Настройки кошельков", callback_data="wallet_settings"))
    keyboard.add(InlineKeyboardButton(text="🔐 API ключи и токены", callback_data="api_settings"))
    keyboard.add(InlineKeyboardButton(text="🏴 Настройки Fragment", callback_data="fragment_settings"))
    
    refund_mode = "🔄 Авто" if config["AUTO_REFUND"] else "⏸️ Ручной"
    keyboard.add(InlineKeyboardButton(text=f"Режим возвратов: {refund_mode}", callback_data="toggle_refund_mode"))
    
    notification_status = "🔔 Вкл" if config["SEND_SUCCESS_NOTIFICATION"] else "🔕 Выкл"
    keyboard.add(InlineKeyboardButton(text=f"Уведомления: {notification_status}", callback_data="toggle_notification"))
    
    sender_status = "👤 Показан" if config["SHOW_SENDER"] == "1" else "👻 Скрыт"
    keyboard.add(InlineKeyboardButton(text=f"Отправитель: {sender_status}", callback_data="toggle_sender_mode"))
    
    keyboard.add(InlineKeyboardButton(text="⬅️ Назад в главное меню", callback_data="back_to_main"))
    
    return keyboard

def show_settings_category(c: Cardinal, chat_id: int, message_id: int, category: str):
    cancel_editing(chat_id)
    
    if category == "wallet_settings":
        keyboard = InlineKeyboardMarkup(row_width=1)
        keyboard.add(InlineKeyboardButton(text="📭 Адрес отправителя", callback_data="wallet_settings:edit_address"))
        keyboard.add(InlineKeyboardButton(text="📬 Адрес Fragment", callback_data="wallet_settings:edit_destination_address"))
        keyboard.add(InlineKeyboardButton(text="🔐 Слова", callback_data="wallet_settings:edit_mnemonic"))
        keyboard.add(InlineKeyboardButton(text="⬅️ Назад к настройкам", callback_data="open_settings"))
        text = "💼 <b>Настройки кошельков</b>\n\nУправление TON кошельками и адресами:"
        
    elif category == "api_settings":
        keyboard = InlineKeyboardMarkup(row_width=1)
        keyboard.add(InlineKeyboardButton(text="🔑 TON API", callback_data="api_settings:edit_ton_api"))
        keyboard.add(InlineKeyboardButton(text="📈 API курса TON", callback_data="api_settings:edit_ton_rate_api"))
        keyboard.add(InlineKeyboardButton(text="⬅️ Назад к настройкам", callback_data="open_settings"))
        text = "🔐 <b>API ключи и токены</b>\n\nНастройки API и идентификации:"
        
    elif category == "fragment_settings":
        keyboard = InlineKeyboardMarkup(row_width=1)
        keyboard.add(InlineKeyboardButton(text="🔑 Fragment хэш", callback_data="fragment_settings:edit_hash"))
        keyboard.add(InlineKeyboardButton(text="🍪 Fragment куки", callback_data="fragment_settings:edit_cookie"))
        keyboard.add(InlineKeyboardButton(text="⬅️ Назад к настройкам", callback_data="open_settings"))
        text = "🌐 <b>Настройки Fragment</b>\n\nПараметры для работы с Fragment.com:"
    
    safe_edit_message_text(
        c.telegram.bot,
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )

def show_main_settings_menu(c: Cardinal, chat_id: int, message_id: int):
    cancel_editing(chat_id)
    
    keyboard = create_settings_keyboard()
    safe_edit_message_text(
        c.telegram.bot,
        chat_id=chat_id,
        message_id=message_id,
        text="⚙️ <b>Настройки</b>\n\nВыберите категорию для изменения параметров:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

def init_commands(c: Cardinal):
    c.add_telegram_commands(
        UUID,
        [
            ("stars", "Настройки автоматической продажи звезд", True),
        ],
    )
    c.telegram.msg_handler(lambda m: stars_config(c, m), commands=["stars"])

    @c.telegram.bot.callback_query_handler(
        func=lambda call: call.data in [
            "start_stars", "stop_stars", "autostars_dates_stats", "activate_lots", "deactivate_lots",
            "send_log", "open_settings", "back_to_main", "toggle_refund_mode", 
            "toggle_notification", "toggle_sender_mode", "get_lot_ids",
            "check_settings", "test_order", "confirm_test_order", "cancel_test_order",
            "wallet_settings", "api_settings", "fragment_settings", "autostars_show_stats",
            "autostars_overall_stats", "autostars_quantities_stats", "autostars_profit_stats"
        ] or call.data.startswith(("wallet_settings", "api_settings", "fragment_settings")) or 
        call.data.startswith("edit_") or (":" in call.data and "edit_" in call.data) or
        call.data.startswith("cancel_edit:")
    )
    def handle_config_callback(call):
        global RUNNING
        
        data = call.data
        chat_id = call.message.chat.id
        message_id = call.message.message_id
        
        try:
            c.telegram.bot.answer_callback_query(call.id)
        except:
            pass
            
        try:    
            if data == "start_stars":
                future = asyncio.run_coroutine_threadsafe(check_wallet_balance(), payment_processor.loop)
                try:
                    balance_ton = future.result(timeout=10)
                    RUNNING = True
                    c.telegram.bot.send_message(
                        chat_id,
                        sanitize_telegram_text(f"🟢 Автопродажа звезд активирована.\nБаланс кошелька: {balance_ton} TON."),
                    )
                    stars_config(c, call.message, edit_message_id=message_id)
                except Exception as e:
                    c.telegram.bot.send_message(chat_id, "❌ Не удалось проверить баланс кошелька.")

            elif data == "stop_stars":
                RUNNING = False
                c.telegram.bot.send_message(chat_id, "🔴 Автопродажа звезд <b>отключена</b>.")
                stars_config(c, call.message, edit_message_id=message_id)

            elif data == "activate_lots":
                activate_lots(c, chat_id)
                stars_config(c, call.message, edit_message_id=message_id)

            elif data == "deactivate_lots":
                deactivate_lots(c, chat_id)
                stars_config(c, call.message, edit_message_id=message_id)

            elif data == "get_lot_ids":
                get_lot_ids_from_category(c, chat_id)
                stars_config(c, call.message, edit_message_id=message_id)

            elif data == "send_log":
                if not os.path.exists(LOG_FILE):
                    c.telegram.bot.send_message(chat_id, "❌ Файл логов не найден.")
                    return
                try:
                    with open(LOG_FILE, "rb") as log_file:
                        c.telegram.bot.send_document(chat_id, document=log_file, caption="📄 Ваш лог AutoStars")
                except Exception as e:
                    c.telegram.bot.send_message(chat_id, f"❌ Не удалось отправить лог: {e}")

            elif data == "open_settings":
                show_main_settings_menu(c, chat_id, message_id)

            elif data in ["wallet_settings", "api_settings", "fragment_settings"]:
                show_settings_category(c, chat_id, message_id, data)

            elif data == "back_to_main":
                stars_config(c, call.message, edit_message_id=message_id)

            elif data == "toggle_refund_mode":
                config["AUTO_REFUND"] = not config["AUTO_REFUND"]
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(config, f, indent=4, ensure_ascii=False)
                show_main_settings_menu(c, chat_id, message_id)

            elif data == "toggle_notification":
                config["SEND_SUCCESS_NOTIFICATION"] = not config["SEND_SUCCESS_NOTIFICATION"]
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(config, f, indent=4, ensure_ascii=False)
                show_main_settings_menu(c, chat_id, message_id)

            elif data == "toggle_sender_mode":
                config["SHOW_SENDER"] = "1" if config["SHOW_SENDER"] == "0" else "0"
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(config, f, indent=4, ensure_ascii=False)
                show_main_settings_menu(c, chat_id, message_id)

            elif data.startswith("cancel_edit:"):
                category = data.replace("cancel_edit:", "")
                cancel_editing(chat_id)
                if category in ["wallet_settings", "api_settings", "fragment_settings"]:
                    show_settings_category(c, chat_id, message_id, category)
                else:
                    show_main_settings_menu(c, chat_id, message_id)

            elif data.startswith("edit_") or (":" in data and "edit_" in data):
                handle_edit_setting(c, call, data, chat_id, message_id)

            elif data.startswith("autostars_"):
                handle_stats_callback(c, call, data, chat_id, message_id)

            elif data == "check_settings":
                handle_check_settings(c, chat_id, message_id)

            elif data in ["test_order", "confirm_test_order", "cancel_test_order"]:
                handle_test_order(c, call, data, chat_id, message_id)

        except Exception as e:
            logger.error(f"Ошибка в обработчике callback: {e}")
            try:
                c.telegram.bot.send_message(chat_id, f"❌ Произошла ошибка: {str(e)}")
            except:
                pass

    @c.telegram.bot.callback_query_handler(func=lambda call: call.data.startswith("refund_order_"))
    def refund_order_callback(call):
        order_id = call.data.replace("refund_order_", "")
        try:
            c.account.refund(order_id)
            msg = f"✅ Заказ #{order_id} возвращён покупателю."
        except Exception as e:
            msg = f"❌ Ошибка при возврате заказа #{order_id}: {e}"
        c.telegram.bot.answer_callback_query(call.id, text=msg, show_alert=True)
        c.telegram.bot.send_message(call.message.chat.id, sanitize_telegram_text(msg))

def get_nested_config_value(config_dict: dict, key: str):
    if "->" in key:
        keys = key.split("->")
        current = config_dict
        for k in keys:
            current = current.get(k, {})
        return current
    return config_dict.get(key)

def set_nested_config_value(config_dict: dict, key: str, value):
    if "->" in key:
        keys = key.split("->")
        current = config_dict
        for k in keys[:-1]:
            if k not in current:
                current[k] = {}
            current = current[k]
        current[keys[-1]] = value
    else:
        config_dict[key] = value

def handle_edit_setting(c: Cardinal, call, data: str, chat_id: int, message_id: int):
    if ":" in data:
        category, action = data.split(":", 1)
    else:
        category = None
        action = data
    
    if action.startswith("edit_"):
        setting_key = action[5:]
    else:
        setting_key = action
    
    setting_map = {
        "address": ("ADDRESS", "адрес кошелька (отправитель)", str),
        "destination_address": ("DESTINATION_ADDRESS", "адрес кошелька (получатель)", str),
        "ton_api": ("API_KEY", "API_KEY", str),
        "ton_rate_api": ("TON_RATE_API", "API курса TON", str),
        "hash": ("fragment_api->hash", "Fragment хэш", str),
        "cookie": ("fragment_api->cookie", "Fragment куки", str),
        "mnemonic": ("MNEMONIC", "Mnemonic фразы", list)
    }
    
    if setting_key not in setting_map:
        return
    
    setting_key, description, value_type = setting_map[setting_key]
    current_value = get_nested_config_value(config, setting_key)
    
    if value_type == list and isinstance(current_value, list):
        current_value = " ".join(current_value)
    
    user_editing_state[chat_id] = {
        'setting_key': setting_key,
        'description': description,
        'value_type': value_type,
        'category': category,
        'active': True
    }
    
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(InlineKeyboardButton(text="❌ Отмена", callback_data=f"cancel_edit:{category}" if category else "open_settings"))
    
    safe_edit_message_text(
        c.telegram.bot,
        chat_id=chat_id,
        message_id=message_id,
        text=sanitize_telegram_text(f"Текущий {description}: {current_value}\n\nВведите новое значение:"),
        reply_markup=keyboard,
    )

    def handle_new_value(m):
        if chat_id not in user_editing_state or not user_editing_state[chat_id].get('active', False):
            c.telegram.bot.send_message(chat_id, "❌ Редактирование было отменено.")
            return
        
        user_input = m.text.strip()
        
        if m.text.startswith("/"):
            cancel_editing(chat_id)
            c.telegram.bot.send_message(chat_id, "❌ Ввод отменен, так как обнаружена команда.")
            if category:
                show_settings_category(c, chat_id, message_id, category)
            else:
                show_main_settings_menu(c, chat_id, message_id)
            return
        
        try:
            new_value = user_input
            if value_type == int:
                new_value = int(new_value)
            elif value_type == list:
                new_value = new_value.split()
                if len(new_value) != 24:
                    c.telegram.bot.send_message(chat_id, "❌ Фразы должны состоять из 24 слов.")
                    return
            
            set_nested_config_value(config, setting_key, new_value)
            
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
            
            c.telegram.bot.send_message(chat_id, f"✅ {description} успешно обновлен.")
            
            cancel_editing(chat_id)
            
            if category:
                show_settings_category(c, chat_id, message_id, category)
            else:
                show_main_settings_menu(c, chat_id, message_id)
            
        except ValueError:
            c.telegram.bot.send_message(chat_id, "❌ Неверный формат значения.")
        except Exception as e:
            c.telegram.bot.send_message(chat_id, f"❌ Ошибка при сохранении: {e}")
            cancel_editing(chat_id)

    c.telegram.bot.register_next_step_handler(call.message, handle_new_value)

def handle_stats_callback(c: Cardinal, call, data: str, chat_id: int, message_id: int):
    if data == "autostars_show_stats":
        today = datetime.datetime.now().strftime("%d.%m.%Y")
        today_stats = stats_data.get(today, {})
        today_profit = today_stats.get("total_profit", 0.0)
        today_success = today_stats.get("successful_transactions", 0)
        today_failed = today_stats.get("unsuccessful_transactions", 0)
        
        today_stars = 0
        quantities_today = today_stats.get("quantities_sold", {})
        for qty, count in quantities_today.items():
            today_stars += int(qty) * count

        total_profit = 0.0
        total_success = 0
        total_failed = 0
        total_stars = 0
        all_dates = []
        
        for date, date_data in stats_data.items():
            total_profit += date_data.get("total_profit", 0.0)
            total_success += date_data.get("successful_transactions", 0)
            total_failed += date_data.get("unsuccessful_transactions", 0)
            all_dates.append(date)
            
            quantities = date_data.get("quantities_sold", {})
            for qty, count in quantities.items():
                total_stars += int(qty) * count
        
        avg_profit_per_order = total_profit / total_success if total_success > 0 else 0
        
        best_day = None
        best_profit = 0
        for date, date_data in stats_data.items():
            profit = date_data.get("total_profit", 0)
            if profit > best_profit:
                best_profit = profit
                best_day = date

        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            InlineKeyboardButton(text="📈 Общая статистика", callback_data="autostars_overall_stats"),
            InlineKeyboardButton(text="⭐️ По количеству Stars", callback_data="autostars_quantities_stats"),
            InlineKeyboardButton(text="💰 По прибыли", callback_data="autostars_profit_stats"),
            InlineKeyboardButton(text="📅 По датам", callback_data="autostars_dates_stats"),
            InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main"),
        )
        
        message_text = f"""
<b>✨ Статистика • {today}</b>

<b>— СЕГОДНЯ</b>
💸 <code>{today_profit:.2f}</code> руб
✅ <code>{today_success}</code>  ❌ <code>{today_failed}</code>  ⭐ <code>{today_stars}</code>

<b>— ВСЕГО</b>
💰 <code>{total_profit:.2f}</code> руб
📊 <code>{avg_profit_per_order:.2f}</code> руб/заказ
✅ <code>{total_success}</code>  ❌ <code>{total_failed}</code>  ⭐ <code>{total_stars}</code>

<b>🚀 Лучший день:</b> {best_day if best_day else 'Н/Д'}
🎯 <code>{best_profit:.2f}</code> руб

Выберите тип статистики:
"""
        safe_edit_message_text(
            c.telegram.bot,
            chat_id=chat_id,
            message_id=message_id,
            text=message_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    elif data == "autostars_overall_stats":
        total_profit = 0.0
        total_success = 0
        total_failed = 0
        total_stars = 0
        total_days = len(stats_data)
        
        for date_data in stats_data.values():
            total_profit += date_data.get("total_profit", 0.0)
            total_success += date_data.get("successful_transactions", 0)
            total_failed += date_data.get("unsuccessful_transactions", 0)
            
            quantities = date_data.get("quantities_sold", {})
            for qty, count in quantities.items():
                total_stars += int(qty) * count
        
        avg_profit_per_day = total_profit / total_days if total_days > 0 else 0
        avg_profit_per_order = total_profit / total_success if total_success > 0 else 0
        success_rate = (total_success / (total_success + total_failed)) * 100 if (total_success + total_failed) > 0 else 0

        message_text = f"""
<b>📈 Общая статистика</b>

<b>💰 Общая прибыль:</b> {total_profit:.2f} руб
<b>✅ Успешных:</b> {total_success}
<b>❌ Ошибок:</b> {total_failed}
<b>⭐ Всего Stars:</b> {total_stars}
<b>📅 Всего дней:</b> {total_days}

<b>📊 Средние показатели:</b>
├ <b>💸 Прибыль в день:</b> {avg_profit_per_day:.2f} руб
├ <b>📈 Прибыль за заказ:</b> {avg_profit_per_order:.2f} руб
└ <b>🎯 Успешность:</b> {success_rate:.1f}%
"""
        keyboard = InlineKeyboardMarkup(row_width=1)
        keyboard.add(InlineKeyboardButton(text="⬅️ Назад к статистике", callback_data="autostars_show_stats"))
        safe_edit_message_text(
            c.telegram.bot,
            chat_id=chat_id,
            message_id=message_id,
            text=message_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    elif data == "autostars_quantities_stats":
        all_quantities = {}
        for date_data in stats_data.values():
            quantities = date_data.get("quantities_sold", {})
            for qty, count in quantities.items():
                if qty not in all_quantities:
                    all_quantities[qty] = 0
                all_quantities[qty] += count

        if not all_quantities:
            message_text = "ℹ️ Нет данных о проданных количествах Stars."
        else:
            message_text = "<b>⭐️ Статистика по количеству Stars</b>\n\n"
            total_orders = sum(all_quantities.values())
            for qty, count in sorted(all_quantities.items(), key=lambda x: int(x[0])):
                percentage = (count / total_orders) * 100
                message_text += f"<b>{qty} Stars:</b> {count} продаж ({percentage:.1f}%)\n"

        keyboard = InlineKeyboardMarkup(row_width=1)
        keyboard.add(InlineKeyboardButton(text="⬅️ Назад к статистике", callback_data="autostars_show_stats"))
        safe_edit_message_text(
            c.telegram.bot,
            chat_id=chat_id,
            message_id=message_id,
            text=message_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    elif data == "autostars_profit_stats":
        daily_profits = []
        for date, date_data in stats_data.items():
            profit = date_data.get("total_profit", 0.0)
            if profit > 0:
                daily_profits.append((date, profit))
        
        daily_profits.sort(key=lambda x: x[1], reverse=True)
        
        message_text = "<b>💰 Статистика по прибыли</b>\n\n"
        if daily_profits:
            message_text += "<b>Топ дней по прибыли:</b>\n"
            for i, (date, profit) in enumerate(daily_profits[:10], 1):
                message_text += f"{i}. <b>{date}:</b> {profit:.2f} руб\n"
        else:
            message_text = "ℹ️ Нет данных о прибыли."

        keyboard = InlineKeyboardMarkup(row_width=1)
        keyboard.add(InlineKeyboardButton(text="⬅️ Назад к статистике", callback_data="autostars_show_stats"))
        safe_edit_message_text(
            c.telegram.bot,
            chat_id=chat_id,
            message_id=message_id,
            text=message_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    elif data == "autostars_dates_stats":
        sorted_dates = sorted(stats_data.keys(), reverse=True)
        
        message_text = "<b>📅 Статистика по датам</b>\n\n"
        
        if not sorted_dates:
            message_text += "ℹ️ Нет данных за указанные даты."
        else:
            for date in sorted_dates:
                date_data = stats_data[date]
                profit = date_data.get("total_profit", 0.0)
                success = date_data.get("successful_transactions", 0)
                failed = date_data.get("unsuccessful_transactions", 0)
                
                day_stars = 0
                quantities = date_data.get("quantities_sold", {})
                for qty, count in quantities.items():
                    try:
                        day_stars += int(qty) * count
                    except (ValueError, TypeError):
                        continue
                
                message_text += f"<b>{date}:</b>\n"
                message_text += f"  💰 {profit:.2f} руб | ✅ {success} | ❌ {failed} | ⭐ {day_stars}\n\n"

        keyboard = InlineKeyboardMarkup(row_width=1)
        keyboard.add(InlineKeyboardButton(text="⬅️ Назад к статистике", callback_data="autostars_show_stats"))
        safe_edit_message_text(
            c.telegram.bot,
            chat_id=chat_id,
            message_id=message_id,
            text=message_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )

def handle_check_settings(c: Cardinal, chat_id: int, message_id: int):
    try:
        future = asyncio.run_coroutine_threadsafe(check_wallet_balance(), payment_processor.loop)
        try:
            balance_ton = future.result(timeout=10)
        except Exception as e:
            balance_ton = f"Ошибка: {e}"

        check_results = []
        check_results.append(f"<b>💰 Баланс кошелька:</b> {balance_ton}")
        check_results.append(f"<b>🔑 API ключ:</b> {'✅ Установлен' if API_KEY and API_KEY != 'YOUR-API-KEY' else '❌ Отсутствует'}")
        check_results.append(f"<b>🔐 Fragment hash:</b> {'✅ Установлен' if FRAGMENT_HASH and FRAGMENT_HASH != 'YOUR-HASH' else '❌ Отсутствует'}")
        check_results.append(f"<b>🍪 Fragment cookie:</b> {'✅ Установлена' if FRAGMENT_COOKIE and FRAGMENT_COOKIE != 'YOUR-COOKIE' else '❌ Отсутствует'}")
        check_results.append(f"<b>🔑 Mnemonic фразы:</b> {'✅ Установлены' if MNEMONIC and MNEMONIC[0] != 'word1' else '❌ Отсутствуют'}")
        check_results.append(f"<b>📭 Адрес кошелька:</b> {'✅ Установлен' if ADDRESS and ADDRESS != 'YOUR-CRYPTO-ADDRESS-WALLET' else '❌ Отсутствует'}")
        check_results.append(f"<b>🤖 Авто-возврат:</b> {'✅ Включен' if config['AUTO_REFUND'] else '❌ Выключен'}")
        check_results.append(f"<b>🔔 Уведомления:</b> {'✅ Включены' if config['SEND_SUCCESS_NOTIFICATION'] else '❌ Выключены'}")
        check_results.append(f"<b>🔐 Лицензия:</b> {'✅ Активна'}")

        message_text = "<b>🔧 Проверка настроек</b>\n\n" + "\n".join(check_results)

        keyboard = InlineKeyboardMarkup(row_width=1)
        keyboard.add(
            InlineKeyboardButton(text="⚙️ Настроить параметры", callback_data="open_settings"),
            InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")
        )

        safe_edit_message_text(
            c.telegram.bot,
            chat_id=chat_id,
            message_id=message_id,
            text=message_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка при проверке настроек: {e}")
        c.telegram.bot.send_message(chat_id, f"❌ Ошибка при проверке настроек: {e}")

def handle_test_order(c: Cardinal, call, data: str, chat_id: int, message_id: int):
    if data == "test_order":
        async def get_test_price():
            try:
                test_username = "veemp"
                test_quantity = 50
                
                clean_username = remove_at_symbol(test_username)
                payload_search = {
                    "query": clean_username,
                    "quantity": test_quantity,
                    "method": "searchStarsRecipient",
                }
                
                response_search = requests.post(url, headers=headers, data=payload_search, timeout=30)
                response_search.raise_for_status()
                text_search = response_search.json()
                
                if 'found' in text_search and 'recipient' in text_search['found']:
                    recipient = text_search['found']['recipient']
                    
                    payload_init = {
                        "recipient": recipient,
                        "quantity": test_quantity,
                        "method": "initBuyStarsRequest",
                    }
                    
                    response_init = requests.post(url, headers=headers, data=payload_init, timeout=30)
                    response_init.raise_for_status()
                    text_init = response_init.json()
                    
                    if 'req_id' in text_init and 'amount' in text_init:
                        amount = float(text_init['amount'])
                        
                        keyboard = InlineKeyboardMarkup(row_width=2)
                        keyboard.add(
                            InlineKeyboardButton(text="✅ Подтвердить отправку", callback_data="confirm_test_order"),
                            InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_test_order")
                        )
                        
                        message_text = (
                            f"<b>🧪 Тестовый заказ 50 Stars</b>\n\n"
                            f"<b>⚠️ Внимание:</b> Будет списано <b>{amount:.4f} TON</b> с вашего кошелька!\n\n"
                            "Это реальная транзакция для проверки работы системы.\n"
                            "Звезды будут отправлены на указанный аккаунт."
                        )
                        
                        safe_edit_message_text(
                            c.telegram.bot,
                            chat_id=chat_id,
                            message_id=message_id,
                            text=message_text,
                            reply_markup=keyboard,
                            parse_mode="HTML"
                        )
                        return
                
                safe_edit_message_text(
                    c.telegram.bot,
                    chat_id=chat_id,
                    message_id=message_id,
                    text="❌ Не удалось получить информацию о цене для тестового заказа.",
                    parse_mode="HTML"
                )
                
            except Exception as e:
                logger.error(f"Ошибка при получении цены тестового заказа: {e}")
                safe_edit_message_text(
                    c.telegram.bot,
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"❌ Ошибка при получении цены: {str(e)}",
                    parse_mode="HTML"
                )
        
        asyncio.run_coroutine_threadsafe(get_test_price(), payment_processor.loop)

    elif data == "confirm_test_order":
        safe_edit_message_text(
            c.telegram.bot,
            chat_id=chat_id,
            message_id=message_id,
            text="<b>🧪 Отправка тестового заказа...</b>",
            parse_mode="HTML"
        )

        async def run_test():
            try:
                test_username = "veemp"
                test_quantity = 50

                logger.info(f"🧪 Начало теста для пользователя: {test_username}, количество: {test_quantity}")

                tx_hash, ref_id, quantity, spent_ton, error = await main_async(test_username, test_quantity)

                if error:
                    if "No Telegram users found" in error:
                        result_text = (
                            "<b>✅ Тест пройден успешно!</b>\n\n"
                            "• Поиск пользователя в Fragment: ✅\n"
                            "• Получение цены: ✅\n"
                            "• Проверка API: ✅\n\n"
                            "⚠️ <b>Примечание:</b> Пользователь не найден в Fragment.\n"
                            "Система работает корректно."
                        )
                    else:
                        result_text = f"<b>❌ Тест не пройден!</b>\n\nОшибка: {error}"
                else:
                    result_text = (
                        "<b>✅ Тест пройден успешно!</b>\n\n"
                        "• Поиск пользователя в Fragment: ✅\n"
                        "• Получение цены: ✅\n" 
                        "• Проверка API: ✅\n"
                        f"• Требуемая сумма: {spent_ton:.4f} TON\n\n"
                        f"<b>Хеш транзакции:</b> {tx_hash}\n"
                        f"<b>Ref ID:</b> {ref_id}\n"
                        f"<b>Ссылка:</b> https://tonviewer.com/transaction/{tx_hash}"
                    )

                keyboard = InlineKeyboardMarkup(row_width=1)
                keyboard.add(InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main"))

                safe_edit_message_text(
                    c.telegram.bot,
                    chat_id=chat_id,
                    message_id=message_id,
                    text=result_text,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )

            except Exception as e:
                logger.error(f"Ошибка при выполнении теста: {e}")
                safe_edit_message_text(
                    c.telegram.bot,
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"<b>❌ Ошибка теста!</b>\n\n{str(e)}",
                    parse_mode="HTML"
                )

        asyncio.run_coroutine_threadsafe(run_test(), payment_processor.loop)

    elif data == "cancel_test_order":
        stars_config(c, call.message, edit_message_id=message_id)

def init(c: Cardinal, *args):
    global RUNNING
    
    # Лицензия полностью отключена
    RUNNING = True
    logger.info("🚀 Плагин AutoStars инициализирован (лицензия отключена).")
    
    # Можно получить реальный TG ID если нужно для уведомлений
    tg_id = get_tg_id_from_cache()
    if tg_id and hasattr(c, 'telegram') and hasattr(c.telegram, 'bot'):
        try:
            c.telegram.bot.send_message(
                tg_id,
                "✅ Плагин AutoStars запущен в режиме без проверки лицензии."
            )
        except:
            pass
    
    init_commands(c)

def main():
    print("AutoStars 2.0 запущен.")

def shutdown():
    payment_processor.loop.call_soon_threadsafe(payment_processor.loop.stop)
    payment_processor.thread.join()

atexit.register(shutdown)

BIND_TO_PRE_INIT = [init_commands]
BIND_TO_NEW_MESSAGE = [handle_new_message_text]
BIND_TO_NEW_ORDER = [handle_new_order_stars]
BIND_TO_POST_INIT = [init]
BIND_TO_DELETE = []

if __name__ == "__main__":
    main()