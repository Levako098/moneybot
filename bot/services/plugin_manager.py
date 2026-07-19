from __future__ import annotations

import ast
import configparser
import importlib.util
import json
import logging
import re
import sys
import threading
import time
import uuid as uuid_module
from datetime import datetime
from types import MethodType
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import telebot
import requests
from FunPayAPI import Account, Runner
from FunPayAPI import types as funpay_types
from FunPayAPI.common import exceptions as funpay_exceptions
from FunPayAPI.updater import events as funpay_events
from bs4 import BeautifulSoup
from telebot.types import CallbackQuery as TeleCallbackQuery
from telebot.types import Message as TeleMessage

from bot.compat import tg_bot as tg_bot_compat
from bot.compat import Utils as utils_compat
from bot.compat import locales as locales_compat
from bot.compat.Utils import cardinal_tools as cardinal_tools_compat
from bot.compat.Utils import exceptions as utils_exceptions_compat
from bot.compat.locales import localizer as localizer_compat
from bot.compat.tg_bot import bot as tg_bot_bot_compat
from bot.compat.tg_bot import keyboards as tg_keyboards_compat
from bot.compat.tg_bot import static_keyboards as tg_static_keyboards_compat
from bot.compat.tg_bot import utils as tg_utils_compat
from bot.version import __version__


logger = logging.getLogger("moneybot.plugins")
ROOT = Path(__file__).resolve().parents[2]
PLUGINS_DIR = ROOT / "plugins"
STATE_FILE = Path(__file__).resolve().parent.parent / "data" / "plugins.json"


@dataclass
class PluginRecord:
    uuid: str
    name: str
    version: str
    description: str
    credits: str
    path: Path
    enabled: bool = False
    duplicate: bool = False
    loaded: bool = False
    settings_page: bool = False
    pinned: bool = False
    delete_handler: Any = None
    error: str = ""
    module: ModuleType | None = None
    commands: dict[str, str] = field(default_factory=dict)

    @property
    def plugin(self) -> ModuleType | None:
        return self.module


class CompatTeleBot(telebot.TeleBot):
    def __init__(self, token: str, manager: "PluginManager") -> None:
        self.manager = manager
        super().__init__(token, threaded=False)

    def message_handler(self, *args: Any, **kwargs: Any):
        parent = super().message_handler(*args, **kwargs)
        plugin_uuid = self.manager.loading_uuid

        def decorator(handler: Callable):
            def guarded(message: Any):
                if not plugin_uuid or self.manager.is_enabled(plugin_uuid):
                    return handler(message)
                return None

            return parent(guarded)

        return decorator

    def callback_query_handler(self, *args: Any, **kwargs: Any):
        parent = super().callback_query_handler(*args, **kwargs)
        plugin_uuid = self.manager.loading_uuid

        def decorator(handler: Callable):
            def guarded(call: Any):
                if not plugin_uuid or self.manager.is_enabled(plugin_uuid):
                    return handler(call)
                return None

            return parent(guarded)

        return decorator

    def middleware_handler(self, *args: Any, **kwargs: Any):
        parent = super().middleware_handler(*args, **kwargs)
        plugin_uuid = self.manager.loading_uuid

        def decorator(handler: Callable):
            def guarded(update: Any):
                if not plugin_uuid or self.manager.is_enabled(plugin_uuid):
                    return handler(update)
                return None

            return parent(guarded)

        return decorator


class CompatTelegram:
    def __init__(self, token: str, owner_id: int, manager: "PluginManager") -> None:
        self.manager = manager
        self.admin_ids = [owner_id]
        self.bot = CompatTeleBot(token, manager)
        self.user_states: dict[int, dict[int, dict[str, Any]]] = {}
        self.file_handlers: dict[str, Callable] = {}
        self.notification_settings: dict[str, dict[str, bool]] = {
            str(owner_id): {}
        }
        self.answer_templates: list[str] = []
        self.authorized_users: dict[int, dict[str, Any]] = {owner_id: {}}
        self.commands: dict[str, str] = {}

    def msg_handler(self, handler: Callable, **kwargs: Any) -> None:
        self.bot.message_handler(**kwargs)(handler)

    def cbq_handler(self, handler: Callable, func: Callable, **kwargs: Any) -> None:
        self.bot.callback_query_handler(func=func, **kwargs)(handler)

    def mdw_handler(self, handler: Callable, **kwargs: Any) -> None:
        self.bot.middleware_handler(**kwargs)(handler)

    def is_file_handler(self, message: Any) -> bool:
        state = self.get_state(message.chat.id, message.from_user.id)
        return bool(state and message.content_type in {"photo", "document"})

    def file_handler(self, state: str, handler: Callable) -> None:
        self.file_handlers[state] = handler

    def run_file_handlers(self, message: Any) -> None:
        state = self.get_state(message.chat.id, message.from_user.id)
        if state and state.get("state") in self.file_handlers:
            self.file_handlers[state["state"]](message)

    def get_state(self, chat_id: int, user_id: int) -> dict[str, Any] | None:
        return self.user_states.get(chat_id, {}).get(user_id)

    def set_state(
        self,
        chat_id: int,
        message_id: int,
        user_id: int,
        state: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.user_states.setdefault(chat_id, {})[user_id] = {
            "state": state,
            "mid": message_id,
            "data": data or {},
        }

    def clear_state(self, chat_id: int, user_id: int, del_msg: bool = False) -> int | None:
        state = self.user_states.get(chat_id, {}).pop(user_id, None)
        if not state:
            return None
        if del_msg:
            try:
                self.bot.delete_message(chat_id, state["mid"])
            except Exception:
                pass
        return state.get("mid")

    def check_state(self, chat_id: int, user_id: int, state: str) -> bool:
        current = self.get_state(chat_id, user_id)
        return bool(current and current.get("state") == state)

    def is_notification_enabled(
        self, chat_id: int | str, notification_type: str
    ) -> bool:
        settings = self.notification_settings.get(str(chat_id), {})
        return settings.get(notification_type, True)

    def toggle_notification(self, chat_id: int, notification_type: str) -> bool:
        settings = self.notification_settings.setdefault(str(chat_id), {})
        settings[notification_type] = not self.is_notification_enabled(
            chat_id, notification_type
        )
        return settings[notification_type]

    def send_notification(
        self,
        text: str | None,
        keyboard: Any = None,
        notification_type: str = tg_utils_compat.NotificationTypes.other,
        photo: bytes | None = None,
        pin: bool = False,
    ) -> None:
        for chat_id in self.admin_ids:
            if not self.is_notification_enabled(chat_id, notification_type):
                continue
            kwargs = {"reply_markup": keyboard} if keyboard is not None else {}
            if photo is not None:
                message = self.bot.send_photo(chat_id, photo, caption=text, **kwargs)
            else:
                message = self.bot.send_message(chat_id, text or "", **kwargs)
            if pin:
                self.bot.pin_chat_message(chat_id, message.id)

    def add_command_to_menu(self, command: str, help_text: str) -> None:
        command = command.lstrip("/")
        self.commands[command] = help_text
        uuid = self.manager.loading_uuid
        if uuid and uuid in self.manager.plugins:
            self.manager.plugins[uuid].commands[command] = help_text

    def setup_commands(self) -> None:
        return None


class PluginManager:
    def __init__(self, token: str, owner_id: int, golden_key: str) -> None:
        PluginManager.instance = self
        self.VERSION = __version__
        self.instance_id = int(time.time() * 1000) % 1_000_000_000
        self.start_time = int(time.time())
        self.run_id = 0
        self.running = False
        self.owner_id = owner_id
        self.golden_key = golden_key
        self.loading_uuid: str | None = None
        self.plugins: dict[str, PluginRecord] = {}
        self.pre_init_handlers: list[Callable] = []
        self.post_init_handlers: list[Callable] = []
        self.pre_start_handlers: list[Callable] = []
        self.post_start_handlers: list[Callable] = []
        self.pre_stop_handlers: list[Callable] = []
        self.post_stop_handlers: list[Callable] = []
        self.init_message_handlers: list[Callable] = []
        self.messages_list_changed_handlers: list[Callable] = []
        self.last_chat_message_changed_handlers: list[Callable] = []
        self.new_message_handlers: list[Callable] = []
        self.init_order_handlers: list[Callable] = []
        self.orders_list_changed_handlers: list[Callable] = []
        self.new_order_handlers: list[Callable] = []
        self.order_status_changed_handlers: list[Callable] = []
        self.pre_delivery_handlers: list[Callable] = []
        self.post_delivery_handlers: list[Callable] = []
        self.pre_lots_raise_handlers: list[Callable] = []
        self.post_lots_raise_handlers: list[Callable] = []
        self.init_handlers: list[Callable] = []
        self.exit_handlers: list[Callable] = []
        self.handler_bind_var_names: dict[str, list[Callable]] = {
            "BIND_TO_PRE_INIT": self.pre_init_handlers,
            "BIND_TO_INIT": self.init_handlers,
            "BIND_TO_POST_INIT": self.post_init_handlers,
            "BIND_TO_PRE_START": self.pre_start_handlers,
            "BIND_TO_POST_START": self.post_start_handlers,
            "BIND_TO_PRE_STOP": self.pre_stop_handlers,
            "BIND_TO_POST_STOP": self.post_stop_handlers,
            "BIND_TO_EXIT": self.exit_handlers,
            "BIND_TO_INIT_MESSAGE": self.init_message_handlers,
            "BIND_TO_MESSAGES_LIST_CHANGED": self.messages_list_changed_handlers,
            "BIND_TO_LAST_CHAT_MESSAGE_CHANGED": self.last_chat_message_changed_handlers,
            "BIND_TO_NEW_MESSAGE": self.new_message_handlers,
            "BIND_TO_INIT_ORDER": self.init_order_handlers,
            "BIND_TO_ORDERS_LIST_CHANGED": self.orders_list_changed_handlers,
            "BIND_TO_NEW_ORDER": self.new_order_handlers,
            "BIND_TO_ORDER_STATUS_CHANGED": self.order_status_changed_handlers,
            "BIND_TO_PRE_DELIVERY": self.pre_delivery_handlers,
            "BIND_TO_POST_DELIVERY": self.post_delivery_handlers,
            "BIND_TO_PRE_LOTS_RAISE": self.pre_lots_raise_handlers,
            "BIND_TO_POST_LOTS_RAISE": self.post_lots_raise_handlers,
        }
        self.funpay_message_observers: list[Callable] = []
        self.funpay_order_observers: list[Callable] = []
        self.telegram = CompatTelegram(token, owner_id, self)
        self.account: Account | None = None
        self.runner: Runner | None = None
        self.profile = None
        self.curr_profile = None
        self.tg_profile = None
        self.last_tg_profile_update = datetime.now()
        self.balance = None
        self.blacklist = cardinal_tools_compat.load_blacklist()
        self.old_users: dict[int, float] = {}
        self.raise_time: dict[int, float] = {}
        self.raised_time: dict[int, float] = {}
        self._exchange_rates: dict[tuple[Any, Any], tuple[float, float]] = {}
        self.delivery_tests: dict[str, str] = {}
        self.proxy: dict[str, str] = {}
        self._runner_started = False
        self._runner_allowed = False
        self._shutdown_done = False
        self.connection_observer: Callable[[bool, Exception | None], None] | None = None
        self._enabled_saved = self._load_enabled()
        self._pinned_saved = self._load_pinned()
        self.MAIN_CFG = self._build_main_config(token, owner_id, golden_key)
        self.AD_CFG = configparser.ConfigParser(interpolation=None)
        self.AR_CFG = configparser.ConfigParser(interpolation=None)
        self.RAW_AR_CFG = configparser.ConfigParser(interpolation=None)
        self._install_module_compatibility()
        if str(PLUGINS_DIR) not in sys.path:
            sys.path.append(str(PLUGINS_DIR))
        self.scan()

    @staticmethod
    def _build_main_config(
        token: str, owner_id: int, golden_key: str
    ) -> configparser.ConfigParser:
        config = configparser.ConfigParser(interpolation=None)
        config.optionxform = str
        config["FunPay"] = {
            "golden_key": golden_key,
            "user_agent": "",
            "autoRaise": "0",
            "autoResponse": "0",
            "autoDelivery": "0",
            "multiDelivery": "0",
            "autoRestore": "0",
            "autoDisable": "0",
            "oldMsgGetMode": "0",
            "keepSentMessagesUnread": "0",
            "locale": "ru",
        }
        config["Telegram"] = {
            "enabled": "1",
            "token": token,
            "admin_id": str(owner_id),
            "proxy": "",
            "blockLogin": "1",
        }
        config["BlockList"] = {
            "blockDelivery": "0",
            "blockResponse": "0",
            "blockNewMessageNotification": "0",
            "blockNewOrderNotification": "0",
            "blockCommandNotification": "0",
        }
        config["NewMessageView"] = {
            "includeMyMessages": "1",
            "includeFPMessages": "1",
            "includeBotMessages": "1",
            "notifyOnlyMyMessages": "0",
            "notifyOnlyFPMessages": "0",
            "notifyOnlyBotMessages": "0",
            "showImageName": "1",
        }
        config["Greetings"] = {
            "ignoreSystemMessages": "0",
            "onlyNewChats": "0",
            "sendGreetings": "0",
            "greetingsText": "",
            "greetingsCooldown": "2",
        }
        config["OrderConfirm"] = {"watermark": "0", "sendReply": "0", "replyText": ""}
        config["ReviewReply"] = {
            **{f"star{stars}Reply": "0" for stars in range(1, 6)},
            **{f"star{stars}ReplyText": "" for stars in range(1, 6)},
        }
        config["Proxy"] = {"enable": "0", "proxy": "", "check": "0"}
        config["Other"] = {"watermark": "", "requestsDelay": "4", "language": "ru"}
        return config

    def _install_module_compatibility(self) -> None:
        cardinal_module = ModuleType("cardinal")
        cardinal_module.Cardinal = PluginManager
        cardinal_module.PluginData = PluginRecord
        cardinal_module.get_cardinal = lambda: self
        tg_bot_bot_compat.TGBot = CompatTelegram
        tg_bot_bot_compat.TgBot = CompatTelegram
        tg_bot_compat.TGBot = CompatTelegram
        tg_bot_compat.TgBot = CompatTelegram
        sys.modules["cardinal"] = cardinal_module
        sys.modules["tg_bot"] = tg_bot_compat
        sys.modules["tg_bot.CBT"] = tg_bot_compat.CBT
        sys.modules["tg_bot.bot"] = tg_bot_bot_compat
        sys.modules["tg_bot.utils"] = tg_utils_compat
        sys.modules["tg_bot.keyboards"] = tg_keyboards_compat
        sys.modules["tg_bot.static_keyboards"] = tg_static_keyboards_compat
        sys.modules["Utils"] = utils_compat
        sys.modules["Utils.cardinal_tools"] = cardinal_tools_compat
        sys.modules["Utils.exceptions"] = utils_exceptions_compat
        sys.modules["locales"] = locales_compat
        sys.modules["locales.localizer"] = localizer_compat

    def _load_enabled(self) -> set[str]:
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            return set(data.get("enabled", []))
        except (OSError, ValueError, TypeError):
            return set()

    def _load_pinned(self) -> set[str]:
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            return set(data.get("pinned", []))
        except (OSError, ValueError, TypeError):
            return set()

    def _save_enabled(self) -> None:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._enabled_saved = {
            item.uuid for item in self.plugins.values() if item.enabled
        }
        self._pinned_saved = {
            item.uuid for item in self.plugins.values() if item.pinned
        }
        enabled = sorted(self._enabled_saved)
        pinned = sorted(self._pinned_saved)
        STATE_FILE.write_text(
            json.dumps(
                {"enabled": enabled, "pinned": pinned},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _version_key(value: str) -> tuple[int, ...]:
        numbers = re.findall(r"\d+", value)
        return tuple(int(x) for x in numbers) or (0,)

    @staticmethod
    def _metadata(path: Path) -> PluginRecord:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        values: dict[str, Any] = {}
        constants: dict[str, Any] = {}
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {
                    "UUID", "NAME", "VERSION", "DESCRIPTION", "CREDITS",
                    "SETTINGS_PAGE",
                }:
                    try:
                        values[target.id] = ast.literal_eval(node.value)
                    except Exception:
                        pass
                if isinstance(target, ast.Name):
                    try:
                        constants[target.id] = ast.literal_eval(node.value)
                    except Exception:
                        pass
        uuid = str(values.get("UUID") or "")
        if not uuid:
            raise ValueError("отсутствует UUID")
        record = PluginRecord(
            uuid=uuid,
            name=str(values.get("NAME") or path.stem),
            version=str(values.get("VERSION") or "0"),
            description=str(values.get("DESCRIPTION") or ""),
            credits=str(values.get("CREDITS") or ""),
            path=path,
            settings_page=bool(values.get("SETTINGS_PAGE", False)),
        )
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or len(node.args) < 2:
                continue
            if not isinstance(node.func, ast.Attribute) or node.func.attr != "add_telegram_commands":
                continue
            def evaluate(value: ast.AST):
                if isinstance(value, ast.Name) and value.id in constants:
                    return constants[value.id]
                if isinstance(value, (ast.List, ast.Tuple)):
                    return [evaluate(item) for item in value.elts]
                return ast.literal_eval(value)

            try:
                commands = evaluate(node.args[1])
            except Exception:
                continue
            for item in commands:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    record.commands[str(item[0])] = str(item[1])
        return record

    def scan(self) -> None:
        PLUGINS_DIR.mkdir(exist_ok=True)
        candidates: list[PluginRecord] = []
        for path in sorted(PLUGINS_DIR.glob("*.py")):
            try:
                candidates.append(self._metadata(path))
            except Exception as error:
                logger.error("Плагин %s не распознан: %s", path.name, error)
        winners: dict[str, PluginRecord] = {}
        for record in candidates:
            key = record.uuid
            current = winners.get(key)
            if current is None or self._version_key(record.version) > self._version_key(current.version):
                if current:
                    current.duplicate = True
                winners[key] = record
            else:
                record.duplicate = True
        by_name: dict[str, PluginRecord] = {}
        for record in winners.values():
            key = record.name.casefold().replace(" ", "")
            current = by_name.get(key)
            if current is None or self._version_key(record.version) > self._version_key(current.version):
                if current:
                    current.duplicate = True
                by_name[key] = record
            else:
                record.duplicate = True
        self.plugins = {
            record.uuid: record
            for record in winners.values()
            if not record.duplicate
        }
        for record in self.plugins.values():
            record.enabled = record.uuid in self._enabled_saved and not record.duplicate
            record.pinned = record.uuid in self._pinned_saved and not record.duplicate

    def install_plugin(self, file_name: str, content: bytes) -> PluginRecord:
        if len(content) > 2 * 1024 * 1024:
            raise ValueError("файл плагина больше 2 МБ")
        safe_name = Path(file_name or "plugin.py").name
        safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", safe_name).strip(". ")
        if not safe_name.lower().endswith(".py"):
            raise ValueError("плагин должен быть Python-файлом с расширением .py")
        if not safe_name:
            raise ValueError("некорректное имя файла")

        PLUGINS_DIR.mkdir(exist_ok=True)
        target = PLUGINS_DIR / safe_name
        if target.exists():
            raise ValueError(f"файл {safe_name} уже существует")
        temporary = PLUGINS_DIR / f".upload-{uuid_module.uuid4().hex}.tmp"
        try:
            temporary.write_bytes(content)
            uploaded = self._metadata(temporary)
            for path in PLUGINS_DIR.glob("*.py"):
                try:
                    existing = self._metadata(path)
                except Exception:
                    continue
                if existing.uuid == uploaded.uuid:
                    raise ValueError(
                        f"плагин с UUID {uploaded.uuid} уже загружен: {path.name}"
                    )
                if existing.name.casefold() == uploaded.name.casefold():
                    raise ValueError(
                        f"плагин с названием {uploaded.name} уже загружен: {path.name}"
                    )
            temporary.replace(target)
            record = self._metadata(target)
            record.enabled = False
            self.plugins[record.uuid] = record
            self._enabled_saved.discard(record.uuid)
            self._save_enabled()
            logger.info("Плагин %s загружен через Telegram", target.name)
            return record
        finally:
            if temporary.exists():
                temporary.unlink()

    def initialize(self) -> None:
        if self.account is not None:
            return
        self.account = Account(self.golden_key).get()
        self._install_account_compatibility()
        self.profile = self.account.get_user(self.account.id)
        self.curr_profile = self.profile
        self.tg_profile = self.profile
        self.last_tg_profile_update = datetime.now()
        for record in list(self.plugins.values()):
            if record.enabled:
                self.enable(record.uuid, save=False)

    def _install_account_compatibility(self) -> None:
        if not self.account:
            return
        response = requests.get(
            "https://funpay.com",
            headers={"cookie": f"golden_key={self.account.golden_key}"},
            timeout=self.account.requests_timeout,
            proxies=self.account.proxy or {},
        )
        fresh_cookies = response.cookies.get_dict()
        self.account.phpsessid = fresh_cookies.get(
            "PHPSESSID", self.account.phpsessid
        )
        self.account.golden_seal = fresh_cookies.get("golden_seal", "")
        parser = BeautifulSoup(response.text, "html.parser")
        body = parser.find("body")
        if body and body.get("data-app-data"):
            self.account.app_data = json.loads(body["data-app-data"])
            self.account.csrf_token = self.account.app_data["csrf-token"]

        def compatible_method(
            account: Account,
            request_method: str,
            api_method: str,
            headers: dict,
            payload: Any,
            exclude_phpsessid: bool = False,
            raise_not_200: bool = False,
            locale: str | None = None,
        ):
            headers = dict(headers or {})
            cookies = [f"golden_key={account.golden_key}"]
            if account.phpsessid and not exclude_phpsessid:
                cookies.append(f"PHPSESSID={account.phpsessid}")
            if getattr(account, "golden_seal", ""):
                cookies.append(f"golden_seal={account.golden_seal}")
            headers["cookie"] = "; ".join(cookies)
            if account.user_agent:
                headers["user-agent"] = account.user_agent
            url = (
                api_method
                if api_method.startswith("https://funpay.com")
                else "https://funpay.com/" + api_method
            )
            if locale and request_method == "get":
                separator = "&" if "?" in url else "?"
                url = f"{url}{separator}setlocale={locale}"
            elif locale and locale != "ru" and request_method == "post":
                prefix = "https://funpay.com/"
                if url.startswith(prefix) and not url.startswith(
                    f"{prefix}{locale}/"
                ):
                    url = f"{prefix}{locale}/{url[len(prefix):]}"
            result = getattr(requests, request_method)(
                url,
                headers=headers,
                data=payload,
                timeout=account.requests_timeout,
                proxies=account.proxy or {},
            )
            response_cookies = result.cookies.get_dict()
            if response_cookies.get("PHPSESSID"):
                account.phpsessid = response_cookies["PHPSESSID"]
            if response_cookies.get("golden_seal"):
                account.golden_seal = response_cookies["golden_seal"]
            if result.status_code == 403:
                raise funpay_exceptions.UnauthorizedError(result)
            if result.status_code != 200 and raise_not_200:
                raise funpay_exceptions.RequestFailedError(result)
            return result

        self.account.method = MethodType(compatible_method, self.account)

        def compatible_parse_messages(
            account: Account,
            json_messages: list[dict[str, Any]],
            chat_id: int | str,
            interlocutor_id: int | None = None,
            interlocutor_username: str | None = None,
            from_id: int = 0,
        ) -> list[Any]:
            messages = []
            authors = {account.id: account.username, 0: "FunPay"}
            badges: dict[int, str | int] = {}
            if interlocutor_id is not None:
                authors[interlocutor_id] = interlocutor_username

            for raw_message in json_messages:
                if raw_message["id"] < from_id:
                    continue
                author_id = raw_message["author"]
                parser = BeautifulSoup(raw_message["html"], "html.parser")
                author_div = parser.find("div", {"class": "media-user-name"})
                if author_div is not None:
                    badge = author_div.find("span")
                    badges[author_id] = badge.get_text(strip=True) if badge else 0
                    author_link = author_div.find("a")
                    if author_link is not None:
                        authors[author_id] = author_link.get_text(strip=True)
                        if (
                            account.chat_id_private(chat_id)
                            and author_id == interlocutor_id
                            and not interlocutor_username
                        ):
                            interlocutor_username = authors[author_id]

                image_node = (
                    parser.find("a", {"class": "chat-img-link"})
                    if account.chat_id_private(chat_id)
                    else None
                )
                if image_node is not None:
                    image_link = image_node.get("href")
                    message_text = None
                else:
                    image_link = None
                    text_node = (
                        parser.find("div", {"class": "message-text"})
                        or parser.find("div", {"class": "chat-msg-text"})
                    )
                    if author_id == 0:
                        text_node = (
                            parser.find(
                                "div", {"class": "alert alert-with-icon alert-info"}
                            )
                            or text_node
                        )
                    message_text = (
                        text_node.get_text(" ", strip=True) if text_node is not None else ""
                    )

                by_bot = False
                if message_text and message_text.startswith(account.bot_character):
                    message_text = message_text.replace(account.bot_character, "", 1)
                    by_bot = True

                message = funpay_types.Message(
                    raw_message["id"],
                    message_text,
                    chat_id,
                    interlocutor_username,
                    None,
                    author_id,
                    raw_message["html"],
                    image_link,
                    determine_msg_type=False,
                )
                message.by_bot = by_bot
                message.type = (
                    funpay_types.MessageTypes.NON_SYSTEM
                    if author_id != 0
                    else message.get_message_type()
                )
                messages.append(message)

            for message in messages:
                message.author = authors.get(message.author_id)
                message.chat_name = interlocutor_username
                badge = badges.get(message.author_id)
                message.badge = badge if badge not in {None, 0} else None
            return messages

        self.account._Account__parse_messages = MethodType(
            compatible_parse_messages, self.account
        )

        def compatible_send_message(
            account: Account,
            chat_id: int | str,
            text: str | None = None,
            chat_name: str | None = None,
            interlocutor_id: int | None = None,
            image_id: int | None = None,
            add_to_ignore_list: bool = True,
            update_last_saved_message: bool = False,
            leave_as_unread: bool = False,
        ) -> Any:
            del interlocutor_id, leave_as_unread
            if not account.is_initiated:
                raise funpay_exceptions.AccountNotInitiatedError()

            headers = {
                "accept": "*/*",
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                "x-requested-with": "XMLHttpRequest",
            }
            content = "" if image_id is not None else (
                f"{account.bot_character}{text}" if text else ""
            )
            request_data: dict[str, Any] = {
                "node": chat_id,
                "last_message": -1,
                "content": content,
            }
            if image_id is not None:
                request_data["image_id"] = image_id
            request = {"action": "chat_message", "data": request_data}
            objects = [
                {
                    "type": "chat_node",
                    "id": chat_id,
                    "tag": "00000000",
                    "data": {
                        "node": chat_id,
                        "last_message": -1,
                        "content": "",
                    },
                }
            ]
            payload = {
                "objects": json.dumps(objects),
                "request": json.dumps(request),
                "csrf_token": account.csrf_token,
            }
            response = account.method(
                "post", "runner/", headers, payload, raise_not_200=True
            )
            json_response = response.json()
            response_data = json_response.get("response")
            if not response_data:
                raise funpay_exceptions.MessageNotDeliveredError(
                    response, None, chat_id
                )
            if (error_text := response_data.get("error")) is not None:
                raise funpay_exceptions.MessageNotDeliveredError(
                    response, error_text, chat_id
                )

            raw_message = json_response["objects"][0]["data"]["messages"][-1]
            message_parser = BeautifulSoup(raw_message["html"], "html.parser")
            image_node = message_parser.find("a", {"class": "chat-img-link"})
            if image_node is not None:
                image_link = image_node.get("href")
                message_text = None
            else:
                image_link = None
                text_node = (
                    message_parser.find("div", {"class": "message-text"})
                    or message_parser.find("div", {"class": "chat-msg-text"})
                )
                message_text = (
                    text_node.get_text(" ", strip=True) if text_node is not None else ""
                ).replace(account.bot_character, "", 1)

            message = funpay_types.Message(
                int(raw_message["id"]),
                message_text,
                chat_id,
                chat_name,
                account.username,
                account.id,
                raw_message["html"],
                image_link,
            )
            message.by_bot = True
            if account.runner and isinstance(chat_id, int):
                if add_to_ignore_list:
                    account.runner.mark_as_by_bot(chat_id, message.id)
                if update_last_saved_message:
                    account.runner.update_last_message(chat_id, message_text)
            return message

        self.account.send_message = MethodType(
            compatible_send_message, self.account
        )
        original_send_image = self.account.send_image

        def compatible_send_image(
            account: Account,
            chat_id: int,
            image: Any,
            chat_name: str | None = None,
            interlocutor_id: int | None = None,
            add_to_ignore_list: bool = True,
            update_last_saved_message: bool = False,
            leave_as_unread: bool = False,
        ) -> Any:
            del account, interlocutor_id, leave_as_unread
            return original_send_image(
                chat_id,
                image,
                chat_name,
                add_to_ignore_list,
                update_last_saved_message,
            )

        self.account.send_image = MethodType(compatible_send_image, self.account)

        def compatible_get_sales(
            account: Account,
            start_from: str | None = None,
            include_paid: bool = True,
            include_closed: bool = True,
            include_refunded: bool = True,
            exclude_ids: list[str] | None = None,
            locale: str | None = None,
            subcategories: dict[str, Any] | None = None,
            sudcategories: dict[str, Any] | None = None,
            **filters: Any,
        ) -> tuple[str | None, list[Any], str, dict[str, Any]]:
            next_id, orders = account.get_sells(
                start_from=start_from,
                include_paid=include_paid,
                include_closed=include_closed,
                include_refunded=include_refunded,
                exclude_ids=exclude_ids,
                **filters,
            )
            known_subcategories = subcategories or sudcategories or {}
            account_locale = getattr(account, "locale", None) or "ru"
            return next_id, orders, locale or account_locale, known_subcategories

        self.account.get_sales = MethodType(compatible_get_sales, self.account)
        if not hasattr(self.account, "get_my_subcategory_lots"):
            def get_my_subcategory_lots(account: Account, subcategory_id: int):
                profile = account.get_user(account.id)
                return [
                    lot for lot in profile.get_lots()
                    if int(lot.subcategory.id) == int(subcategory_id)
                ]

            self.account.get_my_subcategory_lots = MethodType(
                get_my_subcategory_lots, self.account
            )

    def is_enabled(self, uuid: str) -> bool:
        record = self.plugins.get(uuid)
        return bool(record and record.enabled)

    def enable(self, uuid: str, save: bool = True) -> PluginRecord:
        record = self.plugins[uuid]
        if record.duplicate:
            record.error = "дубликат более новой версии"
            return record
        record.error = ""
        try:
            runner_was_started = self._runner_started
            just_loaded = False
            if not record.loaded:
                self.loading_uuid = uuid
                module_name = f"moneybot_plugin_{uuid.replace('-', '_')}"
                spec = importlib.util.spec_from_file_location(module_name, record.path)
                if spec is None or spec.loader is None:
                    raise ImportError("не удалось создать import spec")
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                pydantic_state = self._activate_pydantic_v1()
                try:
                    spec.loader.exec_module(module)
                finally:
                    self._restore_pydantic(pydantic_state)
                try:
                    import pydantic.v1 as pydantic_v1
                    if hasattr(module, "pydantic"):
                        module.pydantic = pydantic_v1
                except ImportError:
                    pass
                record.module = module
                record.loaded = True
                record.delete_handler = getattr(module, "BIND_TO_DELETE", None)
                just_loaded = True
                self._register_module_handlers(module, uuid)
            record.enabled = True
            if just_loaded:
                self.loading_uuid = uuid
                self._run_module_bindings(record.module, ("BIND_TO_PRE_INIT", "BIND_TO_INIT"))
                self._run_module_bindings(record.module, ("BIND_TO_POST_INIT",))
            if just_loaded and runner_was_started:
                self._run_module_bindings(record.module, ("BIND_TO_PRE_START",))
                self._run_module_bindings(record.module, ("BIND_TO_POST_START",))
            if save:
                self._save_enabled()
            self.start_runner()
        except Exception as error:
            record.enabled = False
            record.error = f"{type(error).__name__}: {error}"[:500]
            logger.exception("Ошибка загрузки плагина %s", record.path.name)
        finally:
            self.loading_uuid = None
        return record

    @staticmethod
    def _binding_functions(module: ModuleType | None, name: str) -> list[Callable]:
        if module is None:
            return []
        value = getattr(module, name, None)
        if value is None:
            return []
        if callable(value):
            return [value]
        return [item for item in value if callable(item)]

    def _register_module_handlers(self, module: ModuleType, uuid: str) -> None:
        for name, target in self.handler_bind_var_names.items():
            for handler in self._binding_functions(module, name):
                handler.plugin_uuid = uuid
                if handler not in target:
                    target.append(handler)

    def add_handlers_from_plugin(
        self, plugin: ModuleType, uuid: str | None = None
    ) -> None:
        """Register Cardinal lifecycle and FunPay handlers from a module."""
        for name, target in self.handler_bind_var_names.items():
            for handler in self._binding_functions(plugin, name):
                handler.plugin_uuid = uuid
                if handler not in target:
                    target.append(handler)

    def add_handlers(self) -> None:
        """Register handlers exposed by every plugin module already loaded."""
        for uuid, record in self.plugins.items():
            if record.module is not None:
                self.add_handlers_from_plugin(record.module, uuid)

    def _run_module_bindings(
        self, module: ModuleType | None, names: tuple[str, ...]
    ) -> None:
        for name in names:
            for handler in self._binding_functions(module, name):
                handler(self)

    def run_handlers(self, handlers_list: list[Callable], args: tuple[Any, ...]) -> None:
        for handler in list(handlers_list):
            uuid = getattr(handler, "plugin_uuid", None)
            if uuid is not None and not self.is_enabled(uuid):
                continue
            try:
                handler(*args)
            except Exception:
                logger.exception("Ошибка обработчика плагина %s", uuid or "core")

    @staticmethod
    def _activate_pydantic_v1() -> dict[str, Any]:
        try:
            import pydantic
            import pydantic.v1 as pydantic_v1
        except ImportError:
            return {}
        names = (
            "BaseModel", "Field", "ValidationError", "validator",
            "root_validator", "create_model", "parse_obj_as",
        )
        state = {name: getattr(pydantic, name, None) for name in names}
        state["_module"] = pydantic
        for name in names:
            if hasattr(pydantic_v1, name):
                setattr(pydantic, name, getattr(pydantic_v1, name))
        return state

    @staticmethod
    def _restore_pydantic(state: dict[str, Any]) -> None:
        module = state.pop("_module", None)
        if not module:
            return
        for name, value in state.items():
            if value is not None:
                setattr(module, name, value)

    def disable(self, uuid: str) -> PluginRecord:
        record = self.plugins[uuid]
        record.enabled = False
        self._save_enabled()
        return record

    def toggle(self, uuid: str) -> PluginRecord:
        return self.disable(uuid) if self.is_enabled(uuid) else self.enable(uuid)

    def toggle_plugin(self, uuid: str) -> PluginRecord:
        return self.toggle(uuid)

    def pin_plugin(self, uuid: str) -> PluginRecord:
        record = self.plugins[uuid]
        record.pinned = not record.pinned
        self._save_enabled()
        return record

    def delete_plugin(self, uuid: str) -> Path:
        record = self.plugins[uuid]
        self._run_module_bindings(record.module, ("BIND_TO_DELETE",))
        path = record.path.resolve()
        if path.parent != PLUGINS_DIR.resolve():
            raise ValueError("Путь плагина находится вне каталога plugins")
        path.unlink(missing_ok=True)
        self.plugins.pop(uuid, None)
        self._enabled_saved.discard(uuid)
        self._pinned_saved.discard(uuid)
        self._save_enabled()
        return path

    def shutdown(self) -> None:
        if self._shutdown_done:
            return
        self._shutdown_done = True
        self.run_handlers(self.pre_stop_handlers, (self,))
        self.run_handlers(self.exit_handlers, (self,))
        self.run_handlers(self.post_stop_handlers, (self,))
        self.running = False

    def init(self) -> "PluginManager":
        self.initialize()
        return self

    def run(self) -> "PluginManager":
        self.activate_runner()
        return self

    def start(self) -> "PluginManager":
        self.activate_runner()
        return self

    def stop(self) -> None:
        self.shutdown()

    def add_telegram_commands(self, uuid: str, commands: list[tuple]) -> None:
        record = self.plugins.get(uuid)
        if record:
            for item in commands:
                if len(item) < 2:
                    continue
                command, description = str(item[0]).lstrip("/"), str(item[1])
                record.commands[command] = description
                if len(item) < 3 or bool(item[2]):
                    self.telegram.add_command_to_menu(command, description)

    def add_funpay_message_observer(self, observer: Callable) -> None:
        if observer not in self.funpay_message_observers:
            self.funpay_message_observers.append(observer)

    def add_funpay_order_observer(self, observer: Callable) -> None:
        if observer not in self.funpay_order_observers:
            self.funpay_order_observers.append(observer)

    @staticmethod
    def split_text(text: str) -> list[str]:
        lines = text.splitlines()
        return ["\n".join(lines[index:index + 20]) for index in range(0, len(lines), 20)]

    def parse_message_entities(self, text: str) -> list[str | int | float]:
        result: list[str | int | float] = []
        pattern = re.compile(r"\$(photo|sleep)=([^\s]+)")
        position = 0
        for match in pattern.finditer(text):
            before = text[position:match.start()].strip()
            if before:
                result.extend(self.split_text(before))
            if match.group(1) == "photo":
                result.append(int(match.group(2)))
            else:
                result.append(float(match.group(2)))
            position = match.end()
        tail = text[position:].strip()
        if tail:
            result.extend(self.split_text(tail))
        return result

    def send_message(
        self,
        chat_id: int | str,
        message_text: str,
        chat_name: str | None = None,
        interlocutor_id: int | None = None,
        attempts: int = 3,
        watermark: bool = True,
    ) -> list[Any] | None:
        del interlocutor_id, watermark
        if not self.account:
            raise RuntimeError("FunPay account is not initialized")
        entities = self.parse_message_entities(message_text)
        if not entities:
            return None
        sent = []
        for entity in entities:
            if isinstance(entity, float):
                time.sleep(entity)
                continue
            for attempt in range(max(1, attempts)):
                try:
                    if isinstance(entity, int):
                        message = self.account.send_image(chat_id, entity, chat_name)
                    else:
                        message = self.account.send_message(chat_id, entity, chat_name)
                    sent.append(message)
                    break
                except Exception:
                    if attempt + 1 >= max(1, attempts):
                        logger.exception("Не удалось отправить сообщение плагина в FunPay")
                        return []
                    time.sleep(1)
        return sent

    def get_order_from_object(self, obj: Any, order_id: str | None = None) -> Any:
        if not self.account:
            return None
        candidate = order_id
        if candidate is None:
            candidate = str(getattr(obj, "id", "") or "").lstrip("#")
            if not candidate:
                match = re.search(r"#[A-Z0-9]{6,}", str(obj), re.IGNORECASE)
                candidate = match.group(0).lstrip("#") if match else ""
        if not candidate or candidate == "ADTEST":
            return None
        try:
            order = self.account.get_order(candidate)
            setattr(obj, "_order", order)
            return order
        except Exception:
            logger.exception("Не удалось получить заказ #%s", candidate)
            return None

    def get_exchange_rate(
        self, base_currency: Any, target_currency: Any, min_interval: int = 60
    ) -> float:
        if base_currency == target_currency:
            return 1.0
        cached = self._exchange_rates.get((base_currency, target_currency))
        if cached and time.time() - cached[1] < min_interval:
            return cached[0]
        if not self.account:
            raise RuntimeError("FunPay account is not initialized")
        base_rate, base_reference = self.account.get_exchange_rate(base_currency)
        target_rate, target_reference = self.account.get_exchange_rate(target_currency)
        if base_reference != target_reference:
            raise RuntimeError("FunPay вернул несовместимые базовые валюты")
        result = float(target_rate) / float(base_rate)
        self._exchange_rates[(base_currency, target_currency)] = (result, time.time())
        return result

    def update_session(self, attempts: int = 3) -> bool:
        if not self.account:
            return False
        for _ in range(max(1, attempts)):
            try:
                self.account.get(update_phpsessid=True)
                return True
            except Exception:
                time.sleep(1)
        return False

    def get_rating_restrictions(self) -> list[dict[str, str]]:
        if not self.account:
            raise RuntimeError("FunPay account is not initialized")
        response = self.account.method(
            "get",
            "sras/info",
            {"accept": "text/html,application/xhtml+xml"},
            {},
            raise_not_200=True,
        )
        soup = BeautifulSoup(response.text, "html.parser")
        restrictions = []
        for row in soup.find_all("tr"):
            cells = [
                cell.get_text(" ", strip=True)
                for cell in row.find_all("td")
            ]
            if len(cells) < 2 or not cells[0] or not cells[1]:
                continue
            restrictions.append(
                {"section": cells[0], "restriction": cells[1]}
            )
        return restrictions

    def update_lots_and_categories(self) -> None:
        if self.account:
            self.account.get()
            self.profile = self.account.get_user(self.account.id)
            self.curr_profile = self.profile
            self.tg_profile = self.profile
            self.last_tg_profile_update = datetime.now()

    def switch_msg_get_mode(self) -> bool:
        current = self.MAIN_CFG["FunPay"].getboolean("oldMsgGetMode")
        self.MAIN_CFG["FunPay"]["oldMsgGetMode"] = "0" if current else "1"
        return not current

    @staticmethod
    def save_config(config: configparser.ConfigParser, file_path: str) -> None:
        with open(file_path, "w", encoding="utf-8") as config_file:
            config.write(config_file)

    @property
    def autoraise_enabled(self) -> bool:
        return self.MAIN_CFG["FunPay"].getboolean("autoRaise")

    @property
    def autoresponse_enabled(self) -> bool:
        return self.MAIN_CFG["FunPay"].getboolean("autoResponse")

    @property
    def autodelivery_enabled(self) -> bool:
        return self.MAIN_CFG["FunPay"].getboolean("autoDelivery")

    @property
    def multidelivery_enabled(self) -> bool:
        return self.MAIN_CFG["FunPay"].getboolean("multiDelivery")

    @property
    def autorestore_enabled(self) -> bool:
        return self.MAIN_CFG["FunPay"].getboolean("autoRestore")

    @property
    def autodisable_enabled(self) -> bool:
        return self.MAIN_CFG["FunPay"].getboolean("autoDisable")

    @property
    def old_mode_enabled(self) -> bool:
        return self.MAIN_CFG["FunPay"].getboolean("oldMsgGetMode")

    @property
    def keep_sent_messages_unread(self) -> bool:
        return self.MAIN_CFG["FunPay"].getboolean("keepSentMessagesUnread")

    @property
    def show_image_name(self) -> bool:
        return self.MAIN_CFG["NewMessageView"].getboolean("showImageName")

    def _config_flag(self, section: str, option: str) -> bool:
        return self.MAIN_CFG[section].getboolean(option)

    @property
    def bl_delivery_enabled(self) -> bool:
        return self._config_flag("BlockList", "blockDelivery")

    @property
    def bl_response_enabled(self) -> bool:
        return self._config_flag("BlockList", "blockResponse")

    @property
    def bl_msg_notification_enabled(self) -> bool:
        return self._config_flag("BlockList", "blockNewMessageNotification")

    @property
    def bl_order_notification_enabled(self) -> bool:
        return self._config_flag("BlockList", "blockNewOrderNotification")

    @property
    def bl_cmd_notification_enabled(self) -> bool:
        return self._config_flag("BlockList", "blockCommandNotification")

    @property
    def include_my_msg_enabled(self) -> bool:
        return self._config_flag("NewMessageView", "includeMyMessages")

    @property
    def include_fp_msg_enabled(self) -> bool:
        return self._config_flag("NewMessageView", "includeFPMessages")

    @property
    def include_bot_msg_enabled(self) -> bool:
        return self._config_flag("NewMessageView", "includeBotMessages")

    @property
    def only_my_msg_enabled(self) -> bool:
        return self._config_flag("NewMessageView", "notifyOnlyMyMessages")

    @property
    def only_fp_msg_enabled(self) -> bool:
        return self._config_flag("NewMessageView", "notifyOnlyFPMessages")

    @property
    def only_bot_msg_enabled(self) -> bool:
        return self._config_flag("NewMessageView", "notifyOnlyBotMessages")

    @property
    def block_tg_login(self) -> bool:
        return self._config_flag("Telegram", "blockLogin")

    def raise_lots(self) -> int:
        if not self.account or not self.curr_profile:
            return 10
        categories: dict[int, tuple[Any, set[int]]] = {}
        for lot in self.curr_profile.get_lots():
            subcategory = getattr(lot, "subcategory", None)
            category = getattr(subcategory, "category", None)
            category_id = getattr(category, "id", None)
            subcategory_id = getattr(subcategory, "id", None)
            type_name = str(getattr(getattr(subcategory, "type", None), "name", ""))
            if category_id is None or subcategory_id is None or type_name == "CURRENCY":
                continue
            row = categories.setdefault(int(category_id), (category, set()))
            row[1].add(int(subcategory_id))
        for category_id, (category, subcategories) in categories.items():
            self.run_handlers(self.pre_lots_raise_handlers, (self, category))
            error_text = ""
            try:
                self.account.raise_lots(category_id, sorted(subcategories))
                self.raised_time[category_id] = time.time()
            except Exception as error:
                error_text = str(error).splitlines()[0][:300]
            self.run_handlers(
                self.post_lots_raise_handlers, (self, category, error_text)
            )
        return 10

    def start_runner(self) -> None:
        if not self._runner_allowed or self._runner_started or not self.account:
            return
        self.run_handlers(self.pre_start_handlers, (self,))
        self._runner_started = True
        self.running = True
        self.run_id += 1
        self.runner = Runner(self.account)
        self.account.runner = self.runner
        threading.Thread(target=self._runner_loop, daemon=True, name="funpay-plugins").start()
        self.run_handlers(self.post_start_handlers, (self,))

    def activate_runner(self) -> None:
        self._runner_allowed = True
        self.start_runner()

    def set_connection_observer(
        self, observer: Callable[[bool, Exception | None], None]
    ) -> None:
        self.connection_observer = observer

    def _runner_loop(self) -> None:
        assert self.runner is not None
        event_handlers = {
            funpay_events.EventTypes.INITIAL_CHAT: self.init_message_handlers,
            funpay_events.EventTypes.CHATS_LIST_CHANGED: self.messages_list_changed_handlers,
            funpay_events.EventTypes.LAST_CHAT_MESSAGE_CHANGED: self.last_chat_message_changed_handlers,
            funpay_events.EventTypes.NEW_MESSAGE: self.new_message_handlers,
            funpay_events.EventTypes.INITIAL_ORDER: self.init_order_handlers,
            funpay_events.EventTypes.ORDERS_LIST_CHANGED: self.orders_list_changed_handlers,
            funpay_events.EventTypes.NEW_ORDER: self.new_order_handlers,
            funpay_events.EventTypes.ORDER_STATUS_CHANGED: self.order_status_changed_handlers,
        }
        connected = True
        while True:
            try:
                for event in self.runner.listen(requests_delay=4):
                    if not connected:
                        connected = True
                        if self.connection_observer:
                            self.connection_observer(True, None)
                    name = type(event).__name__
                    if name == "NewMessageEvent":
                        for observer in list(self.funpay_message_observers):
                            try:
                                observer(event)
                            except Exception:
                                logger.exception("Ошибка observer сообщения FunPay")
                    if name == "NewOrderEvent":
                        for observer in list(self.funpay_order_observers):
                            try:
                                observer(event)
                            except Exception:
                                logger.exception("Ошибка observer заказа FunPay")
                    handlers = event_handlers.get(getattr(event, "type", None), [])
                    self.run_handlers(handlers, (self, event))
            except Exception as error:
                if connected:
                    connected = False
                    if self.connection_observer:
                        self.connection_observer(False, error)
                logger.exception("Ошибка FunPay Runner, повтор через 10 секунд")
                threading.Event().wait(10)

    def dispatch_message(self, message_json: str) -> None:
        message = TeleMessage.de_json(message_json)
        self.telegram.bot.process_new_messages([message])

    def dispatch_callback(self, callback_json: str) -> None:
        callback = TeleCallbackQuery.de_json(callback_json)
        self.telegram.bot.process_new_callback_query([callback])
