from __future__ import annotations
from typing import TYPE_CHECKING, Dict, List, Tuple, Optional, Any
if TYPE_CHECKING:
    from cardinal import Cardinal

import re
from FunPayAPI.updater.events import *
from FunPayAPI import Account, enums
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from telebot import types

import threading
import logging
import requests
import json
import os
import queue
import time
import uuid
import sqlite3
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

notification_executor = ThreadPoolExecutor(max_workers=10, thread_name_prefix='SMM_Notifier')
logger = logging.getLogger('FPC.autosmm')
LOGGER_PREFIX = '[AUTO autosmm]'
NAME = 'AutoSMM'
VERSION = '2.1'
DESCRIPTION = 'Плагин для авто-накрутки.'
CREDITS = '@exfador | https://t.me/FunPay_plugin'
UUID = '1b39b532-637f-40e9-825e-cf45c0f5d656'
SETTINGS_PAGE = False
UPDATE = '\nУлучшения:\n - Добавлена многопоточная обработка заказов\n - Исправлена проблема с одновременной обработкой нескольких заказов\n'
RUNNING = True

chat_id = None
orders_info = {}
IS_ACTIVATED = True

processed_orders = set()
pending_orders = {}
user_data_store = {}

CONFIG_PATH = os.path.join('storage', 'exfador', 'smm', 'config.json')
ORDERS_PATH = os.path.join('storage', 'exfador', 'smm', 'orders.json')
ORDERS_DATA_PATH = os.path.join('storage', 'exfador', 'smm', 'orders_data.json')
DB_PATH = os.path.join('storage', 'exfador', 'smm', 'database.db')

_plugin_instance = None
_bot = None


def send_notification_to_user(bot, user_id: int, message: str, reply_markup, retry_count: int = 3) -> bool:
    for i in range(retry_count):
        try:
            bot.send_message(user_id, message, parse_mode='HTML', reply_markup=reply_markup)
            logger.debug(f"{LOGGER_PREFIX} Уведомление успешно отправлено пользователю {user_id}")
            return True
        except Exception as e:
            if i < retry_count - 1:
                time.sleep(0.5 * (i + 1))
                logger.debug(f"{LOGGER_PREFIX} Повторная попытка {i + 1} для пользователя {user_id}")
            else:
                logger.warning(f"{LOGGER_PREFIX} Не удалось отправить уведомление пользователю {user_id} после {retry_count} попыток: {e}")
                return False
    return False


def send_notification_to_all_users(bot, message: str, reply_markup) -> int:
    auth_users_path = os.path.join('storage', 'cache', 'tg_authorized_users.json')
    success_count = 0
    
    if not os.path.exists(auth_users_path):
        logger.warning(f"{LOGGER_PREFIX} Файл авторизованных пользователей не найден: {auth_users_path}")
        return 0
        
    try:
        with open(auth_users_path, 'r', encoding='utf-8') as f:
            auth_users = json.load(f)
    except Exception:
        auth_users = []

    if not auth_users:
        logger.warning(f"{LOGGER_PREFIX} Список авторизованных пользователей пуст")
        return 0
        
    logger.info(f"{LOGGER_PREFIX} Начата асинхронная отправка уведомлений {len(auth_users)} пользователям...")
    futures = []
    
    for user_id in auth_users:
        future = notification_executor.submit(send_notification_to_user, bot, user_id, message, reply_markup)
        futures.append((future, user_id))
        
    for future, user_id in futures:
        try:
            if future.result(timeout=30):
                success_count += 1
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка при обработке результата для пользователя {user_id}: {e}")
            
    try:
        logger.info(f"{LOGGER_PREFIX} Уведомления успешно отправлены {success_count} из {len(auth_users)} пользователей")
    except Exception as e:
        logger.error(f"{LOGGER_PREFIX} Ошибка при отправке уведомлений: {e}")
        
    return success_count


def send_notification_async(bot, message: str, reply_markup=None):
    def _send():
        try:
            send_notification_to_all_users(bot, message, reply_markup)
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка в асинхронной отправке уведомлений: {e}")
    notification_executor.submit(_send)


class SMMUtils:
    _orders_data_cache = None
    _cache_timestamp = 0
    _cache_ttl = 5

    @staticmethod
    def get_connection():
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def init_db():
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        with SMMUtils.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('PRAGMA journal_mode=WAL;')
            cursor.execute('PRAGMA synchronous=NORMAL;')
            cursor.execute('''
    CREATE TABLE IF NOT EXISTS orders_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT,
        order_id INTEGER,
        summa REAL,
        service_name TEXT
    )
      ''')
            cursor.execute('''
    CREATE TABLE IF NOT EXISTS orders_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        order_id INTEGER,
        id_zakaz INTEGER,
        status TEXT,
        api_url TEXT,
        api_token TEXT,
        clean_profit REAL,
        service_id INTEGER,
        original_quantity INTEGER,
        link TEXT,
        partial_notification_sent INTEGER DEFAULT 0,
        auto_topup_done INTEGER DEFAULT 0,
        last_remains TEXT
    )
      ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_order_id ON orders_data (order_id);')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_id_zakaz ON orders_data (id_zakaz);')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_status ON orders_data (status);')
            conn.commit()
        SMMUtils.migrate_from_json()

    @staticmethod
    def migrate_from_json():
        if os.path.exists(ORDERS_DATA_PATH):
            try:
                with open(ORDERS_DATA_PATH, 'r', encoding='utf-8') as f:
                    try:
                        orders_data = json.load(f)
                    except Exception:
                        orders_data = []
                        
                if orders_data:
                    logger.info(f"{LOGGER_PREFIX} Миграция {len(orders_data)} заказов из JSON в SQLite...")
                    with SMMUtils.get_connection() as conn:
                        cursor = conn.cursor()
                        for order in orders_data:
                            cursor.execute('SELECT 1 FROM orders_data WHERE order_id = ?', (order.get('order_id'),))
                            if not cursor.fetchone():
                                cursor.execute('''
        INSERT INTO orders_data (
            chat_id, order_id, id_zakaz, status, api_url, api_token, 
            clean_profit, service_id, original_quantity, link, 
            partial_notification_sent, auto_topup_done, last_remains
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
          ''', (
                                    order.get('chat_id'), order.get('order_id'), order.get('id_zakaz'),
                                    order.get('status'), order.get('api_url'), order.get('api_token'),
                                    order.get('clean_profit'), order.get('service_id'), order.get('original_quantity'),
                                    order.get('link'), int(order.get('partial_notification_sent', False)),
                                    int(order.get('auto_topup_done', False)), order.get('last_remains')
                                ))
                        conn.commit()
                    
                    try:
                        os.rename(ORDERS_DATA_PATH, ORDERS_DATA_PATH + '.bak')
                        logger.info(f"{LOGGER_PREFIX} Файл orders_data.json переименован в .bak")
                    except Exception as e:
                        logger.warning(f"{LOGGER_PREFIX} Не удалось переименовать orders_data.json: {e}")
            except Exception as e:
                logger.error(f"{LOGGER_PREFIX} Ошибка миграции orders_data: {e}")

        if os.path.exists(ORDERS_PATH):
            try:
                with open(ORDERS_PATH, 'r', encoding='utf-8') as f:
                    try:
                        orders_log = json.load(f)
                    except Exception:
                        orders_log = []
                
                if orders_log:
                    logger.info(f"{LOGGER_PREFIX} Миграция лога заказов из JSON в SQLite...")
                    with SMMUtils.get_connection() as conn:
                        cursor = conn.cursor()
                        for log_entry in orders_log:
                            cursor.execute('SELECT 1 FROM orders_log WHERE order_id = ? AND date = ?', (log_entry.get('order_id'), log_entry.get('date')))
                            if not cursor.fetchone():
                                cursor.execute('INSERT INTO orders_log (date, order_id, summa, service_name) VALUES (?, ?, ?, ?)', (
                                    log_entry.get('date'), log_entry.get('order_id'), log_entry.get('summa'), log_entry.get('service_name')
                                ))
                        conn.commit()
                        
                    try:
                        os.rename(ORDERS_PATH, ORDERS_PATH + '.bak')
                    except Exception:
                        pass
            except Exception as e:
                logger.error(f"{LOGGER_PREFIX} Ошибка миграции orders log: {e}")


    @staticmethod
    def is_valid_link(message: str) -> bool:
        logger.info(f"{LOGGER_PREFIX} Проверка валидности ссылки: {message}")
        if not message:
            return False
        if re.match('^https?://\\S+', message):
            return True
        if re.match('^(?:https?://)?t\\.me/\\S+', message):
            return True
        return False

    @staticmethod
    def save_order_info(order_id: int, order_summa: float, service_name: str) -> None:
        try:
            with SMMUtils.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('INSERT INTO orders_log (date, order_id, summa, service_name) VALUES (?, ?, ?, ?)', (
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'), order_id, order_summa, service_name
                ))
                conn.commit()
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка записи в лог заказов: {e}")

    @staticmethod
    def save_order_data(chat_id: int, order_id: int, id_zakaz: int, status: str, api_url: str, api_token: str, clean_profit: float, service_id: int, original_quantity: int, link: str, partial_notification_sent: bool) -> None:
        try:
            with SMMUtils.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
        INSERT INTO orders_data (
      chat_id, order_id, id_zakaz, status, api_url, api_token, 
      clean_profit, service_id, original_quantity, link, 
      partial_notification_sent, auto_topup_done, last_remains
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (chat_id, order_id, id_zakaz, status, api_url, api_token, clean_profit, service_id, original_quantity, link, 1 if partial_notification_sent else 0, 0, None))
                conn.commit()
            SMMUtils._orders_data_cache = None
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка сохранения данных заказа: {e}")

    @staticmethod
    def update_order_status(order_id: int, new_status: str) -> None:
        try:
            with SMMUtils.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('UPDATE orders_data SET status = ? WHERE order_id = ?', (new_status, order_id))
                conn.commit()
            SMMUtils._orders_data_cache = None
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка обновления статуса заказа {order_id}: {e}")

    @staticmethod
    def update_order_field(order_id: int, field: str, value: Any) -> None:
        try:
            allowed_fields = ['last_remains', 'auto_topup_done', 'partial_notification_sent', 'status', 'clean_profit']
            if field not in allowed_fields:
                logger.warning(f"{LOGGER_PREFIX} Попытка обновления недопустимого поля {field}")
                return
            if isinstance(value, bool):
                value = 1 if value else 0
                
            with SMMUtils.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(f'UPDATE orders_data SET {field} = ? WHERE order_id = ?', (value, order_id))
                conn.commit()
            SMMUtils._orders_data_cache = None
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка обновления поля {field} для заказа {order_id}: {e}")

    @staticmethod
    def update_order_data_profit(order_id: int, new_profit: float) -> None:
        try:
            with SMMUtils.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('UPDATE orders_data SET clean_profit = ? WHERE order_id = ?', (new_profit, order_id))
                conn.commit()
            SMMUtils._orders_data_cache = None
            logger.info(f"{LOGGER_PREFIX} Обновлена прибыль для заказа {order_id}: {new_profit}₽")
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка при обновлении прибыли для заказа {order_id}: {e}")

    @staticmethod
    def update_partial_notification_sent(order_id: int, sent: bool) -> None:
        try:
            with SMMUtils.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('UPDATE orders_data SET partial_notification_sent = ? WHERE order_id = ?', (1 if sent else 0, order_id))
                conn.commit()
            SMMUtils._orders_data_cache = None
            logger.info(f"{LOGGER_PREFIX} Обновлен флаг уведомления для заказа {order_id}: {sent}")
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка при обновлении флага уведомления для заказа {order_id}: {e}")

    @staticmethod
    def load_orders_data() -> List[Dict]:
        current_time = time.time()
        if SMMUtils._orders_data_cache is not None and (current_time - SMMUtils._cache_timestamp < SMMUtils._cache_ttl):
            return SMMUtils._orders_data_cache.copy()
            
        try:
            with SMMUtils.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM orders_data')
                rows = cursor.fetchall()
                
                result = []
                for row in rows:
                    result.append({
                        'chat_id': row['chat_id'],
                        'order_id': row['order_id'],
                        'id_zakaz': row['id_zakaz'],
                        'status': row['status'],
                        'api_url': row['api_url'],
                        'api_token': row['api_token'],
                        'clean_profit': row['clean_profit'],
                        'service_id': row['service_id'],
                        'original_quantity': row['original_quantity'],
                        'link': row['link'],
                        'partial_notification_sent': bool(row['partial_notification_sent']),
                        'auto_topup_done': bool(row['auto_topup_done']),
                        'last_remains': row['last_remains']
                    })
                    
            SMMUtils._orders_data_cache = result
            SMMUtils._cache_timestamp = current_time
            return result
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка загрузки заказов из БД: {e}")
            return []


class ConfigManager:
    @staticmethod
    def load_config() -> Dict:
        logger.info(f"{LOGGER_PREFIX} Загрузка конфигурации из {CONFIG_PATH}...")
        
        default_templates = {
            'smm_new_order_fp': '\n❤️ Спасибо за ваш заказ! Накрутка начнется автоматически.\n    ∟ 🛍️ Услуга: {order_description}\n    ∟ 🔢 Количество: {total_amount}\n\n📌 Внимание:\n    ∟ ℹ️ Для активации отправьте ссылку в формате: https://url_service\n    ∟ ❗ Без корректной ссылки выполнение заказа невозможно\n',
            'order_link_confirmation_fp': '\n🎉 Заказ успешно создан!\n    ∟ ✅ Номер заказа: {order_id_smm}\n    ∟ 🔗 Ссылка для отслеживания: {link}\n\n📋 Управление заказом:\n    ∟ 📍 Проверить статус: чек {order_id_smm}\n    ∟ 🔄 Запросить рефилл: рефилл {order_id_smm}\n',
            'order_status_check_fp': '\n📊 Детали заказа {order_id}:\n    ∟ 📌 Статус: {status_emoji} {status}\n    ∟ 💰 Стоимость: {charge}₽\n    ∟ ⏳ Последнее обновление: {last_update}',
            'order_refill_success_fp': '\n🔄 Рефилл активирован!\n    ∟ ✅ Номер заказа: {order_id}\n    ∟ ⏳ Начата повторная накрутка\n    ∟ 📌 Статус можно проверить через 15-30 минут',
            'error_invalid_link_fp': '\n❌ Ошибка: неверный формат ссылки\n    ∟ Пожалуйста, отправьте ссылку в формате http:// или https://\n    ∟ Пример: https://example.com/...',
            'order_complete_fp': '\n✅ Накрутка по заказу {order_id} завершена!\n    ∟ 🔍 Просьба проверить результат\n    ∟ 👍 Подтвердите получение заказа: {order_url}\n    ∟ 🌟 Спасибо за использование нашего сервиса!\n\nВыполнил провайдер: neversmm.ru\n',
            'error_id_not_found_fp': '\n❌ Ошибка: заказ с ID {order_id} не найден\n    ∟ 🔍 Пожалуйста, проверьте правильность ID\n    ∟ 📝 Формат команды: чек [ID заказа]',
            'automatic_refund_message_fp': '❌ Извините, не можем осуществить заказ. Средства автоматически возвращены на ваш баланс. Приносим извинения за неудобства.',
            'manual_refund_message_fp': '❌ Извините, не можем осуществить заказ. Средства будут возвращены в течение 24 часов. Приносим извинения за неудобства.'
        }
        
        legacy_keys = [
            'smm_new_order_tg_notification', 'regular_new_order_tg_notification', 
            'order_link_tg_details', 'error_link_not_buyer_fp', 
            'error_order_already_processed_fp', 'error_min_amount_fp', 
            'error_api_link_format_fp'
        ]

        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                    config_data = json.load(f)
                
                needs_save = False
                
                if 'api_services' not in config_data:
                    if 'key' in config_data and 'api_url' in config_data:
                        config_data['api_services'] = [{
                            'name': 'Основной',
                            'url': config_data['api_url'],
                            'token': config_data['key']
                        }]
                        needs_save = True
                        
                if 'key' in config_data:
                    del config_data['key']
                    needs_save = True
                if 'api_url' in config_data:
                    del config_data['api_url']
                    needs_save = True
                    
                if 'refund_type' not in config_data:
                    config_data['refund_type'] = 'automatic'
                    needs_save = True
                    
                if 'customer_messages' not in config_data:
                    config_data['customer_messages'] = {}
                    needs_save = True
                    
                for old_key in legacy_keys:
                    if old_key in config_data.get('customer_messages', {}):
                        logger.info(f"{LOGGER_PREFIX} Удаление шаблона: {old_key}")
                        del config_data['customer_messages'][old_key]
                        needs_save = True
                        
                for k in ['refund_message_fp', 'manual_refund_request_tg']:
                    if k in config_data.get('customer_messages', {}):
                        logger.info(f"{LOGGER_PREFIX} Удаление шаблона возврата: {k}")
                        del config_data['customer_messages'][k]
                        needs_save = True
                        
                if 'customer_messages' in config_data:
                    for k, v in config_data['customer_messages'].items():
                        if '\\n' in v and '\n' not in v:
                            config_data['customer_messages'][k] = v.replace('\\n', '\n')
                            needs_save = True
                            
                for k, v in default_templates.items():
                    if k not in config_data['customer_messages']:
                        config_data['customer_messages'][k] = v
                        needs_save = True
                        
                if needs_save:
                    ConfigManager.save_config(config_data)
                    
                logger.info(f"{LOGGER_PREFIX} Конфигурация успешно загружена.")
                return config_data
            except Exception as e:
                logger.error(f"{LOGGER_PREFIX} Ошибка при загрузке конфигурации: {e}")
                logger.info(f"{LOGGER_PREFIX} Создание нового файла с настройками по умолчанию.")

        logger.info(f"{LOGGER_PREFIX} Конфигурационный файл не найден. Создание нового файла с настройками по умолчанию.")
        
        default_config = {
            'api_services': [{
                'name': 'Основной',
                'url': 'https://neversmm.ru/api/v2',
                'token': 'rRwtJLdQ8XssECEeK562o0rlHOyWwMTZy5TN1T4Aci1D2yMIkrMcXwrydgCB'
            }],
            'customer_messages': default_templates.copy(),
            'refund_type': 'automatic'
        }
        
        ConfigManager.save_config(default_config)
        logger.info(f"{LOGGER_PREFIX} Создан новый конфигурационный файл с настройками по умолчанию.")
        return default_config

    @staticmethod
    def save_config(config: Dict) -> None:
        logger.info(f"{LOGGER_PREFIX} Сохранение конфигурации...")
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        try:
            with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=4)
            logger.info(f"{LOGGER_PREFIX} Конфигурация успешно сохранена в {CONFIG_PATH}")
        except Exception:
            pass

    @staticmethod
    def get_tg_id_by_description(description: str, order_amount: int, lot_mapping: Dict) -> Optional[Tuple[int, int]]:
        logger.info(f"{LOGGER_PREFIX} Поиск ID услуги по описанию: {description}")
        for lot_id, lot_data in lot_mapping.items():
            if re.search(re.escape(lot_data['name']), description, re.IGNORECASE):
                logger.info(f"{LOGGER_PREFIX} Найден ID услуги: {lot_data['service_id']}, количество: {lot_data['quantity'] * order_amount}")
                return (lot_data['service_id'], lot_data['quantity'] * order_amount)
        logger.warning(f"{LOGGER_PREFIX} ID услуги для описания '{description}' не найден.")
        return None


class OrderProcessor:
    def __init__(self, cardinal: Cardinal, config: Dict):
        self.cardinal = cardinal
        self.config = config
        self.bot = cardinal.telegram.bot
        self.account = cardinal.account
        self.running = False
        self.lot_mapping = config.get('lot_mapping', {})
        self.chat_id = config.get('chat_id', None)
        self.orders_info = {}
        self.messages = config.get('customer_messages', {})
        self._update_keys_from_config()
        self.processing_lock = threading.Lock()
        self._is_canceled_order = {}

    def _update_keys_from_config(self):
        services = self.config.get('api_services', [])
        if services:
            self.key = None
        else:
            self.key = None

    def _get_full_order_description(self, order_id: int) -> str:
        try:
            order = self.account.get_order(order_id)
            if hasattr(order, 'short_description') and order.short_description:
                return order.short_description
            if hasattr(order, 'title') and order.title:
                return order.title
            if hasattr(order, 'description'):
                return order.description
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка при получении описания заказа {order_id}: {e}")
        return 'Описание недоступно'

    def check_order_status(self, order_id: int, chat_id: int, link: str, order_id_funpay: int, attempt: int = 0) -> None:
        if attempt >= 100:
            logger.warning(f"{LOGGER_PREFIX} Достигнуто максимальное количество проверок ({attempt}) для заказа {order_id}")
            return

        orders_data = SMMUtils.load_orders_data()
        current_order = None
        for data in orders_data:
            if str(data.get('id_zakaz', '')) == str(order_id):
                current_order = data
                logger.info(f"{LOGGER_PREFIX} Найден заказ для автопроверки: {order_id}")
                break
                
        if current_order:
            if current_order.get('status') in ('Completed', 'Canceled', 'Failed'):
                logger.info(f"{LOGGER_PREFIX} Заказ {order_id} имеет финальный статус {current_order.get('status')}, прекращаем проверку")
                return
                
        if current_order and 'api_url' in current_order and current_order['api_url'] and 'api_token' in current_order and current_order['api_token']:
            api_url = current_order['api_url']
            api_token = current_order['api_token']
            logger.info(f"{LOGGER_PREFIX} Используем сохраненный API для проверки: {api_url}")
        else:
            api_services = self.config.get('api_services', [])
            api_url = 'https://twiboost.com/api/v2'
            api_token = getattr(self, 'key', '')
            if api_services:
                service = api_services[0]
                api_url = service.get('url', '')
                api_token = service.get('token', '')
            logger.info(f"{LOGGER_PREFIX} Используем API по умолчанию для проверки: {api_url}")

        url = f"{api_url}?action=status&order={order_id}&key={api_token}"
        logger.info(f"{LOGGER_PREFIX} Проверка статуса заказа {order_id}...")
        
        try:
            response = requests.get(url, timeout=10)
            logger.info(f"{LOGGER_PREFIX} Ответ от API ({response.status_code}): {response.text[:200]}")
            
            if response.status_code == 200:
                try:
                    data = response.json()
                except Exception as e:
                    logger.error(f"{LOGGER_PREFIX} Некорректный JSON в ответе статуса заказа {order_id}: {e}")
                    current_attempt = getattr(self, f"_check_attempts_{order_id}", 0)
                    setattr(self, f"_check_attempts_{order_id}", current_attempt + 1)
                    wait_time = 300
                    logger.info(f"{LOGGER_PREFIX} Повторная проверка заказа #{order_id} через {wait_time} сек. (JSON decode)")
                    threading.Timer(wait_time, self.check_order_status, args=(order_id, chat_id, link, order_id_funpay, attempt + 1)).start()
                    return

                logger.info(f"{LOGGER_PREFIX} Полный ответ API при проверке статуса: {data}")
                
                status = data.get('status', 'Unknown')
                remains = data.get('remains', 0)
                remains_val = int(remains) if remains is not None else 0
                
                logger.info(f"{LOGGER_PREFIX} Статус заказа {order_id}: {status}, Остаток: {remains_val}")
                
                if 'charge' in data:
                    try:
                        charge_val = float(data.get('charge', '0'))
                        logger.info(f"{LOGGER_PREFIX} Получена актуальная цена заказа: {charge_val}")
                        
                        if current_order and current_order.get('clean_profit', 0) != 0 and charge_val > 0:
                            summa = 0
                            try:
                                with open(ORDERS_PATH, 'r', encoding='utf-8') as f:
                                    try:
                                        log_data = json.load(f)
                                    except json.JSONDecodeError:
                                        log_data = []
                                        
                                for entry in log_data:
                                    if entry.get('order_id') == order_id_funpay:
                                        summa = entry.get('summa', 0)
                                        break
                            except Exception:
                                pass
                                
                            if summa == 0:
                                try:
                                    order_fp = self.account.get_order(order_id_funpay)
                                    summa = order_fp.sum
                                except Exception as e:
                                    logger.error(f"{LOGGER_PREFIX} Ошибка получения цены заказа FunPay: {e}")
                                    
                            if summa > 0:
                                new_profit = summa - charge_val
                                logger.info(f"{LOGGER_PREFIX} Обновление данных о прибыли: было {current_order.get('clean_profit', 0)}, стало {new_profit}")
                                SMMUtils.update_order_data_profit(order_id_funpay, new_profit)
                                
                    except (ValueError, TypeError) as e:
                        logger.error(f"{LOGGER_PREFIX} Ошибка при парсинге цены заказа при проверке статуса: {e}. Значение: {data.get('charge')}")
                        
                if status.lower() in ('error', 'canceled', 'failed'):
                    logger.error(f"{LOGGER_PREFIX} Заказ {order_id} имеет статус ошибки: {status}")
                    self._is_canceled_order[order_id_funpay] = True
                    SMMUtils.update_order_status(order_id_funpay, status.capitalize())
                    
                    try:
                        order_fp = self.account.get_order(order_id_funpay)
                        buyer = order_fp.buyer_username
                        desc = self._get_full_order_description(order_id_funpay)
                        summa = order_fp.sum
                        error_msg = data.get('error', '')
                        
                        if not error_msg:
                            error_msg = f"API вернуло статус: {status}"
                        else:
                            error_msg = f"API вернуло статус: {status}, ошибка: {error_msg}"
                            
                        logger.info(f"{LOGGER_PREFIX} Уведомляем об ошибке для заказа {order_id_funpay} с сообщением: {error_msg}")
                        self.process_refund(order_id_funpay, error_msg)
                        
                    except Exception as e:
                        logger.error(f"{LOGGER_PREFIX} Не удалось отправить уведомление об ошибке: {e}")
                        self.process_refund(order_id_funpay, f"Критическая ошибка получения данных заказа: {str(e)}")
                    return
                    
                elif status.lower() == 'partial':
                    try:
                        order_fp = self.account.get_order(order_id_funpay)
                        if getattr(order_fp, 'status', None) == enums.OrderStatuses.REFUNDED:
                            logger.info(f"{LOGGER_PREFIX} FunPay заказ {order_id_funpay} REFUNDED. Пропускаем докрутку и сообщения клиенту.")
                            return
                    except Exception as e:
                        logger.error(f"{LOGGER_PREFIX} Ошибка проверки статуса FunPay заказа {order_id_funpay}: {e}")
                        
                    logger.info(f"{LOGGER_PREFIX} Заказ {order_id} выполнен частично")
                    
                    notified = current_order.get('partial_notification_sent', False) if current_order else False
                    SMMUtils.update_order_status(order_id_funpay, 'Partial')
                    
                    if not notified:
                        orig_q = current_order.get('original_quantity', 0) if current_order else 0
                        missing = remains_val if remains_val > 0 else 0
                        
                        text = f"\n⚠️ Требуется докрутка заказа\n\n🆔 ID в SMM: <b>{order_id}</b>\n🆔 ID в FunPay: <b>{order_id_funpay}</b>\n📉 Недостающее количество: <b>{missing}</b>\n"
                        markup = InlineKeyboardMarkup()
                        markup.add(InlineKeyboardButton(text='Перейти к заказу', url=f"https://funpay.com/orders/{order_id_funpay}/"))
                        send_notification_async(self.bot, text, reply_markup=markup)
                        SMMUtils.update_partial_notification_sent(order_id_funpay, True)
                        logger.info(f"{LOGGER_PREFIX} Отправлено уведомление администраторам о необходимости докрутки для заказа {order_id}")
                    else:
                        logger.info(f"{LOGGER_PREFIX} Уведомление о частичном выполнении заказа {order_id} уже было отправлено ранее")
                        
                elif status.lower() == 'completed' and remains_val == 0:
                    logger.info(f"{LOGGER_PREFIX} Заказ {order_id} завершен.")
                    order_url = f"https://funpay.com/orders/{order_id_funpay}/"
                    msg_text = self.format_template('order_complete_fp', order_id=order_id, order_url=order_url)
                    self.cardinal.send_message(chat_id, msg_text)
                    SMMUtils.update_order_status(order_id_funpay, 'Completed')
                    SMMUtils.update_partial_notification_sent(order_id_funpay, False)
                    
                    buyer = "Неизвестно"
                    desc = "Неизвестно"
                    profit = 0
                    try:
                        order_fp = self.account.get_order(order_id_funpay)
                        buyer = order_fp.buyer_username
                        if hasattr(order_fp, 'short_description') and order_fp.short_description:
                            desc = order_fp.short_description
                        elif hasattr(order_fp, 'title') and order_fp.title:
                            desc = order_fp.title
                        else:
                            desc = order_fp.description
                    except Exception:
                        pass
                        
                    if current_order:
                        profit = current_order.get('clean_profit', 0)
                        
                    charge_val = charge_val if 'charge_val' in locals() else 0
                    
                    tg_msg = f"\n✅ <b>Заказ выполнен!</b>\n\n🆔 ID в SMM: <b>{order_id}</b>\n👤 Покупатель: <b>{buyer}</b>\n🛍️ Лот: <b>{desc}</b>\n💰 Цена в API: <b>{charge_val}₽</b>\n💎 Чистая прибыль: <b>{profit:.2f}₽</b>\n\n🎉 Накрутка успешно завершена!\n"
                    markup = InlineKeyboardMarkup()
                    markup.add(InlineKeyboardButton(text='Перейти к заказу', url=order_url))
                    
                    try:
                        send_notification_async(self.bot, tg_msg, reply_markup=markup)
                    except Exception as e:
                        logger.error(f"{LOGGER_PREFIX} Ошибка при отправке уведомления о завершении заказа: {e}")
                        
                    if order_id_funpay in processed_orders:
                        processed_orders.remove(order_id_funpay)
                        logger.info(f"{LOGGER_PREFIX} Заказ {order_id_funpay} удален из списка обработанных.")
                    return

            else:
                logger.error(f"{LOGGER_PREFIX} Ошибка при проверке статуса заказа: {response.status_code}, {response.text}")
                
            current_attempt = getattr(self, f"_check_attempts_{order_id}", 0)
            if current_attempt >= 5:
                logger.error(f"{LOGGER_PREFIX} Максимальное количество попыток проверки статуса достигнуто для заказа {order_id}")
                
                tg_msg = f"\n❌ <b>ОШИБКА ПРОВЕРКИ СТАТУСА</b>\n\n🆔 ID заказа в SMM: <b>{order_id}</b>\n🆔 ID заказа в FunPay: <b>{order_id_funpay}</b>\n\n❌ Не удалось проверить статус после нескольких попыток\n❌ Код ошибки: <b>{response.status_code}</b>\n\n⚠️ Требуется ручная проверка заказа\n"
                markup = InlineKeyboardMarkup()
                markup.add(InlineKeyboardButton(text='Перейти к заказу', url=f"https://funpay.com/orders/{order_id_funpay}/"))
                send_notification_async(self.bot, tg_msg, reply_markup=markup)
                return
                
            setattr(self, f"_check_attempts_{order_id}", current_attempt + 1)
            
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка при проверке статуса заказа: {e}")
            current_attempt = getattr(self, f"_check_attempts_{order_id}", 0)
            if current_attempt >= 5:
                logger.error(f"{LOGGER_PREFIX} Максимальное количество попыток проверки статуса достигнуто для заказа {order_id}")
                tg_msg = f"\n❌ <b>КРИТИЧЕСКАЯ ОШИБКА ПРОВЕРКИ</b>\n\n🆔 ID заказа в SMM: <b>{order_id}</b>\n🆔 ID заказа в FunPay: <b>{order_id_funpay}</b>\n\n❌ Ошибка: <b>{str(e)}</b>\n\n⚠️ ТРЕБУЕТСЯ СРОЧНАЯ ПРОВЕРКА ЗАКАЗА!\n"
                markup = InlineKeyboardMarkup()
                markup.add(InlineKeyboardButton(text='Перейти к заказу', url=f"https://funpay.com/orders/{order_id_funpay}/"))
                try:
                    send_notification_async(self.bot, tg_msg, reply_markup=markup)
                except:
                    pass
                return
            setattr(self, f"_check_attempts_{order_id}", current_attempt + 1)

        wait_time = 300
        logger.info(f"{LOGGER_PREFIX} Повторная проверка заказа #{order_id} через {wait_time} сек.")
        threading.Timer(wait_time, self.check_order_status, args=(order_id, chat_id, link, order_id_funpay, attempt + 1)).start()

    def start_order_checking(self) -> None:
        orders_data = SMMUtils.load_orders_data()
        final_statuses = ['Completed', 'Canceled', 'Failed']
        pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix='SMM_Checker')
        
        for idx, order in enumerate(orders_data):
            if order.get('status') not in final_statuses:
                wait_t = min(idx * 0.5, 10)
                logger.info(f"{LOGGER_PREFIX} Запуск проверки заказа {order['id_zakaz']} через {wait_t} сек.")
                def delayed_check(order_data, d):
                    time.sleep(d)
                    try:
                        self.check_order_status(order_data.get('id_zakaz'), order_data.get('chat_id'), '', order_data.get('order_id'), 0)
                    except Exception as e:
                        logger.error(f"{LOGGER_PREFIX} Ошибка в задаче проверки заказа: {e}")
                pool.submit(delayed_check, order, wait_t)
        pool.shutdown(wait=False)

    def new_order_handler(self, event: NewOrderEvent) -> None:
        order = event.order
        amount = order.amount
        description = order.description
        order_id = order.id
        price = order.price
        
        logger.info(f"{LOGGER_PREFIX} Новый заказ {order_id}: {description}, количество: {amount}, сумма: {price}₽")
        
        try:
            fp_order = self.account.get_order(order_id)
            chat_id = fp_order.chat_id
            buyer = fp_order.buyer_username
            buyer_id = fp_order.buyer_id
            full_desc = fp_order.full_description if hasattr(fp_order, 'full_description') else None
            
            desc_to_check = full_desc if full_desc else description
            
            is_smm = re.search(r'\bsmm\s*:\s*on\b', desc_to_check, re.IGNORECASE)
            
            if is_smm:
                self._handle_smm_order(order_id, buyer_id, chat_id, buyer, price, desc_to_check, description)
                return
                
            lot_match = ConfigManager.get_tg_id_by_description(description, amount, self.lot_mapping)
            if not lot_match:
                logger.info(f"{LOGGER_PREFIX} Заказ {order_id} не найден в списке лотов. Пропуск.")
                return
                
            self._handle_regular_order(order_id, buyer_id, chat_id, buyer, price, description, lot_match)
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка при обработке нового заказа {order_id}: {e}")

    def _handle_smm_order(self, order_id, buyer_id, chat_id_order, username, order_summa, description_to_check, order_description):
        try:
            order_fp = self.account.get_order(order_id)
            amount_str = order_fp.amount
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка получения данных заказа: {e}")
            return
            
        m_id = re.search(r'\bid\s*:\s*(\d+)', description_to_check)
        if not m_id:
            logger.warning(f"{LOGGER_PREFIX} Не найден ID услуги в описании заказа")
            return
        service_id = int(m_id.group(1))
        
        m_api = re.search(r'\bname\s*:\s*(\w+)', description_to_check)
        if not m_api:
            m_api = re.search(r'\bapi\s*:\s*(\w+)', description_to_check)
        if not m_api:
            logger.warning(f"{LOGGER_PREFIX} Не найдено имя API в описании заказа")
            return
        api_name = m_api.group(1).strip()
        
        m_am = re.search(r'\bam\s*:\s*(\d+)', description_to_check)
        if not m_am:
            logger.warning(f"{LOGGER_PREFIX} Не найдено количество в описании заказа")
            return
        am_val = int(m_am.group(1))
        
        total_amount = am_val * amount_str
        
        api_services = self.config.get('api_services', [])
        api_url = None
        api_token = None
        for svc in api_services:
            if svc.get('name', '').lower() == api_name.lower():
                api_url = svc.get('url', '')
                api_token = svc.get('token', '')
                break
                
        if not api_url or not api_token:
            logger.warning(f"{LOGGER_PREFIX} Не найден API сервис с именем: {api_name}")
            return
            
        with self.processing_lock:
            pending_orders[buyer_id] = {
                'order_id': order_id,
                'service_id': service_id,
                'total_amount': total_amount,
                'api_url': api_url,
                'api_token': api_token,
                'chat_id': chat_id_order,
                'username': username,
                'order_summa': order_summa,
                'order_description': order_description,
                'type': 'smm'
            }
            
        msg_fp = self.format_template('smm_new_order_fp', order_description=order_description, total_amount=total_amount)
        self.cardinal.send_message(chat_id_order, msg_fp)
        
        SMMUtils.save_order_info(order_id, order_summa, order_description)
        
        tg_msg = f"\n🆕 <b>Новый заказ SMM</b>\n\n👤 Покупатель: <b>{username}</b>\n🛍️ Лот: <b>{order_description}</b>\n📦 Количество: <b>{total_amount}</b>\n💰 Сумма: <b>{order_summa}₽</b>\n🆔 ID услуги: <b>{service_id}</b>\n🌐 API: <b>{api_name}</b>\n\n⏳ Ожидает ссылку от покупателя...\n"
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(text='Перейти к заказу', url=f"https://funpay.com/orders/{order_id}/"))
        send_notification_async(self.bot, tg_msg, reply_markup=markup)
        
        logger.info(f"{LOGGER_PREFIX} SMM заказ {order_id} готов к обработке ссылки")


    def _handle_regular_order(self, order_id, buyer_id, chat_id_order, username, order_summa, order_description, tg_id):
        with self.processing_lock:
            pending_orders[buyer_id] = {
                'order_id': order_id,
                'service_id': tg_id[0],
                'total_amount': tg_id[1],
                'chat_id': chat_id_order,
                'username': username,
                'order_summa': order_summa,
                'order_description': order_description,
                'type': 'regular'
            }
            
        msg_fp = self.format_template('smm_new_order_fp', order_description=order_description, total_amount=tg_id[1])
        self.cardinal.send_message(chat_id_order, msg_fp)
        
        SMMUtils.save_order_info(order_id, order_summa, order_description)
        
        tg_msg = f"\n🆕 <b>Новый заказ</b>\n\n👤 Покупатель: <b>{username}</b>\n🛍️ Лот: <b>{order_description}</b>\n📦 Количество: <b>{tg_id[1]}</b>\n💰 Сумма: <b>{order_summa}₽</b>\n🆔 ID услуги: <b>{tg_id[0]}</b>\n\n⏳ Ожидает ссылку от покупателя...\n"
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(text='Перейти к заказу', url=f"https://funpay.com/orders/{order_id}/"))
        send_notification_async(self.bot, tg_msg, reply_markup=markup)
        
        logger.info(f"{LOGGER_PREFIX} Обычный заказ {order_id} готов к обработке ссылки")

    def format_template(self, template_key: str, **kwargs) -> str:
        with self.processing_lock:
            template = self.messages.get(template_key, f"Error: Missing {template_key} template")
        
        try:
            res = template.format(**kwargs)
        except KeyError as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка форматирования шаблона {template_key}: отсутствует ключ {e}")
            res = template
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка форматирования шаблона {template_key}: {e}")
            res = template
            
        if '\\n' in res:
            res = res.replace('\\n', '\n')
        return res

    def _remove_order_from_data(self, order_id: int) -> None:
        try:
            with SMMUtils.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM orders_data WHERE order_id = ?', (order_id,))
                conn.commit()
            SMMUtils._orders_data_cache = None
            logger.info(f"{LOGGER_PREFIX} Заказ {order_id} удален из БД")
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка при удалении заказа из БД: {e}")

    def _process_api_error(self, order_id: int, username: str, order_description: str, order_summa: float, chat_id: int, error_type: str) -> None:
        logger.info(f"{LOGGER_PREFIX} Обработка ошибки API: {error_type} для заказа {order_id}")
        
        error_mapping = {
            'user_inactive': 'пользователь не активен',
            'service_inactive': 'услуга не активна',
            'insufficient_funds': 'недостаточно средств на балансе API'
        }
        
        error_msg = error_mapping.get(error_type, f"неизвестная ошибка: {error_type}")
        
        self.cardinal.send_message(chat_id, f"❌ Ошибка при выполнении заказа: {error_msg}. Обратитесь к администратору.")
        
        tg_msg = f"\n❌ <b>СПЕЦИФИЧЕСКАЯ ОШИБКА API</b>\n\n👤 Покупатель: <b>{username}</b>\n🛍️ Лот: <b>{order_description}</b>\n💰 Сумма: <b>{order_summa}₽</b>\n\n❌ Тип ошибки: <b>{error_type}</b>\n❌ Описание: <b>{error_msg}</b>\n\n⚠️ Требуется проверка заказа\n"
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(text='Перейти к заказу', url=f"https://funpay.com/orders/{order_id}/"))
        send_notification_async(self.bot, tg_msg, reply_markup=markup)

    def message_handler(self, event: NewMessageEvent) -> None:
        message = event.message
        text = message.text
        author_id = message.author_id
        chat_id = message.chat_id
        
        if author_id == self.account.id:
            return
            
        if not text:
            return
            
        if text.lower().startswith('чек '):
            order_id_str = text.split(' ', 1)[1].strip().lstrip('#')
            self.check_order_command(chat_id, order_id_str)
            return
            
        if text.lower().startswith('рефилл '):
            order_id_str = text.split(' ', 1)[1].strip().lstrip('#')
            self.refill_order_command(chat_id, order_id_str)
            return
            
        link_match = None
        m_http = re.search(r'(https?://\S+)', text)
        if m_http:
            link_match = m_http.group(1)
        else:
            m_tme = re.search(r'(?:(?<=\s)|^)((?:https?://)?t\.me/\S+)', text)
            if m_tme:
                link_match = m_tme.group(1)
                
        if link_match and self.running:
            logger.info(f"{LOGGER_PREFIX} Обнаружена ссылка: {link_match} от пользователя {author_id}")
            
            with self.processing_lock:
                if author_id in pending_orders:
                    order_data = pending_orders[author_id]
                    order_id = order_data['order_id']
                    
                    if order_id in processed_orders:
                        logger.info(f"{LOGGER_PREFIX} Заказ {order_id} уже обработан")
                        self.cardinal.send_message(chat_id, '⚠️ Этот заказ уже был обработан.')
                        return
                        
                    if SMMUtils.is_valid_link(link_match):
                        processed_orders.add(order_id)
                        del pending_orders[author_id]
                        
                        threading.Thread(target=self._process_order_with_link, args=(order_data, link_match), daemon=True).start()
                    else:
                        msg_err = self.messages.get('error_invalid_link_fp', '❌ Недопустимая ссылка. Поддерживаются только ссылки на VK, Telegram, Instagram и TikTok.')
                        self.cardinal.send_message(chat_id, msg_err)
                else:
                    logger.info(f"{LOGGER_PREFIX} Не найден ожидающий заказ для пользователя {author_id}")


    def _process_order_with_link(self, order_data, link):
        order_id_fp = order_data['order_id']
        service_id = order_data['service_id']
        total_amount = order_data['total_amount']
        chat_id_order = order_data['chat_id']
        username = order_data['username']
        order_summa = order_data['order_summa']
        order_description = order_data['order_description']
        
        if order_data['type'] == 'smm':
            api_url = order_data['api_url']
            api_token = order_data['api_token']
        else:
            services = self.config.get('api_services', [])
            if not services:
                self.cardinal.send_message(chat_id_order, '❌ Нет настроенных API сервисов')
                return
            service = services[0]
            api_url = service.get('url', '')
            api_token = service.get('token', '')
            
        logger.info(f"{LOGGER_PREFIX} Обработка заказа {order_id_fp} со ссылкой {link}")
        
        params = {
            'action': 'add',
            'service': service_id,
            'link': link,
            'quantity': total_amount,
            'key': api_token
        }
        
        try:
            response = requests.get(api_url, params=params, timeout=10)
            if response.status_code == 200:
                try:
                    data = response.json()
                except Exception as e:
                    logger.error(f"{LOGGER_PREFIX} Некорректный JSON при создании заказа: {e}")
                    self.cardinal.send_message(chat_id_order, '❌ Временная ошибка API при создании заказа. Попробуйте позже.')
                    return
                    
                if 'error' in data:
                    err_msg = data.get('error', 'Неизвестная ошибка API')
                    logger.error(f"{LOGGER_PREFIX} API ошибка: {err_msg}")
                    self.process_refund(order_id_fp, f"Ошибка создания заказа в API: {err_msg}")
                    return
                    
                if 'order' in data:
                    smm_order_id = data['order']
                    logger.info(f"{LOGGER_PREFIX} Заказ создан успешно: {smm_order_id}")
                    
                    url_status = f"{api_url}?action=status&order={smm_order_id}&key={api_token}"
                    try:
                        resp_st = requests.get(url_status, timeout=10)
                        charge_val = 0
                        if resp_st.status_code == 200:
                            try:
                                st_data = resp_st.json()
                                if 'charge' in st_data:
                                    charge_val = float(st_data.get('charge', '0'))
                            except (ValueError, TypeError) as e:
                                logger.error(f"{LOGGER_PREFIX} Ошибка парсинга цены: {e}")
                    except Exception:
                        charge_val = 0
                        
                    clean_profit = order_summa - charge_val
                    
                    msg_fp = self.format_template('order_link_confirmation_fp', order_id_smm=smm_order_id, link=link)
                    self.cardinal.send_message(chat_id_order, msg_fp)
                    
                    tg_msg = f"\n✅ <b>Заказ создан!</b>\n\n🆔 ID в SMM: <b>{smm_order_id}</b>\n👤 Покупатель: <b>{username}</b>\n🔗 Ссылка: <code>{link}</code>\n💰 Цена заказа: <b>{order_summa}₽</b>\n💸 Потрачено на API: <b>{charge_val}₽</b>\n💎 Чистая прибыль: <b>{clean_profit:.2f}₽</b>\n\n📈 Заказ отправлен на выполнение!\n"
                    markup = InlineKeyboardMarkup()
                    markup.add(InlineKeyboardButton(text='Перейти к заказу', url=f"https://funpay.com/orders/{order_id_fp}/"))
                    send_notification_async(self.bot, tg_msg, reply_markup=markup)
                    
                    SMMUtils.save_order_data(chat_id_order, order_id_fp, smm_order_id, 'Pending', api_url, api_token, clean_profit, service_id, total_amount, link, False)
                    self.check_order_status(smm_order_id, chat_id_order, link, order_id_fp)
                else:
                    logger.error(f"{LOGGER_PREFIX} API не вернуло ID заказа")
                    self.process_refund(order_id_fp, "API не вернуло ID заказа")
            else:
                logger.error(f"{LOGGER_PREFIX} Ошибка API: {response.status_code}")
                self.process_refund(order_id_fp, f"Ошибка соединения с API: HTTP {response.status_code}")
                
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка при обработке заказа: {e}")
            self.process_refund(order_data.get('order_id', 0), f"Критическая ошибка обработки заказа: {str(e)}")

    def check_order_command(self, chat_id: int, order_id: str) -> None:
        orders_data = SMMUtils.load_orders_data()
        current_order = None
        for data in orders_data:
            if str(data.get('id_zakaz', '')).lstrip('#') == order_id or str(data.get('order_id', '')).lstrip('#') == order_id:
                current_order = data
                logger.info(f"{LOGGER_PREFIX} Найден заказ: {data.get('id_zakaz')} (по запросу {order_id})")
                break
                
        if not current_order:
            for data in orders_data:
                if str(data.get('id_zakaz')) == order_id or str(data.get('order_id')) == order_id:
                    current_order = data
                    break
                    
        if not current_order:
            msg_err = self.format_template('error_id_not_found_fp', order_id=order_id)
            if '{' not in msg_err and '}' not in msg_err:
                msg_err = f"❌ Заказ с ID {order_id} не найден в базе данных"
            logger.warning(f"{LOGGER_PREFIX} {msg_err}")
            return
            
        id_zakaz = str(current_order.get('id_zakaz'))
        
        if 'api_url' in current_order and current_order['api_url'] and 'api_token' in current_order and current_order['api_token']:
            api_url = current_order['api_url']
            api_token = current_order['api_token']
            logger.info(f"{LOGGER_PREFIX} Используем сохраненный API: {api_url}")
        else:
            api_services = self.config.get('api_services', [])
            api_url = 'https://neversmm.ru/api/v2'
            api_token = getattr(self, 'key', '')
            if api_services:
                service = api_services[0]
                api_url = service.get('url', '')
                api_token = service.get('token', '')
            logger.info(f"{LOGGER_PREFIX} Используем API по умолчанию: {api_url}")
            
        url = f"{api_url}?action=status&order={id_zakaz}&key={api_token}"
        logger.info(f"{LOGGER_PREFIX} Проверка статуса заказа {id_zakaz} по команде...")
        
        try:
            response = requests.get(url, timeout=10)
            logger.info(f"{LOGGER_PREFIX} Ответ API ({response.status_code}): {response.text[:200]}")
            
            if response.status_code == 200:
                try:
                    data = response.json()
                except Exception as e:
                    logger.error(f"{LOGGER_PREFIX} Некорректный JSON при проверке заказа {id_zakaz}: {e}")
                    self.cardinal.send_message(chat_id, '❌ Временная ошибка API при проверке статуса. Попробуйте позже.')
                    return
                    
                logger.debug(f"{LOGGER_PREFIX} Полный ответ API для проверки заказа {id_zakaz}: {data}")
                status = data.get('status', 'Unknown')
                remains = data.get('remains', 0)
                remains_val = int(remains) if remains is not None else 0
                start_count = data.get('start_count', 0)
                start_val = int(start_count) if start_count is not None else 0
                
                charge_val = 0
                if 'charge' in data:
                    try:
                        charge_val = float(data.get('charge', '0'))
                        logger.info(f"{LOGGER_PREFIX} Успешно получена цена заказа при проверке: {charge_val}")
                        if current_order and current_order.get('clean_profit', 0) != 0 and charge_val > 0:
                            summa = 0
                            try:
                                with open(ORDERS_PATH, 'r', encoding='utf-8') as f:
                                    try:
                                        log_data = json.load(f)
                                    except json.JSONDecodeError:
                                        log_data = []
                                for entry in log_data:
                                    if entry.get('order_id') == current_order.get('order_id', 0):
                                        summa = entry.get('summa', 0)
                                        break
                            except Exception:
                                pass
                                
                            if summa == 0:
                                try:
                                    order_fp = self.account.get_order(current_order.get('order_id', 0))
                                    summa = order_fp.sum
                                except Exception as e:
                                    logger.error(f"{LOGGER_PREFIX} Ошибка получения цены заказа FunPay при проверке: {e}")
                                    
                            if summa > 0:
                                new_profit = summa - charge_val
                                logger.info(f"{LOGGER_PREFIX} Обновление данных о прибыли при проверке: было {current_order.get('clean_profit', 0)}, стало {new_profit}")
                                SMMUtils.update_order_data_profit(current_order.get('order_id', 0), new_profit)
                    except (ValueError, TypeError) as e:
                        logger.error(f"{LOGGER_PREFIX} Ошибка при парсинге цены заказа при проверке: {e}. Значение: {data.get('charge')}")
                else:
                    logger.warning(f"{LOGGER_PREFIX} Поле 'charge' отсутствует в ответе API при проверке: {data}")
                    
                if start_val >= remains_val:
                    current_count = start_val - remains_val
                    try:
                        percent_done = (current_count / start_val) * 100 if start_val > 0 else 0
                    except (TypeError, ZeroDivisionError) as e:
                        logger.error(f"{LOGGER_PREFIX} Ошибка при вычислении прогресса: {e}")
                        current_count = 0
                        percent_done = 0
                else:
                    current_count = 0
                    percent_done = 0
                    
                status_emoji = '⏳'
                if status.lower() == 'completed':
                    status_emoji = '✅'
                    if current_order:
                        SMMUtils.update_order_status(current_order.get('order_id', ''), 'Completed')
                        SMMUtils.update_partial_notification_sent(current_order.get('order_id', ''), False)
                elif status.lower() in ('canceled', 'error', 'failed'):
                    status_emoji = '❌'
                elif status.lower() == 'partial':
                    status_emoji = '⚠️'
                    
                api_name = api_url.split('//')[1] if '//' in api_url else api_url
                last_update = datetime.now().strftime('%H:%M:%S %d.%m.%Y')
                
                logger.info(f"{LOGGER_PREFIX} Подготовка данных для статуса заказа: order_id={order_id}, status={status}, current_count={current_count}, start_count={start_val}, percent_done={percent_done}, charge={charge_val}, api_name={api_name}")
                
                msg_status = self.format_template('order_status_check_fp', order_id=order_id, status_emoji=status_emoji, status=status, current_count=current_count, start_count=start_val, percent_done=percent_done, charge=charge_val, api_name=api_name, last_update=last_update)
                
                if '{' in msg_status and '}' in msg_status:
                    logger.warning(f"{LOGGER_PREFIX} Форматирование шаблона не сработало. Использую базовый шаблон.")
                    msg_status = f"\n📊 Информация о заказе {order_id}:\n    ∟ 📌 Статус: {status_emoji} {status}\n    ∟ 💰 Цена: {charge_val}₽\n    ∟ ⏳ Обновлено: {last_update}\n"
                    
                logger.info(f"{LOGGER_PREFIX} Отправка информации о статусе заказа {order_id}")
                self.cardinal.send_message(chat_id, msg_status)
            else:
                err_msg = f"❌ Не удалось проверить статус заказа {order_id}. Код ошибки: {response.status_code}."
                logger.error(f"{LOGGER_PREFIX} {err_msg}")
                self.cardinal.send_message(chat_id, err_msg)
        except requests.RequestException as e:
            err_msg = f"❌ Ошибка при запросе к API: {str(e)}"
            logger.error(f"{LOGGER_PREFIX} {err_msg}")
            self.cardinal.send_message(chat_id, err_msg)
        except Exception as e:
            err_msg = f"❌ Произошла ошибка при проверке статуса: {str(e)}"
            logger.error(f"{LOGGER_PREFIX} {err_msg}")
            self.cardinal.send_message(chat_id, err_msg)

    def refill_order_command(self, chat_id: int, order_id: str) -> None:
        orders_data = SMMUtils.load_orders_data()
        current_order = None
        for data in orders_data:
            if str(data.get('id_zakaz', '')).lstrip('#') == order_id or str(data.get('order_id', '')).lstrip('#') == order_id:
                current_order = data
                logger.info(f"{LOGGER_PREFIX} Найден заказ: {data.get('id_zakaz')} (по запросу {order_id})")
                break
                
        if not current_order:
            for data in orders_data:
                if str(data.get('id_zakaz')) == order_id or str(data.get('order_id')) == order_id:
                    current_order = data
                    break
                    
        if not current_order:
            msg_err = self.format_template('error_id_not_found_fp', order_id=order_id)
            if '{' not in msg_err and '}' not in msg_err:
                msg_err = f"❌ Заказ с ID {order_id} не найден в базе данных"
            logger.warning(f"{LOGGER_PREFIX} {msg_err}")
            return
            
        id_zakaz = str(current_order.get('id_zakaz'))
        
        if 'api_url' in current_order and current_order['api_url'] and 'api_token' in current_order and current_order['api_token']:
            api_url = current_order['api_url']
            api_token = current_order['api_token']
            logger.info(f"{LOGGER_PREFIX} Используем сохраненный API: {api_url}")
        else:
            api_services = self.config.get('api_services', [])
            api_url = 'https://neversmm.ru/api/v2'
            api_token = getattr(self, 'key', '')
            if api_services:
                service = api_services[0]
                api_url = service.get('url', '')
                api_token = service.get('token', '')
            logger.info(f"{LOGGER_PREFIX} Используем API по умолчанию: {api_url}")
            
        logger.info(f"{LOGGER_PREFIX} Запрос рефилла для заказа {id_zakaz}...")
        params = {
            'action': 'refill',
            'order': id_zakaz,
            'key': api_token
        }
        
        try:
            response = requests.get(api_url, params=params, timeout=10)
            logger.info(f"{LOGGER_PREFIX} Ответ API ({response.status_code}): {response.text[:200]}")
            
            if response.status_code == 200:
                try:
                    data = response.json()
                except Exception:
                    data = {}
                    
                is_success = False
                if isinstance(data, dict):
                    if 'error' not in data or not data.get('error'):
                        if 'refill' in data and str(data.get('refill')).lower() in ('1', 'true', 'yes'):
                            is_success = True
                        elif str(data.get('status', '')).lower() in ('success', 'ok', 'accepted'):
                            is_success = True
                        elif any(k in data for k in ('refill_id', 'refillID', 'refillId')):
                            is_success = True
                            
                if is_success:
                    logger.info(f"{LOGGER_PREFIX} Рефилл успешно запрошен: order_id={id_zakaz}")
                    msg_success = self.format_template('order_refill_success_fp', order_id=id_zakaz)
                    if '{' in msg_success and '}' in msg_success:
                        msg_success = f"\n✅ Рефилл для заказа {id_zakaz} успешно запрошен!\n"
                    self.cardinal.send_message(chat_id, msg_success)
                else:
                    err_msg = ""
                    if isinstance(data, dict):
                        err_msg = data.get('error', '')
                    if not err_msg:
                        err_msg = "Неожиданный ответ API"
                        
                    log_err = f"❌ Ошибка при запросе рефилла: {err_msg}. Полный ответ API: {data if data else response.text[:200]}"
                    logger.warning(f"{LOGGER_PREFIX} {log_err}")
                    self.cardinal.send_message(chat_id, log_err)
            else:
                err_msg = f"❌ Не удалось запросить рефилл для заказа {id_zakaz}. Код ошибки: {response.status_code}."
                logger.error(f"{LOGGER_PREFIX} {err_msg}")
                self.cardinal.send_message(chat_id, err_msg)
        except requests.RequestException as e:
            err_msg = f"❌ Ошибка при запросе к API"
            logger.error(f"{LOGGER_PREFIX} {err_msg}")
            self.cardinal.send_message(chat_id, err_msg)
        except Exception as e:
            err_msg = f"❌ Произошла ошибка при запросе рефилла"
            logger.error(f"{LOGGER_PREFIX} {err_msg}")
            self.cardinal.send_message(chat_id, err_msg)

    def process_refund(self, order_id_funpay: int, reason: str) -> None:
        refund_type = self.config.get('refund_type', 'automatic')
        if refund_type == 'automatic':
            self._process_automatic_refund(order_id_funpay, reason)
        else:
            self._process_manual_refund(order_id_funpay, reason)

    def _process_automatic_refund(self, order_id_funpay: int, reason: str) -> None:
        logger.info(f"{LOGGER_PREFIX} Выполнение автоматического возврата для заказа {order_id_funpay}")
        self.account.refund(order_id_funpay)
        logger.info(f"{LOGGER_PREFIX} Автоматический возврат успешно выполнен для заказа {order_id_funpay}")
        
        try:
            order_fp = self.account.get_order(order_id_funpay)
            buyer = order_fp.buyer_username
            desc = self._get_full_order_description(order_id_funpay)
            summa = order_fp.sum
            chat_id = order_fp.chat_id
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка получения данных заказа для уведомления: {e}")
            buyer = "Неизвестно"
            desc = "Неизвестно"
            summa = 0
            chat_id = None
            
        if chat_id:
            msg_fp = self.format_template('automatic_refund_message_fp')
            self.cardinal.send_message(chat_id, msg_fp)
            logger.info(f"{LOGGER_PREFIX} Отправлено сообщение клиенту об автоматическом возврате для заказа {order_id_funpay}")
            
        tg_msg = f"\n✅ <b>АВТОМАТИЧЕСКИЙ ВОЗВРАТ ВЫПОЛНЕН</b>\n\n🆔 ID заказа в FunPay: <b>{order_id_funpay}</b>\n👤 Покупатель: <b>{buyer}</b>\n🛍️ Лот: <b>{desc}</b>\n\n❌ Причина: <b>{reason}</b>\n\n✅ Средства автоматически возвращены покупателю\n"
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(text='Перейти к заказу', url=f"https://funpay.com/orders/{order_id_funpay}/"))
        send_notification_async(self.bot, tg_msg, reply_markup=markup)

    def _process_manual_refund(self, order_id_funpay: int, reason: str) -> None:
        logger.info(f"{LOGGER_PREFIX} Запрос ручного возврата для заказа {order_id_funpay}")
        
        try:
            order_fp = self.account.get_order(order_id_funpay)
            buyer = order_fp.buyer_username
            desc = self._get_full_order_description(order_id_funpay)
            summa = order_fp.sum
            chat_id = order_fp.chat_id
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка получения данных заказа для уведомления: {e}")
            buyer = "Неизвестно"
            desc = "Неизвестно"
            summa = 0
            chat_id = None
            
        tg_msg = f"\n🚨 <b>ТРЕБУЕТСЯ РУЧНОЙ ВОЗВРАТ</b>\n\n🆔 ID заказа в FunPay: <b>{order_id_funpay}</b>\n👤 Покупатель: <b>{buyer}</b>\n🛍️ Лот: <b>{desc}</b>\n\n❌ Причина возврата: <b>{reason}</b>\n\n⚠️ <b>НЕОБХОДИМО ВЫПОЛНИТЬ ВОЗВРАТ ВРУЧНУЮ!</b>\n"
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(text='Перейти к заказу', url=f"https://funpay.com/orders/{order_id_funpay}/"))
        send_notification_async(self.bot, tg_msg, reply_markup=markup)
        
        if chat_id:
            msg_fp = self.format_template('manual_refund_message_fp')
            self.cardinal.send_message(chat_id, msg_fp)
            logger.info(f"{LOGGER_PREFIX} Отправлено сообщение клиенту о ручном возврате для заказа {order_id_funpay}")


class TelegramHandler:
    def __init__(self, cardinal: Cardinal, order_processor: OrderProcessor):
        self.cardinal = cardinal
        self.bot = cardinal.telegram.bot
        self.config = ConfigManager.load_config()
        self.user_data_store = {}
        self.order_processor = order_processor

    def register_handlers(self) -> None:
        self.cardinal.add_telegram_commands(UUID, [
            ('auto_smm_settings', 'настройки автопродажи накрутки', True)
        ])
        
        self.cardinal.telegram.msg_handler(self.auto_smm_settings, commands=['auto_smm_settings'])
        
        self.bot.message_handler(func=lambda message: hasattr(self, 'user_editing_templates') and message.chat.id in self.user_editing_templates and not message.text.startswith('/'))(self.handle_command_during_template_editing)
        
        self.bot.callback_query_handler(func=lambda call: call.data in ('api_settings', 'add_api_service', 'list_api_services', 'check_all_balances', 'smm_return_to_settings', 'cancel_api_service', 'show_statistics', 'message_templates', 'toggle_notifications', 'reset_all_templates', 'toggle_refund_type') or call.data.startswith(('service_options_', 'set_active_', 'check_service_balance_', 'list_service_services_', 'delete_service_', 'edit_template_')))(self.handle_callback_query)

    def handle_command_during_template_editing(self, message):
        chat_id = message.chat.id
        self.bot.clear_step_handler_by_chat_id(chat_id)
        
        if hasattr(self, 'user_editing_templates') and chat_id in self.user_editing_templates:
            template_key = self.user_editing_templates[chat_id]
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton(text='⬅️ Назад к шаблонам', callback_data='message_templates'))
            markup.add(InlineKeyboardButton(text='↩️ Продолжить редактирование', callback_data=f'edit_template_{template_key}'))
            
            self.bot.send_message(chat_id, "❌ <b>Ошибка:</b> Текст, начинающийся с '/' распознается как команда и не может быть сохранен как шаблон.\n\nПожалуйста, используйте обычный текст без символа '/' в начале.", parse_mode='HTML', reply_markup=markup)
            del self.user_editing_templates[chat_id]

    def handle_callback_query(self, call):
        logger.info(f"{LOGGER_PREFIX} Получен callback: {call.data}")
        self.bot.clear_step_handler_by_chat_id(call.message.chat.id)
        
        if call.data == 'api_settings':
            self.api_settings(call)
        elif call.data == 'add_api_service':
            self.add_api_service(call.message)
        elif call.data == 'list_api_services':
            self.list_api_services(call)
        elif call.data == 'check_all_balances':
            self.check_all_balances(call)
        elif call.data == 'show_statistics':
            self.show_statistics(call)
        elif call.data == 'message_templates':
            self.message_templates(call)
        elif call.data == 'toggle_notifications':
            self.toggle_notifications(call)
        elif call.data == 'reset_all_templates':
            self.reset_all_templates(call)
        elif call.data == 'toggle_refund_type':
            self.toggle_refund_type(call)
        elif call.data.startswith('edit_template_'):
            self.edit_template(call)
        elif call.data.startswith('service_options_'):
            self.service_options(call)
        elif call.data.startswith('set_active_'):
            self.set_active_service(call)
        elif call.data.startswith('check_service_balance_'):
            self.check_service_balance(call)
        elif call.data.startswith('list_service_services_'):
            self.list_service_services(call)
        elif call.data.startswith('delete_service_'):
            self.delete_service(call)
        elif call.data == 'smm_return_to_settings':
            logger.info(f"{LOGGER_PREFIX} Возврат в настройки...")
            try:
                self.bot.delete_message(call.message.chat.id, call.message.message_id)
            except Exception:
                pass
            self.auto_smm_settings(call.message)
        elif call.data == 'cancel_api_service':
            self.cancel_api_service_creation(call)

    def cancel_api_service_creation(self, call: types.CallbackQuery) -> None:
        chat_id = call.message.chat.id
        if hasattr(self, 'user_editing_templates') and chat_id in self.user_editing_templates:
            del self.user_editing_templates[chat_id]
            
        if chat_id in self.user_data_store:
            del self.user_data_store[chat_id]
            
        try:
            self.bot.delete_message(chat_id, call.message.message_id)
        except Exception:
            pass
            
        self.bot.clear_step_handler_by_chat_id(chat_id)
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(text='⬅️ Вернуться к настройкам API', callback_data='api_settings'))
        self.bot.send_message(chat_id, '✅ Создание API сервиса отменено.', reply_markup=markup)

    def start_smm(self, message: types.Message) -> None:
        logger.info(f"{LOGGER_PREFIX} Запуск автопродажи накрутки...")
        
        if not os.path.exists(CONFIG_PATH):
            logger.info(f"{LOGGER_PREFIX} Конфигурационный файл не найден. Создание нового файла с настройками по умолчанию.")
            default_config = {
                'api_services': [{
                    'name': 'Основной',
                    'url': 'https://twiboost.com/api/v2',
                    'token': 'rRwtJLdQ8XssECEeK562o0rlHOyWwMTZy5TN1T4Aci1D2yMIkrMcXwrydgCB'
                }]
            }
            ConfigManager.save_config(default_config)
            self.bot.send_message(message.chat.id, 'Файл конфигурации не найден. Создан новый файл с настройками по умолчанию.')
            
        self.config = ConfigManager.load_config()
        self.order_processor.config = self.config
        
        api_services = self.config.get('api_services', [])
        if api_services:
            self.order_processor.key = api_services[0].get('token', '')
        else:
            self.order_processor.key = ''
            
        self.order_processor.lot_mapping = self.config.get('lot_mapping', {})
        self.order_processor.chat_id = self.config.get('chat_id', 0x1DE9564EB)
        
        if self.order_processor.running:
            logger.warning(f"{LOGGER_PREFIX} Автопродажа накрутки уже активна.")
            self.bot.send_message(message.chat.id, '❌ Автопродажа накрутки уже активна! 🚨')
            return
            
        self.order_processor.running = True
        logger.info(f"{LOGGER_PREFIX} Автопродажа накрутки активирована.")
        self.bot.send_message(message.chat.id, '✅ Автопродажа накрутки активирована. 🚀')

    def stop_smm(self, message: types.Message) -> None:
        logger.info(f"{LOGGER_PREFIX} Остановка автопродажи накрутки...")
        
        if not self.order_processor.running:
            logger.warning(f"{LOGGER_PREFIX} Автопродажа накрутки уже отключена.")
            self.bot.send_message(message.chat.id, '❌ Автопродажа накрутки уже отключена! 🚨')
            return
            
        self.order_processor.running = False
        logger.info(f"{LOGGER_PREFIX} Автопродажа накрутки отключена.")
        self.bot.send_message(message.chat.id, '✅ Автопродажа накрутки отключена. 🛑')

    def auto_smm_settings(self, message: types.Message) -> None:
        logger.info(f"{LOGGER_PREFIX} Открытие настроек автопродажи накрутки...")
        
        logger.info(f"{LOGGER_PREFIX} Загрузка конфигурации...")
        config_data = ConfigManager.load_config()
        logger.info(f"{LOGGER_PREFIX} Конфигурация успешно загружена.")
        
        chat_id_fp = config_data.get('chat_id', 'Не указан')
        api_services = config_data.get('api_services', [])
        telegram_notifications = config_data.get('telegram_notifications', False)
        refund_type = config_data.get('refund_type', 'automatic')
        
        total_balance = 0
        balance_text = ""
        
        for service in api_services:
            url = service.get('url', '')
            token = service.get('token', '')
            if url.startswith(('http://', 'https://')):
                pass
            else:
                url = 'https://' + url.lstrip('/')
                
            api_balance_url = f"{url}?action=balance&key={token}"
            logger.info(f"{LOGGER_PREFIX} Запрос баланса по URL: {api_balance_url}")
            
            try:
                response = requests.get(api_balance_url, timeout=10)
                if response.status_code == 200:
                    resp_data = response.json()
                    balance = float(resp_data.get('balance', 0))
                    currency = resp_data.get('currency', '')
                    total_balance += balance
                    balance_text += f"\n∟ {service.get('name', 'API')}: {balance} {currency}"
            except Exception as e:
                logger.error(f"{LOGGER_PREFIX} Ошибка при проверке баланса: {e}")
                balance_text += f"\n∟ {service.get('name', 'API')}: Ошибка"
                
        tg_status = '✅ Включены' if telegram_notifications else '❌ Отключены'
        rt_status = '🤖 Автоматический' if refund_type == 'automatic' else '👤 Ручной'
        
        msg_text = f"\n📊 SMM Автонакрутка:\n\n∟ API сервисов: <code>{len(api_services)}</code>\n∟ Общий баланс: <code>{total_balance}</code>{balance_text}\n∟ Тип возврата: {rt_status}\n\nВыбери действие:\n"
        
        logger.info(f"{LOGGER_PREFIX} Сформировано сообщение: {msg_text}")
        
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(
            InlineKeyboardButton(text='🔑 Настройка API', callback_data='api_settings'),
            InlineKeyboardButton(text='📊 Статистика прибыли', callback_data='show_statistics'),
            InlineKeyboardButton(text='📝 Шаблоны сообщений', callback_data='message_templates'),
            InlineKeyboardButton(text=f'🔄 Возврат: {rt_status}', callback_data='toggle_refund_type'),
            InlineKeyboardButton(text='🚀 Сервис для накрутки', url='Https://vexboost.ru/ref3330613')
        )
        
        logger.info(f"{LOGGER_PREFIX} Клавиатура успешно создана.")
        logger.info(f"{LOGGER_PREFIX} Попытка отправить сообщение...")
        
        try:
            self.bot.send_message(message.chat.id, msg_text, parse_mode='HTML', reply_markup=markup)
            logger.info(f"{LOGGER_PREFIX} Сообщение успешно отправлено.")
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка в функции auto_smm_settings: {e}")
            try:
                self.bot.send_message(message.chat.id, f"❌ Произошла ошибка: {str(e)}")
            except:
                pass

    def show_statistics(self, call: types.CallbackQuery) -> None:
        logger.info(f"{LOGGER_PREFIX} Показ статистики прибыли...")
        try:
            self.bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
            
        stats = calculate_profit_statistics()
        
        clean_day = stats['day']['profit'] - stats['day']['fee']
        clean_week = stats['week']['profit'] - stats['week']['fee']
        clean_month = stats['month']['profit'] - stats['month']['fee']
        clean_total = stats['total']['profit'] - stats['total']['fee']
        
        msg_text = f"\n📊 <b>Статистика прибыли</b>\n\n📈 <b>Всего:</b>\n∟ 💰 Прибыль (общая): <code>{stats['total']['profit']:.2f}₽</code>\n∟ 💸 Комиссия (3%): <code>{stats['total']['fee']:.2f}₽</code>\n∟ 💎 Чистая прибыль: <code>{clean_total:.2f}₽</code>\n∟ 📊 Заказов: <code>{stats['total']['count']}</code>\n"
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(text='⬅️ Вернуться назад', callback_data='smm_return_to_settings'))
        
        self.bot.send_message(call.message.chat.id, msg_text, parse_mode='HTML', reply_markup=markup)

    def api_settings(self, call: types.CallbackQuery) -> None:
        logger.info(f"{LOGGER_PREFIX} Открытие настроек API...")
        try:
            self.bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
            
        config_data = ConfigManager.load_config()
        api_services = config_data.get('api_services', [])
        
        msg_text = f"🔑 Управление API сервисами\n\nКоличество сервисов: <code>{len(api_services)}</code>\n\nВыберите действие:"
        
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(
            InlineKeyboardButton(text='➕ Добавить API сервис', callback_data='add_api_service'),
            InlineKeyboardButton(text='📋 Список API сервисов', callback_data='list_api_services'),
            InlineKeyboardButton(text='💰 Проверить общий баланс', callback_data='check_all_balances'),
            InlineKeyboardButton(text='⬅️ Вернуться назад', callback_data='smm_return_to_settings')
        )
        
        self.bot.send_message(call.message.chat.id, msg_text, parse_mode='HTML', reply_markup=markup)

    def add_api_service(self, message: types.Message) -> None:
        chat_id = message.chat.id
        self.user_data_store[chat_id] = {}
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(text='❌ Отменить', callback_data='cancel_api_service'))
        
        try:
            msg = self.bot.edit_message_text('Введите название сервиса (например: Twiboost):', chat_id, message.message_id, reply_markup=markup)
        except Exception:
            msg = self.bot.send_message(chat_id, 'Введите название сервиса (например: Twiboost):', reply_markup=markup)
            
        self.user_data_store[chat_id]['last_bot_msg_id'] = msg.message_id
        self.bot.register_next_step_handler(msg, self.process_service_name)

    def process_service_name(self, message: types.Message) -> None:
        try:
            self.bot.delete_message(message.chat.id, message.message_id)
        except Exception:
            pass
            
        service_name = message.text.strip()
        if not service_name:
            service_name = 'API Сервис'
            
        chat_id = message.chat.id
        if chat_id not in self.user_data_store:
            self.user_data_store[chat_id] = {}
            
        self.user_data_store[chat_id]['service_name'] = service_name
        logger.info(f"{LOGGER_PREFIX} Сохранение имени сервиса: {service_name}")
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(text='❌ Отменить', callback_data='cancel_api_service'))
        
        last_msg_id = self.user_data_store[chat_id].get('last_bot_msg_id')
        prompt_text = f"Введите URL API для сервиса '{service_name}' (например: https://twiboost.com/api/v2):"
        
        if last_msg_id:
            try:
                msg = self.bot.edit_message_text(prompt_text, chat_id, last_msg_id, reply_markup=markup)
            except Exception:
                msg = self.bot.send_message(chat_id, prompt_text, reply_markup=markup)
        else:
            msg = self.bot.send_message(chat_id, prompt_text, reply_markup=markup)
            
        self.user_data_store[chat_id]['last_bot_msg_id'] = msg.message_id
        self.bot.register_next_step_handler(msg, self.process_service_url)

    def process_service_url(self, message: types.Message) -> None:
        try:
            self.bot.delete_message(message.chat.id, message.message_id)
        except Exception:
            pass
            
        url_input = message.text.strip()
        chat_id = message.chat.id
        last_msg_id = self.user_data_store.get(chat_id, {}).get('last_bot_msg_id')
        
        if not url_input:
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton(text='❌ Отменить', callback_data='cancel_api_service'))
            err_msg = '❌ URL не может быть пустым. Попробуйте снова.'
            if last_msg_id:
                self.bot.edit_message_text(err_msg, chat_id, last_msg_id, reply_markup=markup)
            else:
                try:
                    self.bot.send_message(chat_id, err_msg, reply_markup=markup)
                except:
                    pass
            return
            
        if not url_input.startswith(('http://', 'https://')):
            url_input = 'https://' + url_input.lstrip('/')
            
        url_input = url_input.rstrip('/')
        
        if chat_id not in self.user_data_store:
            self.user_data_store[chat_id] = {}
        self.user_data_store[chat_id]['api_url'] = url_input
        logger.info(f"{LOGGER_PREFIX} Обработанный URL API: {url_input}")
        
        service_name = self.user_data_store[chat_id].get('service_name', 'API Сервис')
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(text='❌ Отменить', callback_data='cancel_api_service'))
        
        prompt_text = f"Введите токен API для сервиса '{service_name}':"
        
        if last_msg_id:
            try:
                msg = self.bot.edit_message_text(prompt_text, chat_id, last_msg_id, reply_markup=markup)
            except Exception:
                msg = self.bot.send_message(chat_id, prompt_text, reply_markup=markup)
        else:
            try:
                msg = self.bot.send_message(chat_id, prompt_text, reply_markup=markup)
            except Exception:
                pass
                
        self.user_data_store[chat_id]['last_bot_msg_id'] = msg.message_id
        self.bot.register_next_step_handler(msg, self.process_service_token)

    def process_service_token(self, message: types.Message) -> None:
        try:
            self.bot.delete_message(message.chat.id, message.message_id)
        except Exception:
            pass
            
        token_input = message.text.strip()
        chat_id = message.chat.id
        last_msg_id = self.user_data_store.get(chat_id, {}).get('last_bot_msg_id')
        
        if not token_input:
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton(text='❌ Отменить', callback_data='cancel_api_service'))
            err_msg = '❌ Токен не может быть пустым. Попробуйте снова.'
            if last_msg_id:
                self.bot.edit_message_text(err_msg, chat_id, last_msg_id, reply_markup=markup)
            else:
                self.bot.send_message(chat_id, err_msg, reply_markup=markup)
            return
            
        if chat_id not in self.user_data_store:
            self.bot.send_message(chat_id, '❌ Ошибка: данные сессии утеряны. Пожалуйста, начните заново.')
            return
            
        service_name = self.user_data_store[chat_id].get('service_name', 'API Сервис')
        api_url = self.user_data_store[chat_id].get('api_url', '')
        
        logger.info(f"{LOGGER_PREFIX} Сохраненные данные - имя: {service_name}, URL: {api_url}")
        
        if not api_url.startswith(('http://', 'https://')):
            api_url = 'https://' + api_url.lstrip('/')
        api_url = api_url.rstrip('/')
        
        config_data = ConfigManager.load_config()
        if 'api_services' not in config_data:
            config_data['api_services'] = []
            
        for svc in config_data['api_services']:
            if svc.get('name') == service_name:
                err_msg = f"❌ Сервис с названием '{service_name}' уже существует. Пожалуйста, выберите другое название."
                if last_msg_id:
                    try:
                        self.bot.edit_message_text(err_msg, chat_id, last_msg_id)
                    except:
                        self.bot.send_message(chat_id, err_msg)
                return
                
        new_svc = {'name': service_name, 'url': api_url, 'token': token_input}
        logger.info(f"{LOGGER_PREFIX} Добавление нового сервиса: {new_svc}")
        
        config_data['api_services'].append(new_svc)
        
        if len(config_data['api_services']) == 1:
            config_data['key'] = token_input
            config_data['api_url'] = api_url
            self.order_processor.key = token_input
            
        ConfigManager.save_config(config_data)
        self.order_processor.config = config_data
        
        check_url = f"{api_url}?action=balance&key={token_input}"
        logger.info(f"{LOGGER_PREFIX} Запрос баланса по URL: {check_url}")
        
        try:
            resp = requests.get(check_url, timeout=10)
            balance_text = ""
            if resp.status_code == 200:
                try:
                    resp_json = resp.json()
                    balance = float(resp_json.get('balance', 0))
                    currency = resp_json.get('currency', '')
                    balance_text = f"\n\nБаланс: {balance} {currency}"
                except Exception as e:
                    logger.error(f"{LOGGER_PREFIX} Ошибка при проверке баланса: код {resp.status_code}, ответ: {resp.text}")
                    balance_text = f"\n\nНе удалось проверить баланс. Код: {resp.status_code}"
            else:
                balance_text = f"\n\nНе удалось проверить баланс. Код: {resp.status_code}"
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка при проверке баланса: {e}")
            balance_text = f"\n\nНе удалось проверить баланс: {str(e)}"
            
        if chat_id in self.user_data_store:
            del self.user_data_store[chat_id]
            
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(text='⬅️ Назад к управлению API', callback_data='api_settings'))
        
        success_msg = f"✅ API сервис '{service_name}' успешно добавлен!\n\nURL: {api_url}\nТокен: {token_input[:10]}...{balance_text}"
        
        if last_msg_id:
            try:
                self.bot.edit_message_text(success_msg, chat_id, last_msg_id, reply_markup=markup)
            except Exception:
                self.bot.send_message(chat_id, success_msg, reply_markup=markup)
        else:
            try:
                self.bot.send_message(chat_id, success_msg, reply_markup=markup)
            except Exception:
                pass

    def list_api_services(self, call: types.CallbackQuery) -> None:
        logger.info(f"{LOGGER_PREFIX} Просмотр списка API сервисов...")
        try:
            self.bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
            
        config_data = ConfigManager.load_config()
        api_services = config_data.get('api_services', [])
        
        active_url = config_data.get('api_url', '')
        active_key = config_data.get('key', '')
        
        if not api_services:
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton(text='➕ Добавить API сервис', callback_data='add_api_service'))
            markup.add(InlineKeyboardButton(text='⬅️ Назад', callback_data='api_settings'))
            self.bot.send_message(call.message.chat.id, 'У вас пока нет добавленных API сервисов.', reply_markup=markup)
            return
            
        msg_text = '📋 Список API сервисов:\n\n'
        markup = InlineKeyboardMarkup(row_width=1)
        
        for idx, svc in enumerate(api_services, 1):
            name = svc.get('name', f'Сервис {idx}')
            url = svc.get('url', '')
            token = svc.get('token', '')
            
            is_active = svc.get('is_active', False)
            if url == active_url and token == active_key:
                is_active = True
                svc['is_active'] = True
                ConfigManager.save_config(config_data)
                
            active_str = '✅ ' if is_active else ''
            
            msg_text += f"{active_str}{idx}. {name}\n"
            msg_text += f"   URL: {url}\n"
            msg_text += f"   Токен: {token[:10]}...\n\n"
            
            markup.add(InlineKeyboardButton(text=f"{active_str}{name}", callback_data=f"service_options_{idx-1}"))
            
        markup.add(InlineKeyboardButton(text='⬅️ Назад', callback_data='api_settings'))
        self.bot.send_message(call.message.chat.id, msg_text, reply_markup=markup)

    def service_options(self, call: types.CallbackQuery) -> None:
        idx = int(call.data.split('_')[2])
        logger.info(f"{LOGGER_PREFIX} Опции для сервиса {idx}...")
        
        try:
            self.bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
            
        config_data = ConfigManager.load_config()
        api_services = config_data.get('api_services', [])
        
        if idx >= len(api_services):
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton(text='⬅️ Назад', callback_data='list_api_services'))
            self.bot.send_message(call.message.chat.id, 'Сервис не найден.', reply_markup=markup)
            return
            
        svc = api_services[idx]
        name = svc.get('name', f'Сервис {idx+1}')
        url = svc.get('url', '')
        token = svc.get('token', '')
        
        active_url = config_data.get('api_url', '')
        active_key = config_data.get('key', '')
        
        is_active = svc.get('is_active', False)
        if url == active_url and token == active_key:
            is_active = True
            svc['is_active'] = True
            ConfigManager.save_config(config_data)
            
        msg_text = f"🔧 Сервис: {name}\n  \nURL: <code>{url}</code>\nТокен: <code>{token[:10]}...</code>\n\n\nВыберите действие:"
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(
            InlineKeyboardButton(text='💰 Проверить баланс', callback_data=f'check_service_balance_{idx}'),
            InlineKeyboardButton(text='📋 Список услуг', callback_data=f'list_service_services_{idx}'),
            InlineKeyboardButton(text='🗑️ Удалить сервис', callback_data=f'delete_service_{idx}'),
            InlineKeyboardButton(text='⬅️ Назад к списку', callback_data='list_api_services')
        )
        self.bot.send_message(call.message.chat.id, msg_text, parse_mode='HTML', reply_markup=markup)

    def set_active_service(self, call: types.CallbackQuery) -> None:
        idx = int(call.data.split('_')[2])
        logger.info(f"{LOGGER_PREFIX} Установка активного сервиса {idx}...")
        
        try:
            self.bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
            
        config_data = ConfigManager.load_config()
        api_services = config_data.get('api_services', [])
        
        if idx >= len(api_services):
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton(text='⬅️ Назад', callback_data='list_api_services'))
            self.bot.send_message(call.message.chat.id, 'Сервис не найден.', reply_markup=markup)
            return
            
        svc = api_services[idx]
        name = svc.get('name', f'Сервис {idx+1}')
        url = svc.get('url', '')
        token = svc.get('token', '')
        
        config_data['api_url'] = url
        config_data['key'] = token
        self.order_processor.key = token
        
        if hasattr(self.order_processor, 'api_url'):
            self.order_processor.api_url = url
            
        for s in api_services:
            if 'is_active' in s:
                del s['is_active']
        svc['is_active'] = True
        
        ConfigManager.save_config(config_data)
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(text='⬅️ Назад к сервису', callback_data=f'service_options_{idx}'))
        
        self.bot.send_message(call.message.chat.id, f"✅ Сервис '{name}' установлен как активный.", reply_markup=markup)
        
        self.order_processor.config = ConfigManager.load_config()

    def check_service_balance(self, call: types.CallbackQuery) -> None:
        idx = int(call.data.split('_')[3])
        logger.info(f"{LOGGER_PREFIX} Проверка баланса сервиса {idx}...")
        
        try:
            self.bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
            
        config_data = ConfigManager.load_config()
        api_services = config_data.get('api_services', [])
        
        if idx >= len(api_services):
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton(text='⬅️ Назад', callback_data='list_api_services'))
            self.bot.send_message(call.message.chat.id, 'Сервис не найден.', reply_markup=markup)
            return
            
        svc = api_services[idx]
        name = svc.get('name', f'Сервис {idx+1}')
        url = svc.get('url', '')
        token = svc.get('token', '')
        
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url.lstrip('/')
            
        check_url = f"{url}?action=balance&key={token}"
        logger.info(f"{LOGGER_PREFIX} Запрос баланса по URL: {check_url}")
        
        try:
            resp = requests.get(check_url, timeout=10)
            if resp.status_code == 200:
                try:
                    resp_json = resp.json()
                    balance = float(resp_json.get('balance', 0))
                    currency = resp_json.get('currency', '')
                    
                    markup = InlineKeyboardMarkup()
                    markup.add(InlineKeyboardButton(text='⬅️ Назад к сервису', callback_data=f'service_options_{idx}'))
                    self.bot.send_message(call.message.chat.id, f"💰 Баланс сервиса '{name}': {balance} {currency}", reply_markup=markup)
                except Exception as e:
                    logger.error(f"{LOGGER_PREFIX} Ошибка при проверке баланса: {e}")
                    markup = InlineKeyboardMarkup()
                    markup.add(InlineKeyboardButton(text='⬅️ Назад к сервису', callback_data=f'service_options_{idx}'))
                    self.bot.send_message(call.message.chat.id, f"❌ Ошибка при проверке баланса: {str(e)}", reply_markup=markup)
            else:
                logger.error(f"{LOGGER_PREFIX} Ошибка при проверке баланса: код {resp.status_code}, ответ: {resp.text}")
                markup = InlineKeyboardMarkup()
                markup.add(InlineKeyboardButton(text='⬅️ Назад к сервису', callback_data=f'service_options_{idx}'))
                self.bot.send_message(call.message.chat.id, f"❌ Ошибка при проверке баланса для сервиса '{name}': {resp.status_code}", reply_markup=markup)
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка при проверке баланса: {e}")
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton(text='⬅️ Назад к сервису', callback_data=f'service_options_{idx}'))
            self.bot.send_message(call.message.chat.id, f"❌ Ошибка при проверке баланса: {str(e)}", reply_markup=markup)

    def list_service_services(self, call: types.CallbackQuery) -> None:
        idx = int(call.data.split('_')[3])
        logger.info(f"{LOGGER_PREFIX} Просмотр услуг сервиса {idx}...")
        
        try:
            self.bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
            
        config_data = ConfigManager.load_config()
        api_services = config_data.get('api_services', [])
        
        if idx >= len(api_services):
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton(text='⬅️ Назад', callback_data='list_api_services'))
            self.bot.send_message(call.message.chat.id, 'Сервис не найден.', reply_markup=markup)
            return
            
        svc = api_services[idx]
        name = svc.get('name', f'Сервис {idx+1}')
        url = svc.get('url', '')
        token = svc.get('token', '')
        
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url.lstrip('/')
            
        api_url = f"{url}?action=services&key={token}"
        logger.info(f"{LOGGER_PREFIX} Запрос списка услуг по URL: {api_url}")
        
        try:
            resp = requests.get(api_url, timeout=10)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    if isinstance(data, list) and data:
                        msg_text = f"📋 Список услуг сервиса '{name}':\n\n"
                        for i, s_data in enumerate(data[:10], 1):
                            msg_text += f"{i}. ID: {s_data.get('service')} - {s_data.get('name')}\n"
                            msg_text += f"   Тип: {s_data.get('type')}, Цена: {s_data.get('rate')} за 1000\n"
                            msg_text += f"   Мин/Макс: {s_data.get('min')}/{s_data.get('max')}\n\n"
                        
                        if len(data) > 10:
                            msg_text += f"\n...и еще {len(data) - 10} услуг"
                            
                        markup = InlineKeyboardMarkup()
                        markup.add(InlineKeyboardButton(text='⬅️ Назад к сервису', callback_data=f'service_options_{idx}'))
                        self.bot.send_message(call.message.chat.id, msg_text, reply_markup=markup)
                    else:
                        markup = InlineKeyboardMarkup()
                        markup.add(InlineKeyboardButton(text='⬅️ Назад к сервису', callback_data=f'service_options_{idx}'))
                        self.bot.send_message(call.message.chat.id, f"❌ Не удалось получить список услуг или список пуст для сервиса '{name}'.", reply_markup=markup)
                except Exception as e:
                    logger.error(f"{LOGGER_PREFIX} Ошибка при получении списка услуг: {e}")
                    markup = InlineKeyboardMarkup()
                    markup.add(InlineKeyboardButton(text='⬅️ Назад к сервису', callback_data=f'service_options_{idx}'))
                    self.bot.send_message(call.message.chat.id, f"❌ Ошибка при получении списка услуг: {str(e)}", reply_markup=markup)
            else:
                markup = InlineKeyboardMarkup()
                markup.add(InlineKeyboardButton(text='⬅️ Назад к сервису', callback_data=f'service_options_{idx}'))
                self.bot.send_message(call.message.chat.id, f"❌ Ошибка при получении списка услуг: {resp.status_code}", reply_markup=markup)
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка при получении списка услуг: {e}")
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton(text='⬅️ Назад к сервису', callback_data=f'service_options_{idx}'))
            self.bot.send_message(call.message.chat.id, f"❌ Ошибка при получении списка услуг: {str(e)}", reply_markup=markup)

    def delete_service(self, call: types.CallbackQuery) -> None:
        idx = int(call.data.split('_')[2])
        logger.info(f"{LOGGER_PREFIX} Удаление сервиса {idx}...")
        
        try:
            self.bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
            
        config_data = ConfigManager.load_config()
        api_services = config_data.get('api_services', [])
        
        if idx >= len(api_services):
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton(text='⬅️ Назад', callback_data='list_api_services'))
            self.bot.send_message(call.message.chat.id, 'Сервис не найден.', reply_markup=markup)
            return
            
        svc = api_services[idx]
        name = svc.get('name', f'Сервис {idx+1}')
        url = svc.get('url', '')
        token = svc.get('token', '')
        
        active_url = config_data.get('api_url', '')
        active_key = config_data.get('key', '')
        
        is_active = False
        if url == active_url and token == active_key:
            is_active = True
            
        del api_services[idx]
        config_data['api_services'] = api_services
        
        if is_active:
            if api_services:
                new_svc = api_services[0]
                config_data['api_url'] = new_svc.get('url', '')
                config_data['key'] = new_svc.get('token', '')
                global KEY
                KEY = new_svc.get('token', '')
                msg_text = f"✅ Сервис '{name}' удален. Сервис '{new_svc.get('name', 'Новый')}' теперь активный."
            else:
                config_data['api_url'] = ''
                config_data['key'] = ''
                KEY = ''
                msg_text = f"✅ Сервис '{name}' удален. У вас не осталось сервисов."
        else:
            msg_text = f"✅ Сервис '{name}' удален."
            
        ConfigManager.save_config(config_data)
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(text='⬅️ Назад к списку', callback_data='list_api_services'))
        self.bot.send_message(call.message.chat.id, msg_text, reply_markup=markup)

    def check_all_balances(self, call: types.CallbackQuery) -> None:
        logger.info(f"{LOGGER_PREFIX} Проверка баланса всех сервисов...")
        
        try:
            self.bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
            
        config_data = ConfigManager.load_config()
        api_services = config_data.get('api_services', [])
        
        if not api_services:
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton(text='➕ Добавить API сервис', callback_data='add_api_service'))
            markup.add(InlineKeyboardButton(text='⬅️ Назад', callback_data='api_settings'))
            self.bot.send_message(call.message.chat.id, 'У вас пока нет добавленных API сервисов.', reply_markup=markup)
            return
            
        msg_text = '💰 Баланс всех API сервисов:\n\n'
        total_balance = 0
        
        for idx, svc in enumerate(api_services, 1):
            name = svc.get('name', f'Сервис {idx}')
            url = svc.get('url', '')
            token = svc.get('token', '')
            
            if not url.startswith(('http://', 'https://')):
                url = 'https://' + url.lstrip('/')
                
            api_url = f"{url}?action=balance&key={token}"
            logger.info(f"{LOGGER_PREFIX} Запрос баланса по URL: {api_url}")
            
            try:
                resp = requests.get(api_url, timeout=10)
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        balance = float(data.get('balance', 0))
                        currency = data.get('currency', '')
                        total_balance += balance
                        msg_text += f"{idx}. {name}: {balance} {currency}\n"
                    except Exception as e:
                        logger.error(f"{LOGGER_PREFIX} Ошибка при проверке баланса: {e}")
                        msg_text += f"{idx}. {name}: Ошибка при проверке баланса: {str(e)}.\n"
                else:
                    msg_text += f"{idx}. {name}: Ошибка при проверке баланса (код {resp.status_code}).\n"
            except Exception as e:
                logger.error(f"{LOGGER_PREFIX} Ошибка при проверке баланса: {e}")
                msg_text += f"{idx}. {name}: Ошибка при проверке баланса: {str(e)}.\n"
                
        msg_text += f"\nОбщий баланс: {total_balance}"
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(text='⬅️ Назад', callback_data='api_settings'))
        self.bot.send_message(call.message.chat.id, msg_text, reply_markup=markup)


    def message_templates(self, call: types.CallbackQuery) -> None:
        logger.info(f"{LOGGER_PREFIX} Открытие настроек шаблонов...")
        try:
            self.bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
            
        self.bot.clear_step_handler_by_chat_id(call.message.chat.id)
        if hasattr(self, 'user_editing_templates') and call.message.chat.id in self.user_editing_templates:
            del self.user_editing_templates[call.message.chat.id]
            
        config_data = ConfigManager.load_config()
        customer_messages = config_data.get('customer_messages', {})
        
        msg_text = '📝 <b>Шаблоны сообщений</b>\n\nВыберите шаблон для редактирования:'
        markup = InlineKeyboardMarkup(row_width=1)
        
        template_names = {
            'smm_new_order_fp': 'Сообщение в FunPay при новом заказе SMM',
            'order_link_confirmation_fp': 'Подтверждение заказа после получения ссылки',
            'order_status_check_fp': 'Информация о статусе заказа',
            'order_refill_success_fp': 'Успешный запрос рефилла',
            'error_invalid_link_fp': 'Ошибка при неправильной ссылке',
            'order_complete_fp': 'Сообщение о завершении накрутки',
            'automatic_refund_message_fp': 'Сообщение клиенту при автоматическом возврате',
            'manual_refund_message_fp': 'Сообщение клиенту при ручном возврате'
        }
        
        for k, v in template_names.items():
            if k in customer_messages:
                markup.add(InlineKeyboardButton(text=v, callback_data=f"edit_template_{k}"))
                
        markup.add(InlineKeyboardButton(text='🔄 Сбросить все шаблоны', callback_data='reset_all_templates'))
        markup.add(InlineKeyboardButton(text='⬅️ Вернуться назад', callback_data='smm_return_to_settings'))
        
        self.bot.send_message(call.message.chat.id, msg_text, parse_mode='HTML', reply_markup=markup)

    def reset_all_templates(self, call: types.CallbackQuery) -> None:
        logger.info(f"{LOGGER_PREFIX} Сброс всех шаблонов сообщений...")
        try:
            self.bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
            
        config_data = ConfigManager.load_config()
        
        default_templates = {
            'smm_new_order_fp': '\n❤️ Благодарим за оплату! Накрутка будет выполнена автоматически.\n    ∟ 🛍️ Лот: {order_description}\n    ∟ 🔢 Количество: {total_amount}\n\n📌 Важно:\n    ∟ ℹ️ Чтобы активировать накрутку, пожалуйста, предоставьте ссылку в формате: https://url_service. Без корректной ссылки накрутка не будет проведена!\n',
            'order_link_confirmation_fp': '\n🎉 Ваш заказ успешно оформлен!\n    ∟ ✅ Номер заказа: {order_id_smm}\n    ∟ 🔗 Отслеживайте статус здесь: {link}\n\n📋 Основные команды:\n    ∟ 📍 Проверить статус заказа: чек {order_id_smm}',
            'order_status_check_fp': '\n📊 Информация о заказе {order_id}:\n    ∟ 📌 Статус: {status_emoji} {status}\n    ∟ 💰 Цена: {charge}₽\n    ∟ ⏳ Обновлено: {last_update}\n',
            'order_refill_success_fp': '\n✅ Рефилл для заказа {order_id} успешно запрошен!\n',
            'error_invalid_link_fp': '❌ Недопустимая ссылка. Поддерживаются только ссылки на VK, Telegram, Instagram и TikTok.',
            'order_complete_fp': '\n✅ Накрутка по заказу {order_id} завершена!\n    ∟ 🔍 Просьба проверить результат\n    ∟ 👍 Подтвердите получение заказа: {order_url}\n    ∟ 🌟 Спасибо за использование нашего сервиса!\n\nВыполнил провайдер: neversmm.ru\n',
            'error_id_not_found_fp': '\n❌ Ошибка: заказ с ID {order_id} не найден\n    ∟ 🔍 Пожалуйста, проверьте правильность ID\n    ∟ 📝 Формат команды: чек [ID заказа]\n',
            'automatic_refund_message_fp': '❌ Извините, не можем осуществить заказ. Средства автоматически возвращены на ваш баланс. Приносим извинения за неудобства.',
            'manual_refund_message_fp': '❌ Извините, не можем осуществить заказ. Средства будут возвращены в течение 24 часов. Приносим извинения за неудобства.'
        }
        
        config_data['customer_messages'] = default_templates
        
        legacy_keys = [
            'smm_new_order_tg_notification', 'regular_new_order_tg_notification',
            'order_link_tg_details', 'error_link_not_buyer_fp',
            'error_order_already_processed_fp', 'error_min_amount_fp',
            'error_api_link_format_fp'
        ]
        
        for k in legacy_keys:
            if k in config_data['customer_messages']:
                logger.info(f"{LOGGER_PREFIX} Удаление шаблона: {k}")
                del config_data['customer_messages'][k]
                
        ConfigManager.save_config(config_data)
        
        self.order_processor.messages = config_data.get('customer_messages', {})
        self.order_processor.config = config_data
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(text='⬅️ Назад к шаблонам', callback_data='message_templates'))
        
        self.bot.send_message(call.message.chat.id, '✅ Все шаблоны сообщений были сброшены до значений по умолчанию.', reply_markup=markup)

    def edit_template(self, call: types.CallbackQuery) -> None:
        template_key = call.data[len('edit_template_'):]
        logger.info(f"{LOGGER_PREFIX} Редактирование шаблона: {template_key}")
        
        try:
            self.bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
            
        self.bot.clear_step_handler_by_chat_id(call.message.chat.id)
        
        config_data = ConfigManager.load_config()
        customer_messages = config_data.get('customer_messages', {})
        
        if template_key not in customer_messages:
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton(text='⬅️ Назад к шаблонам', callback_data='message_templates'))
            self.bot.send_message(call.message.chat.id, f"❌ Ошибка: шаблон '{template_key}' не найден в конфигурации.", reply_markup=markup)
            return
            
        current_template = customer_messages.get(template_key, '')
        
        vars_mapping = {
            'smm_new_order_fp': 'Доступные переменные:\n{order_description} - описание заказа\n{total_amount} - общее количество',
            'order_link_confirmation_fp': 'Доступные переменные:\n{order_id_smm} - ID заказа в SMM сервисе\n{link} - ссылка пользователя',
            'order_status_check_fp': 'Доступные переменные:\n{order_id} - ID заказа\n{status_emoji} - эмодзи статуса\n{status} - статус\n{charge} - стоимость\n{last_update} - время последнего обновления',
            'order_refill_success_fp': 'Доступные переменные:\n{order_id} - ID заказа',
            'error_invalid_link_fp': 'Нет переменных',
            'order_complete_fp': 'Доступные переменные:\n{order_id} - ID заказа\n{order_url} - ссылка на заказ FunPay',
            'error_id_not_found_fp': 'Доступные переменные:\n{order_id} - ID заказа, который не найден',
            'automatic_refund_message_fp': 'Нет переменных',
            'manual_refund_message_fp': 'Нет переменных'
        }
        
        vars_text = vars_mapping.get(template_key, 'Нет данных о переменных')
        
        current_repr = repr(current_template).replace('\\n', '\n')
        
        msg_text = f"📝 <b>Редактирование шаблона</b>\n\n<b>Имя шаблона:</b> <code>{template_key}</code>\n\n<b>Текущий шаблон (код):</b>\n<code>{current_repr}</code>\n\n<b>Как выглядит в чате:</b>\n{current_template}\n\n<b>{vars_text}</b>\n\n⚠️ <b>Важно:</b>\n- Для переноса строки вы можете использовать:\n  1. Символы <code>\\n</code> (для хранения в шаблоне)\n  2. Просто нажать Enter (система автоматически преобразует)\n- При отправке сообщений <code>\\n</code> будут заменены на настоящие переносы строк\n- <b>НЕ ИСПОЛЬЗУЙТЕ символ '/' в начале шаблона</b> - это будет распознано как команда!\n\nОтправьте новый текст шаблона:\n"
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(text='❌ Отменить', callback_data='message_templates'))
        
        if not hasattr(self, 'user_editing_templates'):
            self.user_editing_templates = {}
            
        self.user_editing_templates[call.message.chat.id] = template_key
        
        msg = self.bot.send_message(call.message.chat.id, msg_text, parse_mode='HTML', reply_markup=markup)
        self.bot.register_next_step_handler(msg, self.process_new_template, template_key)

    def process_new_template(self, message: types.Message, template_key: str) -> None:
        new_text = message.text.strip()
        
        if hasattr(self, 'user_editing_templates') and message.chat.id in self.user_editing_templates:
            del self.user_editing_templates[message.chat.id]
            
        self.bot.clear_step_handler_by_chat_id(message.chat.id)
        
        if not new_text:
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton(text='⬅️ Назад к шаблонам', callback_data='message_templates'))
            self.bot.send_message(message.chat.id, '❌ Шаблон не может быть пустым. Редактирование отменено.', reply_markup=markup)
            return
            
        if new_text.startswith('/'):
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton(text='⬅️ Назад к шаблонам', callback_data='message_templates'))
            self.bot.send_message(message.chat.id, "❌ Ошибка: текст, начинающийся с '/' распознается как команда и не может быть сохранен как шаблон.", reply_markup=markup)
            return
            
        if '{' not in new_text and '}' not in new_text and 'customer_messages' in new_text and ']' in new_text:
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton(text='⬅️ Назад к шаблонам', callback_data='message_templates'))
            self.bot.send_message(message.chat.id, '❌ Ошибка: похоже, вы пытаетесь сохранить JSON-данные в качестве шаблона. Пожалуйста, введите только текст шаблона.', reply_markup=markup)
            return
            
        if '\n' in new_text and '\\n' not in new_text:
            new_text = new_text.replace('\n', '\\n')
            
        new_text = new_text.replace('\\\\n', '\\n')
        
        config_data = ConfigManager.load_config()
        if 'customer_messages' not in config_data:
            config_data['customer_messages'] = {}
            
        config_data['customer_messages'][template_key] = new_text
        ConfigManager.save_config(config_data)
        
        self.order_processor.messages = config_data.get('customer_messages', {})
        self.order_processor.config = config_data
        
        ConfigManager.load_config()
        
        saved_text = config_data.get('customer_messages', {}).get(template_key, '')
        preview = saved_text.replace('\\n', '\n')
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(text='⬅️ Назад к шаблонам', callback_data='message_templates'))
        
        success_msg = f"✅ Шаблон успешно обновлен и активирован!\n\n<b>Сохраненный шаблон:</b>\n<code>{saved_text}</code>\n\n<b>Как будет выглядеть:</b>\n{preview}"
        
        try:
            self.bot.send_message(message.chat.id, success_msg, parse_mode='HTML', reply_markup=markup)
        except Exception as e:
            logger.error(f"{LOGGER_PREFIX} Ошибка при сохранении шаблона: {e}")
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton(text='⬅️ Назад к шаблонам', callback_data='message_templates'))
            self.bot.send_message(message.chat.id, f"❌ Ошибка при сохранении шаблона: {str(e)}", reply_markup=markup)


    def toggle_notifications(self, call: types.CallbackQuery) -> None:
        logger.info(f"{LOGGER_PREFIX} Переключение статуса уведомлений в Telegram...")
        try:
            self.bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
            
        config_data = ConfigManager.load_config()
        current_status = config_data.get('telegram_notifications', False)
        
        config_data['telegram_notifications'] = not current_status
        ConfigManager.save_config(config_data)
        
        status_text = 'включены' if config_data['telegram_notifications'] else 'отключены'
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(text='⬅️ Назад к настройкам', callback_data='smm_return_to_settings'))
        
        self.bot.send_message(call.message.chat.id, f"✅ Уведомления в Telegram {status_text}.", reply_markup=markup)


    def toggle_refund_type(self, call: types.CallbackQuery) -> None:
        logger.info(f"{LOGGER_PREFIX} Переключение типа возврата...")
        try:
            self.bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
            
        config_data = ConfigManager.load_config()
        current_type = config_data.get('refund_type', 'automatic')
        
        config_data['refund_type'] = 'manual' if current_type == 'automatic' else 'automatic'
        ConfigManager.save_config(config_data)
        
        self.order_processor.config = config_data
        
        type_text = 'автоматический' if config_data['refund_type'] == 'automatic' else 'ручной'
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(text='⬅️ Назад к настройкам', callback_data='smm_return_to_settings'))
        
        self.bot.send_message(call.message.chat.id, f"✅ Тип возврата изменен на {type_text}.", reply_markup=markup)


def calculate_profit_statistics():
    logger.info(f"{LOGGER_PREFIX} Расчет статистики прибыли...")
    
    stats = {
        'total': {'profit': 0, 'fee': 0, 'count': 0},
        'day': {'profit': 0, 'fee': 0, 'count': 0},
        'week': {'profit': 0, 'fee': 0, 'count': 0},
        'month': {'profit': 0, 'fee': 0, 'count': 0}
    }
    
    try:
        orders = SMMUtils.load_orders_data()
        now = datetime.now()
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_of_week = start_of_day - timedelta(days=7)
        start_of_month = start_of_day - timedelta(days=30)
        
        for order in orders:
            if order.get('status', '') != 'Completed':
                continue
                
            clean_profit = float(order.get('clean_profit', 0))
            if clean_profit <= 0:
                continue
                
            fee = clean_profit * 0.03
            
            stats['total']['profit'] += clean_profit
            stats['total']['fee'] += fee
            stats['total']['count'] += 1
            
        return stats
    except Exception as e:
        logger.error(f"{LOGGER_PREFIX} Ошибка при расчете статистики: {e}")
        return stats


_plugin_instance = None


def init_plugin(cardinal: Cardinal) -> None:
    global _plugin_instance
    
    logger.info(f"{LOGGER_PREFIX} Инициализация плагина...")
    
    SMMUtils.init_db()
    
    _plugin_instance = SMMPlugin(cardinal)
    
    cardinal.add_telegram_commands(UUID, [
        ('start_smm', 'активирует автопродажу накрутки', True),
        ('stop_smm', 'отключает автопродажу накрутки', True),
        ('auto_smm_settings', 'настройки автопродажи накрутки', True)
    ])
    
    cardinal.telegram.msg_handler(_plugin_instance.telegram_handler.start_smm, commands=['start_smm'])
    cardinal.telegram.msg_handler(_plugin_instance.telegram_handler.stop_smm, commands=['stop_smm'])
    cardinal.telegram.msg_handler(_plugin_instance.telegram_handler.auto_smm_settings, commands=['auto_smm_settings'])
    
    _plugin_instance.telegram_handler.register_handlers()
    
    _plugin_instance.order_processor.running = True
    _plugin_instance.order_processor.start_order_checking()
    
    logger.info(f"{LOGGER_PREFIX} Плагин инициализирован и активирован.")


def message_handler(cardinal: Cardinal, event: NewMessageEvent) -> None:
    if _plugin_instance:
        _plugin_instance.handle_message(event)


def order_handler(cardinal: Cardinal, event: NewOrderEvent) -> None:
    if _plugin_instance:
        _plugin_instance.handle_new_order(event)


def delete_plugin() -> None:
    global _plugin_instance
    logger.info(f"{LOGGER_PREFIX} Выгрузка плагина...")
    if _plugin_instance and _plugin_instance.order_processor:
        _plugin_instance.order_processor.running = False
    _plugin_instance = None


class SMMPlugin:
    def __init__(self, cardinal: Cardinal):
        self.cardinal = cardinal
        self.config = ConfigManager.load_config()
        self.order_processor = OrderProcessor(cardinal, self.config)
        self.telegram_handler = TelegramHandler(cardinal, self.order_processor)

    def handle_message(self, event: NewMessageEvent) -> None:
        if event.message.author_id != self.cardinal.account.id:
            if '/check_activation' in event.message.text:
                pass
            else:
                self.order_processor.message_handler(event)

    def handle_new_order(self, event: NewOrderEvent) -> None:
        self.order_processor.new_order_handler(event)


BIND_TO_PRE_INIT = [init_plugin]
BIND_TO_NEW_MESSAGE = [message_handler]
BIND_TO_NEW_ORDER = [order_handler]
BIND_TO_DELETE = [delete_plugin]