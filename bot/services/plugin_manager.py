from __future__ import annotations

import ast
import configparser
import importlib.util
import json
import logging
import re
import sys
import threading
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
from bs4 import BeautifulSoup
from telebot.types import CallbackQuery as TeleCallbackQuery
from telebot.types import Message as TeleMessage

from bot.compat import tg_bot as tg_bot_compat


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
    error: str = ""
    module: ModuleType | None = None
    commands: dict[str, str] = field(default_factory=dict)


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


class CompatTelegram:
    def __init__(self, token: str, owner_id: int, manager: "PluginManager") -> None:
        self.admin_ids = [owner_id]
        self.bot = CompatTeleBot(token, manager)
        self.user_states: dict[int, dict[int, dict[str, Any]]] = {}

    def msg_handler(self, handler: Callable, **kwargs: Any) -> None:
        self.bot.message_handler(**kwargs)(handler)

    def cbq_handler(self, handler: Callable, func: Callable, **kwargs: Any) -> None:
        self.bot.callback_query_handler(func=func, **kwargs)(handler)

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


class PluginManager:
    def __init__(self, token: str, owner_id: int, golden_key: str) -> None:
        self.owner_id = owner_id
        self.golden_key = golden_key
        self.loading_uuid: str | None = None
        self.plugins: dict[str, PluginRecord] = {}
        self.new_message_handlers: list[Callable] = []
        self.new_order_handlers: list[Callable] = []
        self.funpay_message_observers: list[Callable] = []
        self.funpay_order_observers: list[Callable] = []
        self.telegram = CompatTelegram(token, owner_id, self)
        self.account: Account | None = None
        self.runner: Runner | None = None
        self.tg_profile = None
        self._runner_started = False
        self._runner_allowed = False
        self._enabled_saved = self._load_enabled()
        self.MAIN_CFG = configparser.ConfigParser()
        self.MAIN_CFG["Telegram"] = {"admin_id": str(owner_id)}
        sys.modules.setdefault("tg_bot", tg_bot_compat)
        sys.modules.setdefault("tg_bot.CBT", tg_bot_compat.CBT)
        self.scan()

    def _load_enabled(self) -> set[str]:
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            return set(data.get("enabled", []))
        except (OSError, ValueError, TypeError):
            return set()

    def _save_enabled(self) -> None:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        enabled = sorted(x.uuid for x in self.plugins.values() if x.enabled)
        STATE_FILE.write_text(
            json.dumps({"enabled": enabled}, ensure_ascii=False, indent=2),
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
                    "UUID", "NAME", "VERSION", "DESCRIPTION", "CREDITS"
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

    def initialize(self) -> None:
        self.account = Account(self.golden_key).get()
        self._install_account_compatibility()
        self.tg_profile = self.account.get_user(self.account.id)
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
            image_id: int | None = None,
            add_to_ignore_list: bool = True,
            update_last_saved_message: bool = False,
        ) -> Any:
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
        if not hasattr(self.account, "get_sales"):
            self.account.get_sales = self.account.get_sells
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
                for name, target in (
                    ("BIND_TO_NEW_MESSAGE", self.new_message_handlers),
                    ("BIND_TO_NEW_ORDER", self.new_order_handlers),
                ):
                    for handler in getattr(module, name, []) or []:
                        handler.plugin_uuid = uuid
                        if handler not in target:
                            target.append(handler)
                for hook in getattr(module, "BIND_TO_PRE_INIT", []) or []:
                    hook.plugin_uuid = uuid
                    hook(self)
            record.enabled = True
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

    def add_telegram_commands(self, uuid: str, commands: list[tuple[str, str, bool]]) -> None:
        record = self.plugins.get(uuid)
        if record:
            for command, description, _ in commands:
                record.commands[command] = description

    def add_funpay_message_observer(self, observer: Callable) -> None:
        if observer not in self.funpay_message_observers:
            self.funpay_message_observers.append(observer)

    def add_funpay_order_observer(self, observer: Callable) -> None:
        if observer not in self.funpay_order_observers:
            self.funpay_order_observers.append(observer)

    def send_message(self, chat_id: int, text: str) -> Any:
        if not self.account:
            raise RuntimeError("FunPay account is not initialized")
        return self.account.send_message(chat_id, text)

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
            self.tg_profile = self.account.get_user(self.account.id)

    def start_runner(self) -> None:
        if not self._runner_allowed or self._runner_started or not self.account:
            return
        self._runner_started = True
        self.runner = Runner(self.account)
        threading.Thread(target=self._runner_loop, daemon=True, name="funpay-plugins").start()

    def activate_runner(self) -> None:
        self._runner_allowed = True
        self.start_runner()

    def _runner_loop(self) -> None:
        assert self.runner is not None
        while True:
            try:
                for event in self.runner.listen(requests_delay=4):
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
                    handlers = (
                        self.new_message_handlers if "Message" in name else
                        self.new_order_handlers if "Order" in name else []
                    )
                    for handler in list(handlers):
                        uuid = getattr(handler, "plugin_uuid", "")
                        if self.is_enabled(uuid):
                            try:
                                handler(self, event)
                            except Exception:
                                logger.exception("Ошибка event handler плагина %s", uuid)
            except Exception:
                logger.exception("Ошибка FunPay Runner, повтор через 10 секунд")
                threading.Event().wait(10)

    def dispatch_message(self, message_json: str) -> None:
        message = TeleMessage.de_json(message_json)
        self.telegram.bot.process_new_messages([message])

    def dispatch_callback(self, callback_json: str) -> None:
        callback = TeleCallbackQuery.de_json(callback_json)
        self.telegram.bot.process_new_callback_query([callback])
