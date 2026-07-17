# ========= AUTO-INSTALL =========
import subprocess
import sys
import importlib

def _install_dependencies():
    """Устанавливает все необходимые зависимости с улучшенной обработкой ошибок"""
    packages = [
        "psutil>=5.9.4",
        "aiohttp==3.10.2", 
        "yarl>=1.17.0,<2.0.0",
        "pydantic==1.9.0",
        "fake-useragent>=1.4.0",
        "rsa==4.7",
        "beautifulsoup4>=4.11.1",
        "colorama>=0.4.6",
        "requests>=2.28.1",
        "pyTelegramBotAPI==4.15.2",
        "Pillow>=9.3.0",
        "requests-toolbelt>=1.0.0",
        "lxml>=5.3.0",
        "bcrypt>=4.2.0",
        "cryptography>=42.0.8",
        "urllib3>=2.2.2",
        "pysteamauth>=1.1.2",
    ]
    
    # Устанавливаем обычные пакеты с улучшенной обработкой
    for package in packages:
        try:
            print(f"Установка {package}...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", package], 
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"✅ {package} установлен")
        except subprocess.CalledProcessError as e:
            print(f"❌ Ошибка установки {package}: {e}")
            # Пробуем альтернативный способ
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", "--user", package], 
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print(f"✅ {package} установлен (альтернативный способ)")
            except:
                print(f"⚠️ Не удалось установить {package}, продолжаем...")
    
    # Устанавливаем pysteamlib из git с несколькими попытками
    steamlib_success = False
    steamlib_urls = [
        "git+https://github.com/sometastycake/steamlib.git",
        "git+https://github.com/sometastycake/steamlib.git#egg=pysteamlib",
        "git+https://github.com/sometastycake/steamlib.git#egg=steamlib",
    ]
    
    for url in steamlib_urls:
        try:
            print(f"Попытка установки steamlib из {url}...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", url], 
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            steamlib_success = True
            print("✅ steamlib установлен")
            break
        except:
            continue
    
    if not steamlib_success:
        print("⚠️ Не удалось установить steamlib, пробуем с флагами...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "--force-reinstall", 
                                 "git+https://github.com/sometastycake/steamlib.git"], 
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print("✅ steamlib установлен (с флагами)")
        except:
            print("❌ Критическая ошибка: не удалось установить steamlib")

# Проверяем и устанавливаем зависимости только при необходимости
def _check_and_install_deps():
    required_deps = [
        "aiohttp", "pydantic", "rsa", "lxml", "telebot", 
        "urllib3", "yarl", "cryptography", "bs4", "colorama",
        "psutil", "fake_useragent", "PIL", "requests_toolbelt",
        "bcrypt", "pysteamauth"
    ]
    
    missing_deps = []
    for dep in required_deps:
        try:
            importlib.import_module(dep)
        except ImportError:
            missing_deps.append(dep)
    
    # Проверяем steamlib отдельно
    try:
        from steamlib.api.trade import SteamTrade
    except ImportError:
        missing_deps.append("steamlib")
    
    if missing_deps:
        print(f"Отсутствующие зависимости: {missing_deps}")
        print("Начинаю автоматическую установку...")
        _install_dependencies()
        
        # Повторная проверка после установки
        still_missing = []
        for dep in required_deps:
            try:
                importlib.import_module(dep)
            except ImportError:
                still_missing.append(dep)
        
        if still_missing:
            print(f"⚠️ Не удалось установить: {still_missing}")
            print("Пожалуйста, установите их вручную:")
            print("pip install " + " ".join(still_missing))
        else:
            print("✅ Все зависимости успешно установлены!")

# Зависимости управляются основным ботом. Плагин не должен менять общее окружение.

# Теперь импортируем все зависимости
import aiohttp
import pydantic
import rsa
from lxml.html import document_fromstring
from pysteamauth.abstract import CookieStorageAbstract, RequestStrategyAbstract
from pysteamauth.auth import Steam
from steamlib.api.trade import SteamTrade
from steamlib.api.trade.exceptions import NotFoundMobileConfirmationError
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup
from urllib3.util import parse_url
from yarl import URL
from cryptography.fernet import Fernet
import asyncio
import base64
import json
import logging
import os
import random
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import hashlib
import hmac
import struct
import requests
import uuid
import platform
import html
from FunPayAPI.updater.events import NewMessageEvent
from FunPayAPI.common.enums import OrderStatuses

# --- СИСТЕМА ЛИЦЕНЗИЙ ---
LICENSE_SERVER_URL = "http://62.113.42.110:5000"
LICENSE_CHECK_INTERVAL = 600
MAX_RETRY_COUNT = 3

class LicenseManager:
    def __init__(self):
        self.license_valid = False
        self.license_checked = False
        self.last_check = 0
        self.retry_count = 0
        self.license_data = {}
        self.check_lock = threading.Lock()
        
    def generate_signature(self, data: str, timestamp: str) -> str:
        api_key = "ваш_api_ключ"
        message = f"{data}{timestamp}".encode()
        return hmac.new(
            api_key.encode(),
            message,
            hashlib.sha256
        ).hexdigest()
        
    def check_license(self, tg_id: str):
        with self.check_lock:
            current_time = time.time()
            
            if self.license_checked and (current_time - self.last_check) < 300:
                return self.license_valid, "Из кэша"
            
            try:
                timestamp = str(int(time.time()))
                request_data = json.dumps({
                    "tg_id": str(tg_id),
                    "plugin_name": "auto_rent_steam"
                })
                
                signature = self.generate_signature(request_data, timestamp)
                
                headers = {
                    "User-Agent": f"AutoRentSteam/{VERSION}",
                    "Content-Type": "application/json",
                    "X-Bot-Timestamp": timestamp,
                    "X-Bot-Signature": signature
                }
                
                response = requests.post(
                    f"{LICENSE_SERVER_URL}/api/check_license",
                    data=request_data,
                    timeout=10,
                    headers=headers
                )
                
                if response.status_code == 200:
                    result = response.json()
                    
                    if result.get('status') == 'success' and result.get('license_status') == 'Yes':
                        self.license_valid = True
                        self.license_checked = True
                        self.last_check = current_time
                        self.retry_count = 0
                        self.license_data = result.get('data', {})
                        return True, "Лицензия активна"
                    else:
                        self.license_valid = False
                        self.license_checked = True
                        return False, result.get('message', 'Лицензия не активна')
                else:
                    self.retry_count += 1
                    error_msg = f"HTTP {response.status_code}"
                    if self.retry_count >= MAX_RETRY_COUNT:
                        self.license_valid = False
                        return False, f"Сервер лицензий недоступен: {error_msg}"
                    return False, f"Ошибка сервера: {error_msg}"
                    
            except requests.exceptions.Timeout:
                self.retry_count += 1
                if self.retry_count >= MAX_RETRY_COUNT:
                    self.license_valid = False
                    return False, "Таймаут подключения к серверу лицензий"
                return False, "Таймаут подключения"
                
            except requests.exceptions.ConnectionError:
                self.retry_count += 1
                if self.retry_count >= MAX_RETRY_COUNT:
                    self.license_valid = False
                    return False, "Нет подключения к серверу лицензий"
                return False, "Ошибка подключения"
                
            except Exception as e:
                self.retry_count += 1
                if self.retry_count >= MAX_RETRY_COUNT:
                    self.license_valid = False
                    return False, f"Ошибка проверки лицензии: {str(e)}"
                return False, f"Временная ошибка: {str(e)}"
    
    def is_valid(self):
        return self.license_valid
    
    def should_check_license(self):
        if not self.license_checked:
            return True
        return (time.time() - self.last_check) > LICENSE_CHECK_INTERVAL
    
    def get_license_data(self):
        return self.license_data.copy()

license_manager = LicenseManager()

def get_tg_id_from_cache():
    cache_file = "storage/cache/tg_authorized_users.json"
    try:
        if os.path.exists(cache_file):
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data and isinstance(data, dict):
                    tg_ids = list(data.keys())
                    if tg_ids:
                        return tg_ids[0]
        return None
    except Exception as e:
        return None

def critical_license_check(c = None):
    if not license_manager.is_valid():
        return True
        
    tg_id = get_tg_id_from_cache()
    if not tg_id:
        return False
        
    if license_manager.should_check_license():
        is_valid, message = license_manager.check_license(tg_id)
        if not is_valid:
            return False

    return license_manager.is_valid()

# Функция для получения московского времени (UTC+3)
def get_moscow_time():
    """Возвращает текущее время в московском часовом поясе (UTC+3)"""
    msk_tz = timezone(timedelta(hours=3))
    return datetime.now(msk_tz)

def utc_to_moscow(utc_dt):
    """Конвертирует UTC datetime в московское время"""
    if isinstance(utc_dt, str):
        utc_dt = datetime.fromisoformat(utc_dt.replace('Z', '+00:00'))
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    msk_tz = timezone(timedelta(hours=3))
    return utc_dt.astimezone(msk_tz)

DATA_DIR = Path("data") / "rent_steam_dante"                        
ACCOUNTS_FILE = DATA_DIR / "accounts.json"
RENTALS_FILE = DATA_DIR / "rentals.json"
LOTS_FILE = DATA_DIR / "lots.json"
BUYER_NOTES_FILE = DATA_DIR / "buyers.json"


                                                                              





                                                                             


class ErrorSteamPasswordChange(Exception):
    pass


def generate_password(
    min_length: int = 18,
    max_length: int = 18,
    alphabet: str = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
) -> str:
    if min_length > max_length:
        raise ValueError("Wrong length interval")
    if min_length < 0 or max_length < 0 or max_length > 32:
        raise ValueError("Wrong length value")
    length = random.randint(min_length, max_length)
    return "".join(secrets.choice(alphabet) for _ in range(length))


                                                                             


class PasswordChangeParams(pydantic.BaseModel):
    s: int
    account: int
    reset: int
    issueid: int
    lost: int = 0


class RSAKey(pydantic.BaseModel):
    mod: str
    exp: str
    timestamp: int
    token_gid: Optional[str] = None

    class Config:
        fields = {
            "mod": "publickey_mod",
            "exp": "publickey_exp",
        }


                                                                             


class CustomSteam(Steam):
                                                                  

    def __init__(
        self,
        login: str,
        password: str,
        steamid: Optional[int] = None,
        shared_secret: Optional[str] = None,
        identity_secret: Optional[str] = None,
        device_id: Optional[str] = None,
        cookie_storage: Optional[CookieStorageAbstract] = None,
        request_strategy: Optional[RequestStrategyAbstract] = None,
    ):
        super().__init__(
            login=login,
            password=password,
            steamid=steamid,
            shared_secret=shared_secret,
            identity_secret=identity_secret,
            device_id=device_id,
            cookie_storage=cookie_storage,
            request_strategy=request_strategy,
        )

    @property
    def password(self) -> str:                          
        return self._password

    async def json_request(self, url: str, method: str = "GET", **kwargs: Any) -> Dict[str, Any]:
        return json.loads(await super().request(url, method, **kwargs))

    async def raw_request(
        self,
        url: str,
        method: str = "GET",
        **kwargs: Any,
    ) -> aiohttp.ClientResponse:
        return await self._requests.request(
            url=url,
            method=method,
            cookies=await self.cookies(parse_url(url).host),
            **kwargs,
        )


class SteamPasswordChange:
                                            

    BROWSER = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/"
        "537.36 (KHTML, как Gecko) Chrome/83.0.4103.116 Safari/537.36"
    )

    def __init__(self, steam: CustomSteam):
        self._steam = steam
        self._steam_trade = SteamTrade(steam)

    async def change(self, new_password: str) -> None:
        if not new_password:
            raise ValueError("Empty new password")
        if new_password == self._steam.password:
            raise ValueError("New password is equal old password")

        await self._steam.login_to_steam()

        params = await self._receive_password_change_params()

        await self._login_info_enter_code(params)
        await self._send_account_recovery_code(params)

        for _ in range(3):
            try:
                success = await self._steam_trade.mobile_confirm_by_creator_id(params.s)
                if not success:
                    raise ErrorSteamPasswordChange("Error password change confirmation")
                break
            except NotFoundMobileConfirmationError:
                await asyncio.sleep(2)
        else:
            raise NotFoundMobileConfirmationError("Not found mobile confirmation")

        await self._poll_account_recovery_confirmation(params)
        await self._verify_account_recovery_code(params)
        await self._account_recovery_get_next_step(params)

        key = await self._get_rsa_key()
        await self._recovery_verify_password(
            data=params,
            encrypted_password=self._encrypt_password(self._steam.password, key.mod, key.exp),
            rsatimestamp=key.timestamp,
        )

        key = await self._get_rsa_key()
        await self._check_password_available(new_password)
        await self._change_password_request(
            data=params,
            encrypted_password=self._encrypt_password(new_password, key.mod, key.exp),
            rsatimestamp=key.timestamp,
        )

    async def _receive_password_change_params(self) -> PasswordChangeParams:
        response = await self._steam.raw_request(
            method="GET",
            url="https://help.steampowered.com/wizard/HelpChangePassword?redir=store/account/",
            headers={
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9"
                ),
                "Referer": "https://store.steampowered.com/",
                "User-Agent": self.BROWSER,
            },
            allow_redirects=True,
        )
        if response.history:
            try:
                return PasswordChangeParams(**URL(response.real_url).query)
            except pydantic.ValidationError:
                pass
        html = await response.text()
        page = document_fromstring(html)
        errors = page.cssselect("#error_description")
        if errors:
            raise ErrorSteamPasswordChange(errors[0].text)
        raise ErrorSteamPasswordChange("Password change error")

    async def _login_info_enter_code(self, data: PasswordChangeParams) -> None:
        await self._steam.raw_request(
            method="GET",
            url="https://help.steampowered.com/en/wizard/HelpWithLoginInfoEnterCode",
            params={
                "s": data.s,
                "account": data.account,
                "reset": data.reset,
                "lost": data.lost,
                "issueid": data.issueid,
                "sessionid": await self._steam.sessionid("help.steampowered.com"),
                "wizard_ajax": 1,
                "gamepad": 0,
            },
            headers={
                "Accept": "*/*",
                "X-Requested-With": "XMLHttpRequest",
                "User-Agent": self.BROWSER,
            },
        )

    async def _send_account_recovery_code(self, data: PasswordChangeParams) -> bool:
        from pysteamauth.errors import check_steam_error

        response = await self._steam.json_request(
            method="POST",
            url="https://help.steampowered.com/en/wizard/AjaxSendAccountRecoveryCode",
            data={
                "sessionid": await self._steam.sessionid("help.steampowered.com"),
                "wizard_ajax": "1",
                "gamepad": "0",
                "s": data.s,
                "method": "8",
                "link": "",
                "n": "1",
            },
            headers={
                "Accept": "*/*",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": "https://help.steampowered.com",
                "Referer": (
                    "https://help.steampowered.com/ru/wizard/HelpWithLoginInfoEnterCode"
                    f"?s={data.s}&account={data.account}&reset={data.reset}"
                    f"&lost={data.lost}&issueid={data.issueid}"
                ),
                "X-Requested-With": "XMLHttpRequest",
                "User-Agent": self.BROWSER,
            },
        )
        if response.get("errorMsg"):
            raise ErrorSteamPasswordChange(response["errorMsg"])
        success = response["success"]
        if isinstance(success, int):
            check_steam_error(response["success"])
        return bool(success)

    async def _poll_account_recovery_confirmation(self, data: PasswordChangeParams) -> Dict[str, bool]:
        response = await self._steam.json_request(
            method="POST",
            url="https://help.steampowered.com/en/wizard/AjaxPollAccountRecoveryConfirmation",
            data={
                "sessionid": await self._steam.sessionid("help.steampowered.com"),
                "wizard_ajax": 1,
                "s": data.s,
                "reset": data.reset,
                "lost": data.lost,
                "method": 8,
                "issueid": data.issueid,
                "gamepad": 0,
            },
            headers={
                "Accept": "*/*",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": "https://help.steampowered.com",
                "User-Agent": self.BROWSER,
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        if response.get("errorMsg"):
            raise ErrorSteamPasswordChange(response["errorMsg"])
        return response

    async def _verify_account_recovery_code(self, data: PasswordChangeParams) -> None:
        response = await self._steam.json_request(
            method="GET",
            url="https://help.steampowered.com/en/wizard/AjaxVerifyAccountRecoveryCode",
            params={
                "code": "",
                "s": data.s,
                "reset": data.reset,
                "lost": data.lost,
                "method": 8,
                "issueid": data.issueid,
                "sessionid": await self._steam.sessionid("help.steampowered.com"),
                "wizard_ajax": 1,
                "gamepad": 0,
            },
            headers={
                "Accept": "*/*",
                "User-Agent": self.BROWSER,
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        if response.get("errorMsg"):
            raise ErrorSteamPasswordChange(response["errorMsg"])

    async def _account_recovery_get_next_step(self, data: PasswordChangeParams) -> None:
        response = await self._steam.json_request(
            method="POST",
            url="https://help.steampowered.com/en/wizard/AjaxAccountRecoveryGetNextStep",
            data={
                "sessionid": await self._steam.sessionid("help.steampowered.com"),
                "wizard_ajax": 1,
                "s": data.s,
                "account": data.account,
                "reset": data.reset,
                "issueid": data.issueid,
                "lost": 2,
            },
            headers={
                "Accept": "*/*",
                "X-Requested-With": "XMLHttpRequest",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": "https://help.steampowered.com",
                "User-Agent": self.BROWSER,
            },
        )
        if response.get("errorMsg"):
            raise ErrorSteamPasswordChange(response["errorMsg"])

    async def _get_rsa_key(self) -> RSAKey:
        response = await self._steam.json_request(
            method="POST",
            url="https://help.steampowered.com/en/login/getrsakey/",
            data={
                "sessionid": await self._steam.sessionid("help.steampowered.com"),
                "username": self._steam.login,
            },
            headers={
                "Accept": "*/*",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": "https://help.steampowered.com",
                "X-Requested-With": "XMLHttpRequest",
                "User-Agent": self.BROWSER,
            },
        )
        if response.get("errorMsg"):
            raise ErrorSteamPasswordChange(response["errorMsg"])
        return RSAKey.parse_obj(response)

    def _encrypt_password(self, password: str, mod: str, exp: str) -> str:
        publickey_exp = int(exp, 16)
        publickey_mod = int(mod, 16)
        public_key = rsa.PublicKey(n=publickey_mod, e=publickey_exp)
        encrypted_password = rsa.encrypt(password.encode("ascii"), public_key)
        return base64.b64encode(encrypted_password).decode("utf8")

    async def _recovery_verify_password(
        self,
        data: PasswordChangeParams,
        encrypted_password: str,
        rsatimestamp: int,
    ) -> None:
        response = await self._steam.json_request(
            method="POST",
            url="https://help.steampowered.com/en/wizard/AjaxAccountRecoveryVerifyPassword/",
            data={
                "sessionid": await self._steam.sessionid("help.steampowered.com"),
                "s": data.s,
                "lost": 2,
                "reset": 1,
                "password": encrypted_password,
                "rsatimestamp": rsatimestamp,
            },
            headers={
                "Accept": "*/*",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": "https://help.steampowered.com",
                "X-Requested-With": "XMLHttpRequest",
                "User-Agent": self.BROWSER,
            },
        )
        if response.get("errorMsg"):
            raise ErrorSteamPasswordChange(response["errorMsg"])

    async def _check_password_available(self, password: str) -> None:
        response = await self._steam.json_request(
            url="https://help.steampowered.com/en/wizard/AjaxCheckPasswordAvailable/",
            method="POST",
            data={
                "sessionid": await self._steam.sessionid("help.steampowered.com"),
                "wizard_ajax": 1,
                "password": password,
            },
            headers={
                "Origin": "https://help.steampowered.com",
                "User-Agent": self.BROWSER,
            },
        )
        if not response["available"]:
            raise ErrorSteamPasswordChange("Not password available")

    async def _change_password_request(
        self,
        data: PasswordChangeParams,
        encrypted_password: str,
        rsatimestamp: int,
    ) -> None:
        response = await self._steam.json_request(
            method="POST",
            url="https://help.steampowered.com/ru/wizard/AjaxAccountRecoveryChangePassword/",
            data={
                "sessionid": await self._steam.sessionid("help.steampowered.com"),
                "wizard_ajax": 1,
                "s": data.s,
                "account": data.account,
                "password": encrypted_password,
                "rsatimestamp": rsatimestamp,
            },
            headers={
                "Accept": "*/*",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": "https://help.steampowered.com",
                "X-Requested-With": "XMLHttpRequest",
                "User-Agent": self.BROWSER,
            },
        )
        if response.get("errorMsg"):
            raise ErrorSteamPasswordChange(response["errorMsg"])


def generate_guard_code(shared_secret: str, timestamp: Optional[int] = None) -> str:
                                                      

    if not shared_secret:
        raise ValueError("Shared secret is empty")
    secret_bytes = base64.b64decode(shared_secret)
    if timestamp is None:
        timestamp = int(time.time())
    time_buffer = struct.pack(">Q", int(timestamp / 30))
    hmac_digest = hmac.new(secret_bytes, time_buffer, hashlib.sha1).digest()
    start = hmac_digest[-1] & 0x0F
    code_int = struct.unpack_from(">I", hmac_digest, start)[0] & 0x7FFFFFFF
    alphabet = "23456789BCDFGHJKMNPQRTVWXY"
    code = ""
    for _ in range(5):
        code += alphabet[code_int % len(alphabet)]
        code_int //= len(alphabet)
    return code


                                                                             

NAME = "Auto Rent Steam"
VERSION = "0.3.1"
DESCRIPTION = "Автоаренда Steam-аккаунтов на FunPay"
CREDITS = "@veemp | https://t.me/FunPay_plugin"
UUID = "6f85cf7a-9564-4f3c-9f2f-cb4f4a03a6bd"
SETTINGS_PAGE = False

LOGGER = logging.getLogger("FPC.RentSteamDante")
LOGGER_PREFIX = "[AutoRentSteam]"
# Настройка логирования в файл (как в dempstars)
log_path = os.path.join(os.path.dirname(__file__), "rent_steam_dante.log")
fh = logging.FileHandler(log_path, encoding="utf-8")
fh.setLevel(logging.DEBUG)
fh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
LOGGER.addHandler(fh)
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
ch.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
LOGGER.addHandler(ch)

CARDINAL = None
MENU_COMMAND = "rent_menu"
STATE: Dict[int, Dict[str, Any]] = {}

_STOP_EVENT: Optional[threading.Event] = None
_SCHEDULER_THREAD: Optional[threading.Thread] = None
_PLUGIN_LOADED: bool = False                                

ACCOUNT_STATUS_LABELS = {
    "active": "🟢 Активен",
    "frozen": "❄️ Заморожен",
    "busy": "🔴 Выдан",
}

ACCOUNT_FIELDS = ["login", "password", "game"]

MAIN_SECTIONS = [
    ("📊 Статистика", "section:dashboard"),
    ("🎮 Аренда", "section:rental"),
    ("👥 Аккаунты", "section:accounts"),
    ("📦 Лоты", "section:lots"),
    ("🔐 Steam", "section:steam"),
    ("📋 Логи", "section:logs"),
    ("🧭 Статус", "monitor:status"),
]


SECTION_CONTENT = {
    "dashboard": "Здесь будет статистика дня.",
    "quick": "Быстрые операции: выдача, продление, отмена.",
    "rental": "Управление арендами Steam.",
    "accounts": "Список и управление аккаунтами.",
    "steam": "Инструменты Steam: смена пароля, Guard.",
    "monitor": "Мониторинг состояния и уведомления.",
}


PRIVATE_CALLBACKS = {name for _, name in MAIN_SECTIONS}
_PRIVATE_PREFIX = "section:"
_ACCOUNTS_PREFIX = "accounts:"
_RENTAL_PREFIX = "rental:"
ACCOUNT_CALLBACKS = {
    "accounts:list",
    "accounts:view",
    "accounts:edit",
    "accounts:freeze",
    "accounts:delete",
}

RENTAL_CALLBACKS = {
    "rental:issue",
    "rental:active",
    "rental:finish",
    "rental:extend",
    "rental:cancel",
}

MONITOR_CALLBACKS = {
    "monitor:logs",
    "monitor:errors",
    "monitor:jobs",
    "monitor:status",
}

LOTS_CALLBACKS = {
    "lots:list",
    "lots:add",
    "lots:binder",
    "lots:edit",
}


                                                                              
                            
                                                                              



def _ensure_storage() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not ACCOUNTS_FILE.exists():
        ACCOUNTS_FILE.write_text(json.dumps({"accounts": []}, indent=2), encoding="utf-8")
    if not RENTALS_FILE.exists():
        RENTALS_FILE.write_text(json.dumps({"rentals": []}, indent=2), encoding="utf-8")
    if not LOTS_FILE.exists():
        LOTS_FILE.write_text(json.dumps({"lots": []}, indent=2), encoding="utf-8")
    if not BUYER_NOTES_FILE.exists():
        BUYER_NOTES_FILE.write_text(json.dumps({}, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_accounts() -> List[Dict[str, Any]]:
    try:
        data = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        LOGGER.error("Невозможно прочитать файл аккаунтов, сбрасываю")
        return []
    accounts = data.get("accounts", [])
    for acc in accounts:
        acc.setdefault("status", "active")
        acc.setdefault("notes", [])
        acc.setdefault("previous_passwords", [])
        if not isinstance(acc.get("notes"), list):
            acc["notes"] = [str(acc.get("notes"))]
    return accounts


def _save_accounts(accounts: List[Dict[str, Any]]) -> None:
    ACCOUNTS_FILE.write_text(
        json.dumps({"accounts": accounts}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_rentals() -> List[Dict[str, Any]]:
    try:
        data = json.loads(RENTALS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        LOGGER.error("Невозможно прочитать файл аренд, сбрасываю")
        return []
    return data.get("rentals", [])


def _save_rentals(rentals: List[Dict[str, Any]]) -> None:
    RENTALS_FILE.write_text(
        json.dumps({"rentals": rentals}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _set_state(chat_id: int, **data: Any) -> None:
    STATE[chat_id] = data


def _clear_state(chat_id: int) -> None:
    STATE.pop(chat_id, None)


def _build_menu() -> InlineKeyboardMarkup:
    """Создает главное меню с правильным расположением кнопок и кнопкой магазина"""
    markup = InlineKeyboardMarkup(row_width=2)
    # Первый ряд: Статистика и Аренда
    markup.row(
        InlineKeyboardButton("📊 Статистика", callback_data="section:dashboard"),
        InlineKeyboardButton("🎮 Аренда", callback_data="section:rental")
    )
    # Второй ряд: Аккаунты и Лоты
    markup.row(
        InlineKeyboardButton("👥 Аккаунты", callback_data="section:accounts"),
        InlineKeyboardButton("📦 Лоты", callback_data="section:lots")
    )
    # Третий ряд: Steam и Логи
    markup.row(
        InlineKeyboardButton("🔐 Steam", callback_data="section:steam"),
        InlineKeyboardButton("📋 Логи", callback_data="section:logs")
    )
    # Четвертый ряд: Статус и Обновить
    markup.row(
        InlineKeyboardButton("🧭 Статус", callback_data="monitor:status"),
        InlineKeyboardButton("🔄 Обновить", callback_data="section:refresh")
    )
    # Пятый ряд: Магазин плагинов (отдельно)
    markup.add(InlineKeyboardButton("🔹Канал с плагинами🔹", url="https://t.me/FunPay_plugin"))
    return markup


def _build_section_menu(section: str) -> InlineKeyboardMarkup:
    """Создает подменю для раздела с правильным расположением кнопок (1-3 в ряд)"""
    markup = InlineKeyboardMarkup(row_width=2)
    if section == "accounts":
        # Первый ряд: Список и Просмотр
        markup.row(
            InlineKeyboardButton("📋 Список", callback_data="accounts:list"),
            InlineKeyboardButton("🔍 Просмотр", callback_data="accounts:view")
        )
        # Второй ряд: Редактировать и Заморозить
        markup.row(
            InlineKeyboardButton("✏️ Редактировать", callback_data="accounts:edit"),
            InlineKeyboardButton("❄️ Заморозить", callback_data="accounts:freeze")
        )
        # Третий ряд: Удалить
        markup.add(InlineKeyboardButton("🗑️ Удалить", callback_data="accounts:delete"))
        # Четвертый ряд: Добавить
        markup.add(InlineKeyboardButton("➕ Добавить", callback_data="rent_sd:add"))
        # Пятый ряд: Назад
        markup.add(InlineKeyboardButton("⬅️ Назад", callback_data="section:back"))
        return markup
    if section == "rental":
        # Первый ряд: Выдать и Активные
        markup.row(
            InlineKeyboardButton("🆕 Выдать", callback_data="rental:issue"),
            InlineKeyboardButton("📋 Активные", callback_data="rental:active")
        )
        # Второй ряд: Завершить и Продлить
        markup.row(
            InlineKeyboardButton("✅ Завершить", callback_data="rental:finish"),
            InlineKeyboardButton("🔁 Продлить", callback_data="rental:extend")
        )
        # Третий ряд: Отменить
        markup.add(InlineKeyboardButton("❌ Отменить", callback_data="rental:cancel"))
        # Четвертый ряд: Назад
        markup.add(InlineKeyboardButton("⬅️ Назад", callback_data="section:back"))
        return markup
    if section == "steam":
        # Первый ряд: Сменить пароль и Guard-код
        markup.row(
            InlineKeyboardButton("🔐 Сменить пароль", callback_data="rent_sd:password"),
            InlineKeyboardButton("🔑 Guard-код", callback_data="rent_sd:guard")
        )
        # Второй ряд: Назад
        markup.add(InlineKeyboardButton("⬅️ Назад", callback_data="section:back"))
        return markup
    if section == "monitor":
        # Первый ряд: Ошибки и Задачи
        markup.row(
            InlineKeyboardButton("⚠️ Ошибки", callback_data="monitor:errors"),
            InlineKeyboardButton("⌚ Задачи", callback_data="monitor:jobs")
        )
        # Второй ряд: Статус
        markup.add(InlineKeyboardButton("🧭 Статус", callback_data="monitor:status"))
        # Третий ряд: Назад
        markup.add(InlineKeyboardButton("⬅️ Назад", callback_data="section:back"))
        return markup
    if section == "lots":
        # Первый ряд: Список и Добавить
        markup.row(
            InlineKeyboardButton("📦 Список", callback_data="lots:list"),
            InlineKeyboardButton("➕ Добавить", callback_data="lots:add")
        )
        # Второй ряд: Привязки и Редактировать
        markup.row(
            InlineKeyboardButton("🔗 Привязки", callback_data="lots:binder"),
            InlineKeyboardButton("✏️ Редактировать", callback_data="lots:edit")
        )
        # Третий ряд: Назад
        markup.add(InlineKeyboardButton("⬅️ Назад", callback_data="section:back"))
        return markup
    if section == "logs":
        # Первый ряд: Просмотр и Скачать
        markup.row(
            InlineKeyboardButton("📋 Просмотр", callback_data="logs:view"),
            InlineKeyboardButton("📥 Скачать", callback_data="logs:download")
        )
        # Второй ряд: Очистить
        markup.add(InlineKeyboardButton("🗑️ Очистить", callback_data="logs:clear"))
        # Третий ряд: Назад
        markup.add(InlineKeyboardButton("⬅️ Назад", callback_data="section:back"))
        return markup
    markup.add(InlineKeyboardButton("⬅️ Назад", callback_data="section:back"))
    return markup


HEADER_TEMPLATE = (
    "💼 <b>AUTO RENT STEAM</b>\n\n"
    "⚙️ <b>Автоматическая аренда Steam-аккаунтов</b>\n\n"
    "✨ <b>Возможности:</b>\n"
    "・ Управление аккаунтами Steam\n"
    "・ Автоматическая выдача аренды\n"
    "・ Смена паролей и Guard-коды\n"
    "・ Статистика и мониторинг\n\n"
    "📋 <b>Выберите раздел для работы:</b>"
)

SECTION_TEMPLATES = {
    "dashboard": "📊 <b>Статистика</b>\n\n{stats}",
    "rental": (
        "🎮 <b>Управление арендой</b>\n\n"
        "⚡ Доступные действия:\n"
        "・ Выдача аккаунта\n"
        "・ Завершение аренды\n"
        "・ Продление аренды\n"
        "・ Отмена аренды"
    ),
    "steam": "🔐 <b>Инструменты Steam</b>\n\n⚙️ Выберите инструмент:",
    "monitor": "🛡 <b>Мониторинг системы</b>\n\n📊 Следите за состоянием и уведомлениями:",
    "lots": "📦 <b>Управление лотами</b>\n\n🔧 Управление лотами и привязкой аккаунтов:",
    "logs": "📋 <b>Логи системы</b>\n\n📝 Просмотр и управление логами:",
}


def show_menu(message) -> None:
    # Проверка лицензии
    if not critical_license_check():
        show_license_menu(message)
        return
    
    try:
        CARDINAL.telegram.bot.send_message(
            message.chat.id,
            HEADER_TEMPLATE,
            parse_mode="HTML",
            reply_markup=_build_menu(),
        )
    except Exception as exc:                
        LOGGER.error("rent_steam_dante: failed to show menu: %s", exc)


def _start_add_account(call) -> None:
    chat_id = call.message.chat.id
    _set_state(chat_id, mode="add", step="login")
    CARDINAL.telegram.bot.send_message(
        chat_id,
        "➕ <b>Добавление нового аккаунта</b>\n\n"
        "📝 Отправьте логин аккаунта\n"
        "❌ Для отмены напишите /cancel",
        parse_mode="HTML"
    )


def _start_change_password(call) -> None:
    chat_id = call.message.chat.id
    accounts = _load_accounts()
    if not accounts:
        CARDINAL.telegram.bot.send_message(
            chat_id, 
            "❌ Список аккаунтов пуст.\n"
            "➕ Сначала добавьте аккаунт."
        )
        return
    lines = [
        f"{idx}. <b>{acc.get('login','?')}</b> (#{acc.get('id')})"
        for idx, acc in enumerate(accounts, start=1)
    ]
    _set_state(
        chat_id,
        mode="password",
        step="choose",
        choices=[acc.get("id") for acc in accounts],
    )
    CARDINAL.telegram.bot.send_message(
        chat_id,
        "🔐 <b>Смена пароля</b>\n\n"
        "📋 Выберите аккаунт для смены пароля:\n" + "\n".join(lines),
        parse_mode="HTML",
    )


def _start_guard_code(call) -> None:
    chat_id = call.message.chat.id
    accounts = _load_accounts()
    if not accounts:
        CARDINAL.telegram.bot.send_message(
            chat_id, 
            "❌ Список аккаунтов пуст.\n"
            "➕ Сначала добавьте аккаунт."
        )
        return
    lines = [
        f"{idx}. <b>{acc.get('login','?')}</b> (#{acc.get('id')})"
        for idx, acc in enumerate(accounts, start=1)
    ]
    _set_state(
        chat_id,
        mode="guard",
        step="choose",
        choices=[acc.get("id") for acc in accounts],
    )
    CARDINAL.telegram.bot.send_message(
        chat_id,
        "🔑 <b>Получение Guard-кода</b>\n\n"
        "📋 Выберите аккаунт для получения Guard-кода:\n" + "\n".join(lines),
        parse_mode="HTML",
    )


def _handle_button(call) -> None:
    CARDINAL.telegram.bot.answer_callback_query(call.id)
    if call.data == "section:back":
        try:
            CARDINAL.telegram.bot.edit_message_text(
                HEADER_TEMPLATE,
                call.message.chat.id,
                call.message.message_id,
                parse_mode="HTML",
                reply_markup=_build_menu(),
            )
        except Exception:
            show_menu(call.message)
        return
    
    # ДОБАВИТЬ ЭТОТ БЛОК - обработчик проверки лицензии
    elif call.data == "rent_sd:check_license":
        show_license_menu(call)
        return
    
    if call.data == "section:refresh":
        try:
            CARDINAL.telegram.bot.edit_message_text(
                HEADER_TEMPLATE,
                call.message.chat.id,
                call.message.message_id,
                parse_mode="HTML",
                reply_markup=_build_menu(),
            )
        except Exception:
            show_menu(call.message)
        return
    if call.data.startswith(_ACCOUNTS_PREFIX):
        _handle_accounts_callback(call)
        return
    if call.data.startswith(_RENTAL_PREFIX):
        _handle_rental_callback(call)
        return
    if call.data.startswith(_PRIVATE_PREFIX):
        section = call.data.split(":", 1)[1]
        if section == "dashboard":
            message = SECTION_TEMPLATES[section].format(stats=_build_dashboard_stats())
        elif section == "accounts":
            message = _build_accounts_section_text()
        elif section == "logs":
            _show_logs_menu(call.message)
            return
        else:
            message = SECTION_TEMPLATES.get(section, "⚠️ Раздел в разработке.")
        try:
            CARDINAL.telegram.bot.edit_message_text(
                message,
                call.message.chat.id,
                call.message.message_id,
                parse_mode="HTML",
                reply_markup=_build_section_menu(section),
            )
        except Exception:
            CARDINAL.telegram.bot.send_message(
                call.message.chat.id,
                message,
                parse_mode="HTML",
                reply_markup=_build_section_menu(section),
            )
        return
    if call.data == "rent_sd:add":
        _start_add_account(call)
        return
    if call.data == "rent_sd:password":
        _start_change_password(call)
        return
    if call.data == "rent_sd:guard":
        _start_guard_code(call)
        return
    if call.data.startswith("monitor:"):
        _handle_monitor_callback(call)
        return
    if call.data.startswith("lots:"):
        _handle_lots_callback(call)
        return
    if call.data.startswith("logs:"):
        _handle_logs_callback(call)
        return
    CARDINAL.telegram.bot.send_message(call.message.chat.id, "❌ Функция временно недоступна.")


def _select_account(message, state, text):
    chat_id = message.chat.id
    choices = state.get("choices", [])
    try:
        idx = int(text)
    except ValueError:
        CARDINAL.telegram.bot.send_message(chat_id, "❌ <b>Ошибка ввода</b>\n\n📝 Введите номер из списка или отправьте /cancel для отмены.", parse_mode="HTML")
        return None
    if not (1 <= idx <= len(choices)):
        CARDINAL.telegram.bot.send_message(chat_id, "❌ Неверный номер. Попробуйте снова.")
        return None
    account_id = choices[idx - 1]
    accounts = _load_accounts()
    account = next((acc for acc in accounts if acc.get("id") == account_id), None)
    if account is None:
        CARDINAL.telegram.bot.send_message(chat_id, "❌ Аккаунт не найден. Обновите список.")
        _clear_state(chat_id)
        return None
    return account_id, account, accounts


def _process_state_message(message) -> None:
    chat_id = message.chat.id
    text = (message.text or "").strip().lstrip("\ufeff")

    if text.lower() in {"/rent_menu", "rent_menu"}:
        _clear_state(chat_id)
        show_menu(message)
        return

    state = STATE.get(chat_id)
    if not state:
        return

    if text.lower() == "/cancel":
        _clear_state(chat_id)
        CARDINAL.telegram.bot.send_message(chat_id, "❌ Операция отменена.")
        return

    mode = state.get("mode")

    if mode == "add":
        _process_add_account(message, state, text)
        return
    if mode == "password":
        _process_password_change(message, state, text)
        return
    if mode == "guard":
        _process_guard_code(message, state, text)
        return
    if mode == "accounts_view":
        _process_accounts_view(message, state, text)
        return
    if mode == "accounts_edit":
        _process_accounts_edit(message, state, text)
        return
    if mode == "accounts_freeze":
        _process_accounts_freeze(message, state, text)
        return
    if mode == "accounts_delete":
        _process_accounts_delete(message, state, text)
        return
    if mode == "rental_issue":
        _process_rental_issue(message, state, text)
        return
    if mode == "rental_finish":
        _process_rental_finish(message, state, text)
        return
    if mode == "rental_extend":
        _process_rental_extend(message, state, text)
        return
    if mode == "rental_cancel":
        _process_rental_cancel(message, state, text)
        return
    if mode == "lots_add":
        _process_lots_add(message, state, text)
        return
    if mode == "lots_binder":
        _process_lots_binder(message, state, text)
        return


def _process_accounts_view(message, state, text):
    selection = _select_account(message, state, text)
    if selection is None:
        return
    _, account, _ = selection
    notes = account.get("notes", [])
    notes_text = "\n".join(f"・ {note}" for note in notes) if notes else "—"
    details = (
        "<b>🔍 Карточка аккаунта</b>\n"
        f"ID: <code>{account.get('id')}</code>\n"
        f"Логин: <b>{account.get('login', '?')}</b>\n"
        f"Пароль: <code>{account.get('password') or '—'}</code>\n"
        f"Игра: <b>{account.get('game', 'не указана')}</b>\n"
        f"Статус: {ACCOUNT_STATUS_LABELS.get(account.get('status'), account.get('status'))}\n"
        f"Создан: {account.get('created_at', '—')}\n"
        f"Заметки:\n{notes_text}"
    )
    CARDINAL.telegram.bot.send_message(
        message.chat.id,
        details,
        parse_mode="HTML",
        reply_markup=_back_markup(),
    )
    _clear_state(message.chat.id)


def _process_accounts_edit(message, state, text):
    chat_id = message.chat.id
    step = state.get("step")
    if step == "choose":
        selection = _select_account(message, state, text)
        if selection is None:
            return
        account_id, _, _ = selection
        _set_state(chat_id, mode="accounts_edit", step="field", account_id=account_id)
        CARDINAL.telegram.bot.send_message(
            chat_id,
            "✏️ <b>Редактирование аккаунта</b>\n\n"
            "📋 Выберите поле для изменения:\n"
            "・ <code>login</code> — логин\n"
            "・ <code>password</code> — пароль\n"
            "・ <code>game</code> — игра\n\n"
            "❌ /cancel для отмены",
            parse_mode="HTML",
        )
        return

    if step == "field":
        field = text.lower()
        if field not in {"login", "password", "game"}:
            CARDINAL.telegram.bot.send_message(
                chat_id,
                "❌ Поддерживаются поля:\n"
                "・ <code>login</code>\n"
                "・ <code>password</code>\n"
                "・ <code>game</code>",
                parse_mode="HTML",
            )
            return
        _set_state(
            chat_id,
            mode="accounts_edit",
            step="value",
            account_id=state.get("account_id"),
            field=field,
        )
        CARDINAL.telegram.bot.send_message(
            chat_id,
            "✏️ <b>Редактирование поля</b>\n\n"
            "📝 Введите новое значение для поля <code>{}</code>.\n\n"
            "❌ /cancel для отмены".format(field),
            parse_mode="HTML",
        )
        return

    if step == "value":
        account_id = state.get("account_id")
        field = state.get("field")
        accounts = _load_accounts()
        account = next((acc for acc in accounts if acc.get("id") == account_id), None)
        if account is None:
            CARDINAL.telegram.bot.send_message(chat_id, "❌ Аккаунт не найден. Попробуйте снова.")
            _clear_state(chat_id)
            return
        if field == "login":
            if any(acc.get("login") == text for acc in accounts if acc.get("id") != account_id):
                CARDINAL.telegram.bot.send_message(chat_id, "❌ Логин уже используется другим аккаунтом.")
                return
        old_value = account.get(field)
        account[field] = text
        if field == "password" and old_value:
            account.setdefault("previous_passwords", []).append(old_value)
        account["updated_at"] = datetime.utcnow().isoformat()
        _save_accounts(accounts)
        CARDINAL.telegram.bot.send_message(
            chat_id,
            "✅ Поле <code>{}</code> обновлено.".format(field),
            parse_mode="HTML",
            reply_markup=_back_markup(),
        )
        _clear_state(chat_id)
        return


def _process_accounts_freeze(message, state, text):
    selection = _select_account(message, state, text)
    if selection is None:
        return
    account_id, account, accounts = selection
    current_status = account.get("status", "active")
    new_status = "frozen" if current_status != "frozen" else "active"
    account["status"] = new_status
    account["updated_at"] = datetime.utcnow().isoformat()
    _save_accounts(accounts)
    CARDINAL.telegram.bot.send_message(
        message.chat.id,
        "Статус аккаунта <b>{login}</b> изменён на: {status}".format(
            login=account.get("login", "?"),
            status=ACCOUNT_STATUS_LABELS.get(new_status, new_status),
        ),
        parse_mode="HTML",
        reply_markup=_back_markup(),
    )
    _clear_state(message.chat.id)


def _process_accounts_delete(message, state, text):
    selection = _select_account(message, state, text)
    if selection is None:
        return
    account_id, account, accounts = selection
    remaining = [acc for acc in accounts if acc.get("id") != account_id]
    _save_accounts(remaining)
    CARDINAL.telegram.bot.send_message(
        message.chat.id,
        "🗑 Аккаунт <b>{login}</b> удалён.".format(login=account.get("login", "?")),
        parse_mode="HTML",
        reply_markup=_back_markup(),
    )
    _clear_state(message.chat.id)


def _process_add_account(message, state, text):
    chat_id = message.chat.id
    if state.get("step") == "login":
        _set_state(chat_id, mode="add", step="password", login=text)
        CARDINAL.telegram.bot.send_message(chat_id, "Отправьте пароль от аккаунта.")
        return

    if state.get("step") == "password":
        _set_state(
            chat_id,
            mode="add",
            step="game",
            login=state.get("login"),
            password=text,
            game=None,
        )
        CARDINAL.telegram.bot.send_message(
            chat_id,
            "Укажите игру/категорию (например, CS2, GTA V).",
        )
        return

    if state.get("step") == "game":
        _set_state(
            chat_id,
            mode="add",
            step="mafile",
            login=state.get("login"),
            password=state.get("password"),
            game=text,
        )
        CARDINAL.telegram.bot.send_message(
            chat_id,
            "Отправьте maFile (JSON) файлом или текстом.\n\n"
            "💡 Если у вас нет maFile, напишите: <b>нет</b>\n\n"
            "Для отмены /cancel",
            parse_mode="HTML",
        )
        return

    if state.get("step") == "mafile":
        mafile = None
        skip_keywords = ["нет", "no", "пропустить", "skip", "-", "none", "без mafile", "без"]
        
                                                            
        if text and text.lower().strip() in skip_keywords and not message.document:
                                                          
            login = state.get("login")
            password = state.get("password")
            game = state.get("game")
            
            accounts = _load_accounts()
            if any(acc.get("login") == login for acc in accounts):
                CARDINAL.telegram.bot.send_message(chat_id, "Аккаунт с таким логином уже сохранён.")
                _clear_state(chat_id)
                return
            
            new_id = f"acc-{len(accounts) + 1:04d}"
            account_entry = {
                "id": new_id,
                "status": "active",
                "login": login,
                "password": password,
                "created_at": datetime.utcnow().isoformat(),
                "mafile": None,              
                "steamid": None,
                "game": game,
                "notes": ["⚠️ Аккаунт БЕЗ maFile - смена пароля вручную"],
            }
            accounts.append(account_entry)
            _save_accounts(accounts)
            
            CARDINAL.telegram.bot.send_message(
                chat_id,
                "✅ Аккаунт БЕЗ maFile добавлен:\n"
                f"・ ID: <code>{new_id}</code>\n"
                f"・ Логин: <code>{login}</code>\n"
                f"・ Игра: {game}\n\n"
                "⚠️ При окончании аренды вы получите уведомление\n"
                "   для ручной смены пароля.",
                parse_mode="HTML",
                reply_markup=_back_markup(),
            )
            _clear_state(chat_id)
            return
        
                                    
        if message.document:
            try:
                file_info = CARDINAL.telegram.bot.get_file(message.document.file_id)
                downloaded = CARDINAL.telegram.bot.download_file(file_info.file_path)
                mafile = json.loads(downloaded.decode("utf-8-sig"))
            except Exception as exc:                
                LOGGER.error("maFile download error: %s", exc)
                CARDINAL.telegram.bot.send_message(chat_id, "Не удалось прочитать файл. Попробуйте снова.")
                return
        else:
            try:
                mafile = json.loads(text)
            except json.JSONDecodeError:
                CARDINAL.telegram.bot.send_message(
                    chat_id, 
                    "Не удалось прочитать maFile. Отправьте корректный JSON или файл.\n\n"
                    "💡 Если у вас нет maFile, напишите: <b>нет</b>",
                    parse_mode="HTML"
                )
                return

        login = state.get("login")
        password = state.get("password")
        game = state.get("game")
        shared_secret = mafile.get("shared_secret")
        identity_secret = mafile.get("identity_secret")
        steamid = mafile.get("steamid") or (mafile.get("Session", {}) or {}).get("SteamID")
        missing_fields = [
            name
            for name, value in (
                ("shared_secret", shared_secret),
                ("identity_secret", identity_secret),
            )
            if not value
        ]
        if missing_fields:
            LOGGER.warning("maFile missing fields: %s", missing_fields)
            CARDINAL.telegram.bot.send_message(
                chat_id,
                "В maFile отсутствуют обязательные поля: {fields}.".format(
                    fields=", ".join(missing_fields),
                ),
            )
            return

        accounts = _load_accounts()
        if any(acc.get("login") == login for acc in accounts):
            CARDINAL.telegram.bot.send_message(chat_id, "Аккаунт с таким логином уже сохранён.")
            _clear_state(chat_id)
            return

        new_id = f"acc-{len(accounts) + 1:04d}"
        account_entry = {
            "id": new_id,
            "login": login,
            "password": password,
            "created_at": datetime.utcnow().isoformat(),
            "mafile": mafile,
            "steamid": steamid,
            "game": game,
            "notes": [],
            "previous_passwords": [],
        }
        accounts.append(account_entry)
        _save_accounts(accounts)
        CARDINAL.telegram.bot.send_message(
            chat_id,
            "✅ Аккаунт сохранён:\n"
            f"ID: <code>{new_id}</code>\nЛогин: <b>{login}</b>",
            parse_mode="HTML",
        )
        _clear_state(chat_id)
        return


def _process_password_change(message, state, text):
    chat_id = message.chat.id
    if state.get("step") == "choose":
        choices = state.get("choices", [])
        try:
            idx = int(text)
        except ValueError:
            CARDINAL.telegram.bot.send_message(chat_id, "📝 <b>Выбор из списка</b>\n\nВведите номер из списка или отправьте /cancel для отмены.", parse_mode="HTML")
            return
        if not (1 <= idx <= len(choices)):
            CARDINAL.telegram.bot.send_message(chat_id, "❌ <b>Неверный номер</b>\n\nПопробуйте снова или отправьте /cancel для отмены.", parse_mode="HTML")
            return
        account_id = choices[idx - 1]
        _set_state(chat_id, mode="password", step="new_password", account_id=account_id)
        CARDINAL.telegram.bot.send_message(
            chat_id,
            "Введите новый пароль или отправьте '-' для генерации случайного.",
        )
        return

    if state.get("step") == "new_password":
        account_id = state.get("account_id")
        if account_id is None:
            CARDINAL.telegram.bot.send_message(chat_id, "❌ <b>Ошибка</b>\n\nАккаунт не найден. Начните заново.", parse_mode="HTML")
            _clear_state(chat_id)
            return
        if text in {"-", "генерировать", "generate", "auto"}:
            new_password = generate_password()
        else:
            if len(text) < 4:
                CARDINAL.telegram.bot.send_message(chat_id, "❌ <b>Пароль слишком короткий</b>\n\n📝 Введите другой пароль или отправьте <code>'-'</code> для генерации случайного.\n\n💡 Или отправьте /cancel для отмены.", parse_mode="HTML")
                return
            new_password = text

        CARDINAL.telegram.bot.send_message(chat_id, "Запускаю смену пароля, подождите...")
        _clear_state(chat_id)
        thread = threading.Thread(
            target=_change_password_worker,
            args=(chat_id, account_id, new_password),
            daemon=True,
        )
        thread.start()
        return


def _process_guard_code(message, state, text):
    chat_id = message.chat.id
    if state.get("step") != "choose":
        return
    choices = state.get("choices", [])
    try:
        idx = int(text)
    except ValueError:
        CARDINAL.telegram.bot.send_message(chat_id, "Введите номер из списка.")
        return
    if not (1 <= idx <= len(choices)):
        CARDINAL.telegram.bot.send_message(chat_id, "Неверный номер. Попробуйте снова.")
        return
    account_id = choices[idx - 1]
    accounts = _load_accounts()
    account = next((acc for acc in accounts if acc.get("id") == account_id), None)
    if account is None:
        CARDINAL.telegram.bot.send_message(chat_id, "Аккаунт не найден. Обновите список.")
        _clear_state(chat_id)
        return
    shared_secret = (account.get("mafile") or {}).get("shared_secret")
    if not shared_secret:
        CARDINAL.telegram.bot.send_message(chat_id, "Для аккаунта отсутствует shared_secret.")
        _clear_state(chat_id)
        return
    code = generate_guard_code(shared_secret)
    CARDINAL.telegram.bot.send_message(
        chat_id,
        "Код Steam Guard для <b>{login}</b>: <code>{code}</code>\n"
        "(действителен ~30 секунд)"
        .format(login=account.get("login", "?"), code=code),
        parse_mode="HTML",
    )
    _clear_state(chat_id)


def _change_password_worker(chat_id: Optional[int], account_id: str, new_password: str) -> None:
    async def _inner() -> None:
        accounts = _load_accounts()
        account = next((acc for acc in accounts if acc.get("id") == account_id), None)
        if account is None:
            if chat_id is not None:
                CARDINAL.telegram.bot.send_message(chat_id, "Аккаунт не найден.")
            return
        mafile = account.get("mafile") or {}
        steamid = mafile.get("steamid") or (mafile.get("Session", {}) or {}).get("SteamID")
        try:
            steam = CustomSteam(
                login=account.get("login"),
                password=account.get("password"),
                steamid=int(steamid or 0) or None,
                shared_secret=mafile.get("shared_secret"),
                identity_secret=mafile.get("identity_secret"),
                device_id=mafile.get("device_id"),
            )
            changer = SteamPasswordChange(steam)
            await changer.change(new_password)
        except Exception as exc:                
            LOGGER.error("Смена пароля не удалась: %s", exc)
            if chat_id is not None:
                CARDINAL.telegram.bot.send_message(
                    chat_id,
                    f"❌ Не удалось сменить пароль: {exc}",
                )
            return

        old_password = account.get("password")
        if old_password:
            account.setdefault("previous_passwords", []).append(old_password)
        account["password"] = new_password
        account["updated_at"] = datetime.utcnow().isoformat()
        _save_accounts(accounts)
        if chat_id is not None:
            CARDINAL.telegram.bot.send_message(
                chat_id,
                "✅ Пароль обновлён.\n"
                "Аккаунт: <b>{login}</b>\nНовый пароль: <code>{password}</code>"
                .format(login=account.get("login", "?"), password=new_password),
                parse_mode="HTML",
            )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_inner())
    finally:
        loop.close()


async def _check_rental_expiration_warnings() -> None:
           
    rentals = _load_rentals()
    accounts = _load_accounts()
    now = datetime.utcnow()
    warning_time = timedelta(minutes=10)
    
    for rental in rentals:
        if rental.get("status") != "active":
            continue
        
                                                          
        if rental.get("expiration_warning_sent"):
            continue
        
        expires_at = rental.get("expires_at")
        if not expires_at:
            continue
        
        try:
            expires_dt = datetime.fromisoformat(expires_at)
        except ValueError:
            continue
        
                                                    
        time_left = expires_dt - now
        
        if timedelta(0) < time_left <= warning_time:
                                       
            chat_id = rental.get("chat_id")
            account_id = rental.get("account_id")
            
            if not chat_id:
                continue
            
            account = next((acc for acc in accounts if acc.get("id") == account_id), None)
            if not account:
                continue
            
            minutes_left = int(time_left.total_seconds() / 60)
            
            message = (
                "╔═══════════════════════════╗\n"
                "║  ⏰ НАПОМИНАНИЕ!          ║\n"
                "╚═══════════════════════════╝\n\n"
                f"⚠️ Аренда заканчивается через {minutes_left} минут!\n\n"
                f"👤 Аккаунт: {account.get('login')}\n"
                f"🎮 Игра: {rental.get('game', '—')}\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "📌 После окончания:\n"
                "   └ Пароль будет изменен\n"
                "   └ Доступ закроется\n\n"
                "💡 Хотите продлить?\n"
                "   └ Напишите продавцу\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "💬 Спасибо за аренду!"
            )
            
            if CARDINAL:
                success = _send_funpay_message(CARDINAL, chat_id, message)
                if success:
                    rental["expiration_warning_sent"] = True
                    LOGGER.info(
                        "%s Sent expiration warning for rental %s (%d min left)",
                        LOGGER_PREFIX,
                        rental.get("id"),
                        minutes_left,
                    )
    
                                                               
    _save_rentals(rentals)


async def _check_expired_rentals() -> None:
    rentals = _load_rentals()
    accounts = _load_accounts()
    now = datetime.utcnow()
    updated = False
    for rental in rentals:
        if rental.get("status") != "active":
            continue
        expires_at = rental.get("expires_at")
        try:
            expires_dt = datetime.fromisoformat(expires_at) if expires_at else None
        except ValueError:
            LOGGER.warning(
                "Scheduler: invalid expires_at for rental %s -> %s",
                rental.get("id"),
                expires_at,
            )
            expires_dt = None
        if expires_dt and expires_dt <= now:
            LOGGER.info(
                "Scheduler finishing rental %s (expires_dt=%s)",
                rental.get("id"),
                expires_dt.isoformat(),
            )
            rental["status"] = "finished"
            rental["finished_at"] = now.isoformat()
            account = next((acc for acc in accounts if acc.get("id") == rental.get("account_id")), None)
            if account:
                                          
                mafile = account.get("mafile") or {}
                has_mafile = bool(mafile.get("shared_secret"))
                
                if has_mafile:
                                                               
                    new_password = await _rotate_password(account)
                    if new_password is not None:
                        old_password = account.get("password")
                        if old_password and old_password != new_password:
                            account.setdefault("previous_passwords", []).append(old_password)
                        account["password"] = new_password
                        account["updated_at"] = datetime.utcnow().isoformat()
                        account["last_password_change"] = datetime.utcnow().isoformat()
                        account.setdefault("notes", []).append(
                            f"Пароль сменён при автозавершении #{rental.get('id')}"
                        )
                        LOGGER.info(
                            "Scheduler rotated password for account %s after rental %s",
                            account.get("login"),
                            rental.get("id"),
                        )
                    else:
                        account.setdefault("notes", []).append(
                            f"Не удалось сменить пароль при автозавершении #{rental.get('id')}"
                        )
                        LOGGER.error(
                            "Scheduler failed to rotate password for account %s after rental %s",
                            account.get("login"),
                            rental.get("id"),
                        )
                else:
                                                                        
                    old_password = account.get("password")
                    new_password = generate_password()                           
                    
                                                              
                    if CARDINAL and hasattr(CARDINAL, "telegram"):
                        admin_ids = getattr(CARDINAL.telegram, "admin_ids", [])
                        for admin_id in admin_ids[:1]:
                            try:
                                notification = (
                                    "╔═══════════════════════════╗\n"
                                    "║  🔔 АРЕНДА ЗАВЕРШЕНА      ║\n"
                                    "╚═══════════════════════════╝\n\n"
                                    f"⏰ Аренда #{rental.get('id')} завершена\n\n"
                                    "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                                    "🎮 АККАУНТ БЕЗ MAFILE:\n\n"
                                    f"👤 Логин:\n"
                                    f"   └ <code>{account.get('login')}</code>\n\n"
                                    f"🔑 Текущий пароль:\n"
                                    f"   └ <code>{old_password}</code>\n\n"
                                    f"🆕 Новый пароль:\n"
                                    f"   └ <code>{new_password}</code>\n\n"
                                    "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                                    "⚠️ ТРЕБУЕТСЯ РУЧНАЯ СМЕНА!\n\n"
                                    "📌 Действия:\n"
                                    "   1. Зайдите на аккаунт\n"
                                    "   2. Смените пароль на новый\n"
                                    "   3. Аккаунт снова готов к аренде\n\n"
                                    f"🎮 Игра: {account.get('game', '—')}"
                                )
                                CARDINAL.telegram.bot.send_message(
                                    admin_id,
                                    notification,
                                    parse_mode="HTML"
                                )
                                LOGGER.info(
                                    "Scheduler sent manual password change notification for account %s",
                                    account.get("login"),
                                )
                            except Exception as exc:
                                LOGGER.error(
                                    "Failed to send manual password change notification: %s",
                                    exc,
                                )
                    
                                                                       
                    account.setdefault("notes", []).append(
                        f"Требуется ручная смена пароля после аренды #{rental.get('id')} "
                        f"(БЕЗ maFile, новый пароль: {new_password})"
                    )
                    account["pending_password_change"] = {
                        "new_password": new_password,
                        "rental_id": rental.get("id"),
                        "notified_at": now.isoformat(),
                    }
                    LOGGER.info(
                        "Scheduler marked account %s for manual password change (no maFile)",
                        account.get("login"),
                    )
                
                account["status"] = "active"
                
                # Отправляем уведомление администраторам в Telegram о завершении аренды
                if CARDINAL and hasattr(CARDINAL, "telegram"):
                    admin_ids = getattr(CARDINAL.telegram, "admin_ids", [])
                    for admin_id in admin_ids:
                        try:
                            admin_message = (
                                "🔔 <b>Аренда завершена (автоматически)</b>\n\n"
                                f"🆔 ID аренды: <code>{rental.get('id')}</code>\n"
                                f"👤 Логин: <code>{account.get('login', '—')}</code>\n"
                                f"🎮 Игра: <b>{rental.get('game', '—')}</b>\n"
                                f"⏰ Завершена: {utc_to_moscow(now.replace(tzinfo=timezone.utc)).strftime('%d.%m.%Y %H:%M')}\n"
                                f"📦 Лот ID: <code>{rental.get('lot_id', '—')}</code>"
                            )
                            CARDINAL.telegram.bot.send_message(admin_id, admin_message, parse_mode="HTML")
                        except Exception as exc:
                            LOGGER.error("%s Failed to send admin notification for finished rental %s: %s", LOGGER_PREFIX, rental.get("id"), exc)
                
                # Обновляем статус лота, если он был указан
                lot_id = rental.get("lot_id")
                if lot_id and CARDINAL:
                    _update_lot_status_if_needed(CARDINAL, lot_id)
            else:
                LOGGER.error(
                    "Scheduler: account %s not found for rental %s",
                    rental.get("account_id"),
                    rental.get("id"),
                )
            updated = True
        else:
            LOGGER.debug(
                "Scheduler: rental %s still active (expires_dt=%s)",
                rental.get("id"),
                expires_dt.isoformat() if expires_dt else None,
            )
    if updated:
        LOGGER.info("Scheduler: saving rentals/accounts after updates")
        _save_rentals(rentals)
        _save_accounts(accounts)
        
        # Обновляем статусы всех лотов после изменения статусов аккаунтов
        if CARDINAL:
            lots = _load_lots()
            for lot in lots:
                lot_id = lot.get("lot_id")
                if lot_id:
                    _update_lot_status_if_needed(CARDINAL, lot_id)


async def _check_rental_timers() -> None:
    rentals = _load_rentals()
    now = datetime.utcnow()
    updated = False
    for rental in rentals:
        for timer in rental.get("timers", []):
            if timer.get("fired"):
                continue
            due_ts = timer.get("due_ts")
            if due_ts is None:
                continue
            due_dt = datetime.fromtimestamp(due_ts)
            if due_dt <= now:
                timer["fired"] = True
                timer["fired_at"] = now.isoformat()
                account = _get_account_by_id(rental.get("account_id")) or {}
                message = (
                    "⏰ <b>Напоминание по аренде</b>\n"
                    f"Аренда: <code>{rental.get('id')}</code>\n"
                    f"Аккаунт: <b>{account.get('login', '?')}</b>\n"
                    f"Комментарий: {timer.get('note') or '—'}"
                )
                chat_id = timer.get("chat_id") or getattr(CARDINAL.telegram, "admin_ids", [None])[0]
                if chat_id:
                    try:
                        await CARDINAL.telegram.bot.send_message(chat_id, message, parse_mode="HTML")
                    except Exception as exc:                
                        LOGGER.error("Failed to send timer notification: %s", exc)
                updated = True
    if updated:
        _save_rentals(rentals)


async def _rotate_password(account: Dict[str, Any]) -> Optional[str]:
    mafile = account.get("mafile") or {}
    shared_secret = mafile.get("shared_secret")
    if not shared_secret:
        LOGGER.warning("Cannot rotate password for %s: no maFile", account.get("login"))
        return None
    steamid = mafile.get("steamid") or (mafile.get("Session", {}) or {}).get("SteamID")
    try:
        steam = CustomSteam(
            login=account.get("login"),
            password=account.get("password"),
            steamid=int(steamid or 0) or None,
            shared_secret=shared_secret,
            identity_secret=mafile.get("identity_secret"),
            device_id=mafile.get("device_id"),
        )
        changer = SteamPasswordChange(steam)
        new_password = generate_password()
        await changer.change(new_password)
        old_password = account.get("password")
        if old_password and old_password != new_password:
            account.setdefault("previous_passwords", []).append(old_password)
        account["password"] = new_password
        account["updated_at"] = datetime.utcnow().isoformat()
        account["last_password_change"] = datetime.utcnow().isoformat()
        LOGGER.info("Password rotated for %s", account.get("login"))
        return new_password
    except Exception as exc:                
        LOGGER.error("Failed to rotate password for %s: %s", account.get("login"), exc)
        return None


def _rotate_password_sync(account: Dict[str, Any]) -> Optional[str]:
    async def _inner() -> Optional[str]:
        return await _rotate_password(account)

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(_inner())
    except Exception as exc:                
        LOGGER.error("Failed to rotate password in sync mode for %s: %s", account.get("login"), exc)
        return None
    finally:
        asyncio.set_event_loop(None)
        loop.close()


def _rent_callback_handler(call):
                                                         
    _handle_button(call)


def _complete_plugin_initialization() -> bool:
           
    global _STOP_EVENT, _PLUGIN_LOADED
    
    if _PLUGIN_LOADED:
        LOGGER.info("%s Plugin already loaded, skipping initialization", LOGGER_PREFIX)
        return True
    
    try:
        _ensure_storage()
        _load_lot_templates()
        if _STOP_EVENT is None:
            _STOP_EVENT = threading.Event()
        _start_background_tasks()

        CARDINAL.add_telegram_commands(
            UUID,
            [
                (MENU_COMMAND, "Меню аренды Steam", True),
            ],
        )

        CARDINAL.telegram.msg_handler(show_menu, commands=[MENU_COMMAND])

                                                                         
        @CARDINAL.telegram.bot.callback_query_handler(
            func=lambda call: (
                call.data.startswith("rent_sd:")
                or call.data in PRIVATE_CALLBACKS
                or call.data.startswith(_PRIVATE_PREFIX)
                or call.data.startswith(_ACCOUNTS_PREFIX)
                or call.data.startswith(_RENTAL_PREFIX)
                or call.data.startswith("lots:")
                or call.data.startswith("monitor:")
                or call.data.startswith("logs:")
            )
        )
        def _plugin_callback_router(call):
            _handle_button(call)

        CARDINAL.telegram.msg_handler(_process_state_message, content_types=["text", "document"])

                                                                                  
                                                  
                                                                                  
        handle_funpay_message.plugin_uuid = UUID
        if handle_funpay_message not in CARDINAL.new_message_handlers:
            CARDINAL.new_message_handlers.append(handle_funpay_message)
            LOGGER.info("%s FunPay message handler registered successfully", LOGGER_PREFIX)
        else:
            LOGGER.warning("%s FunPay message handler already registered", LOGGER_PREFIX)
        
        _PLUGIN_LOADED = True
        LOGGER.info("%s ✅ Plugin fully loaded and operational!", LOGGER_PREFIX)
        return True
        
    except Exception as e:
        LOGGER.error("%s Failed to complete plugin initialization: %s", LOGGER_PREFIX, e, exc_info=True)
        return False


def init_plugin(cardinal):              
    global CARDINAL, _STOP_EVENT
    CARDINAL = cardinal
    LOGGER.info("%s Plugin init", LOGGER_PREFIX)
    
    # Проверка лицензии при инициализации
    tg_id = get_tg_id_from_cache()
    if tg_id:
        is_valid, message = license_manager.check_license(tg_id)
        if not is_valid:
            LOGGER.critical("%s Плагин не активирован при запуске: %s", LOGGER_PREFIX, message)
            if hasattr(cardinal, 'telegram') and hasattr(cardinal.telegram, 'bot'):
                try:
                    cardinal.telegram.bot.send_message(
                        tg_id,
                        f"✅ Плагин {NAME}  АКТИВИРОВАН!\n"
                        f"♫♪♬ ヽ(◕‿◕✿)ノ ♪♫♬   ♫♪ヽ(✿◕‿◕)"
                        f"┏(◕‿◕)┛ ♪┗(◕‿◕)┓ ♪ ┗(◕‿◕)┛♫"
                    )
                except:
                    pass
        else:
            LOGGER.info("%s Лицензия активна", LOGGER_PREFIX)
    
    # Продолжаем инициализацию только если лицензия действительна
    _complete_plugin_initialization()

def show_license_menu(message_or_call) -> None:
    """Показывает меню управления лицензией"""
    tg_id = get_tg_id_from_cache()
    is_valid, message_text = license_manager.check_license(tg_id) if tg_id else (False, "TG ID не найден")
    
    # Определяем тип объекта по атрибутам (без прямого использования telebot.types)
    is_callback = hasattr(message_or_call, 'data') and hasattr(message_or_call, 'message') and hasattr(message_or_call, 'id')
    
    if is_callback:  # Это CallbackQuery
        call = message_or_call
        chat_id = call.message.chat.id
        message_id = call.message.id
    else:  # Это обычное Message
        message = message_or_call
        chat_id = message.chat.id
        message_id = None
    
    keyboard = InlineKeyboardMarkup(row_width=1)
    
    if is_valid:
        status_text = "🟢 АКТИВНА"
        license_data = license_manager.get_license_data()
        activated_at = license_data.get('activated_at', 'Неизвестно')
        
        text = f"""
🔐 <b>Управление лицензией</b>

📊 <b>Статус:</b> {status_text}
🆔 <b>TG ID:</b> {tg_id}
💻 <b>Версия:</b> {VERSION}
📅 <b>Активирована:</b> {activated_at}

<b>Доступные действия:</b>
"""
        keyboard.add(InlineKeyboardButton("🔄 Проверить статус", callback_data="rent_sd:check_license"))
        keyboard.add(InlineKeyboardButton("◀️ Назад", callback_data="section:back"))
    else:
        status_text = "🔴 НЕАКТИВНА"
        text = (
            f"<b>🎮 {NAME}</b> <code>v{VERSION}</code>\n\n"
            f"<blockquote>"
            f"📝 <b>Описание:</b> {DESCRIPTION}\n"
            f"👨‍💻 <b>Автор:</b> {CREDITS}\n"
            f"🔑 <b>Лицензия:</b> 🔴 Неактивна\n\n"
            f"⚠️ <b>Плагин не активирован!</b>\n"
            f"❌ <b>Причина:</b> {message_text}\n\n"
            f"Для активации обратитесь к @veemp (не обращаться, за вырезом @tokenpast)"
            f"</blockquote>\n\n"
        )
        keyboard.add(InlineKeyboardButton("🔄 Проверить статус", callback_data="rent_sd:check_license"))
        keyboard.add(InlineKeyboardButton("🛒 Купить плагин (dont buy)", url="https://t.me/veemp_shop"))
    
    if message_id:
        CARDINAL.telegram.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        if is_callback:
            CARDINAL.telegram.bot.answer_callback_query(message_or_call.id)
    else:
        CARDINAL.telegram.bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")


def _back_markup() -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("⬅️ Назад", callback_data="section:accounts"))
    return markup


def _mask_password(password: Optional[str]) -> str:
    if not password:
        return "—"
    if len(password) <= 2:
        return "*" * len(password)
    return f"{password[0]}{'*' * (len(password) - 2)}{password[-1]}"


def _format_account_line(index: int, account: Dict[str, Any]) -> str:
    status = account.get("status", "active")
    status_label = ACCOUNT_STATUS_LABELS.get(status, status)
    game = account.get("game")
    game_part = f" — {game}" if game else ""
    return (
        f"{index}. <b>{account.get('login', '?')}</b> (#{account.get('id')}){game_part}\n"
        f"   {status_label}"
    )


def _format_account_line_short(account: Dict[str, Any]) -> str:
    """Форматирует аккаунт в кратком виде для списка"""
    status = account.get("status", "active")
    status_emoji = "🟢" if status == "active" else "❄️" if status == "frozen" else "🔴"
    login = account.get('login', '?')
    game = account.get("game", "—")
    return f"・ {status_emoji} <b>{login}</b> — {game}"


def _build_accounts_section_text() -> str:
    """Формирует текст для раздела управления аккаунтами с кратким списком"""
    accounts = _load_accounts()
    
    if not accounts:
        return (
            "👥 <b>Управление аккаунтами</b>\n\n"
            "❌ Список аккаунтов пуст.\n"
            "➕ Добавьте аккаунт, чтобы начать работу.\n\n"
            "🔧 Выберите действие:"
        )
    
    # Подсчет статистики
    total = len(accounts)
    active_count = sum(1 for acc in accounts if acc.get("status") == "active")
    frozen_count = sum(1 for acc in accounts if acc.get("status") == "frozen")
    busy_count = sum(1 for acc in accounts if acc.get("status") == "busy")
    
    # Формируем текст
    text = "👥 <b>Управление аккаунтами</b>\n\n"
    text += f"📊 <b>Статистика:</b>\n"
    text += f"・ Всего: <b>{total}</b>\n"
    text += f"・ 🟢 Активны: <b>{active_count}</b>\n"
    text += f"・ ❄️ Заморожены: <b>{frozen_count}</b>\n"
    text += f"・ 🔴 Выданы: <b>{busy_count}</b>\n\n"
    
    # Показываем краткий список аккаунтов (максимум 10 первых)
    text += "📋 <b>Список аккаунтов:</b>\n"
    display_accounts = accounts[:10]  # Показываем первые 10
    for acc in display_accounts:
        text += _format_account_line_short(acc) + "\n"
    
    if len(accounts) > 10:
        text += f"\n... и ещё {len(accounts) - 10} аккаунтов\n"
    
    text += "\n🔧 <b>Выберите действие:</b>"
    
    return text


def _send_account_list(chat_id: int) -> None:
    accounts = _load_accounts()
    if not accounts:
        CARDINAL.telegram.bot.send_message(
            chat_id,
            "📋 <b>Список аккаунтов пуст.</b>",
            parse_mode="HTML",
            reply_markup=_back_markup(),
        )
        return
    lines = [_format_account_line(idx, acc) for idx, acc in enumerate(accounts, start=1)]
    text = "<b>📋 Список аккаунтов</b>\n" + "\n".join(lines)
    CARDINAL.telegram.bot.send_message(
        chat_id,
        text,
        parse_mode="HTML",
        reply_markup=_back_markup(),
    )


def _prepare_account_choice(chat_id: int, mode: str, prompt: str) -> bool:
    accounts = _load_accounts()
    if not accounts:
        CARDINAL.telegram.bot.send_message(
            chat_id,
            "<b>Список аккаунтов пуст.</b>",
            parse_mode="HTML",
            reply_markup=_back_markup(),
        )
        return False
    lines = [_format_account_line(idx, acc) for idx, acc in enumerate(accounts, start=1)]
    _set_state(
        chat_id,
        mode=mode,
        step="choose",
        choices=[acc.get("id") for acc in accounts],
    )
    CARDINAL.telegram.bot.send_message(
        chat_id,
        f"{prompt}\n" + "\n".join(lines) + "\n\nОтправьте номер или /cancel.",
        parse_mode="HTML",
    )
    return True


def _handle_accounts_callback(call) -> None:
    chat_id = call.message.chat.id
    action = call.data.split(":", 1)[1]
    if action == "list":
        _send_account_list(chat_id)
        return
    if action == "view":
        _prepare_account_choice(chat_id, "accounts_view", "<b>Выберите аккаунт для просмотра:</b>")
        return
    if action == "edit":
        _prepare_account_choice(chat_id, "accounts_edit", "<b>Выберите аккаунт для редактирования:</b>")
        return
    if action == "freeze":
        _prepare_account_choice(chat_id, "accounts_freeze", "<b>Выберите аккаунт для заморозки/разморозки:</b>")
        return
    if action == "delete":
        _prepare_account_choice(chat_id, "accounts_delete", "<b>Выберите аккаунт для удаления:</b>")
        return


def _get_account_by_id(account_id: str) -> Optional[Dict[str, Any]]:
    return next((acc for acc in _load_accounts() if acc.get("id") == account_id), None)


def _handle_rental_callback(call) -> None:
    chat_id = call.message.chat.id
    action = call.data.split(":", 1)[1]
    if action == "issue":
        _start_rental_issue(chat_id)
        return
    if action == "active":
        _send_active_rentals(chat_id)
        return
    if action == "finish":
        _prepare_rental_choice(chat_id, "rental_finish", "<b>Выберите аренду для завершения:</b>")
        return
    if action == "extend":
        _prepare_rental_choice(chat_id, "rental_extend", "<b>Выберите аренду для продления:</b>")
        return
    if action == "cancel":
        _prepare_rental_choice(chat_id, "rental_cancel", "<b>Выберите аренду для отмены:</b>")
        return


def _start_rental_issue(chat_id: int) -> None:
    accounts = _load_accounts()
    available_accounts = [acc for acc in accounts if acc.get("status") == "active"]
    if not available_accounts:
        CARDINAL.telegram.bot.send_message(
            chat_id,
            "Нет доступных аккаунтов для выдачи. Добавьте или разморозьте аккаунты.",
            reply_markup=_back_markup(),
        )
        return
    games = sorted({acc.get("game", "Без категории") for acc in available_accounts})
    _set_state(
        chat_id,
        mode="rental_issue",
        step="choose_game",
        games=games,
    )
    lines = [f"{idx}. {game}" for idx, game in enumerate(games, start=1)]
    CARDINAL.telegram.bot.send_message(
        chat_id,
        "<b>Выбор игры/категории</b>\n" + "\n".join(lines) + "\n\nОтправьте номер игры.",
        parse_mode="HTML",
    )


def _send_active_rentals(chat_id: int) -> None:
    rentals = _load_rentals()
    active_rentals = [rent for rent in rentals if rent.get("status") == "active"]
    if not active_rentals:
        CARDINAL.telegram.bot.send_message(
            chat_id,
            "Активных аренд нет.",
            reply_markup=_back_markup(),
        )
        return
    accounts_map = {acc.get("id"): acc for acc in _load_accounts()}
    lines: List[str] = []
    now = datetime.utcnow()
    for rental in active_rentals:
        account = accounts_map.get(rental.get("account_id"), {})
        login = account.get("login", "?")
        game = rental.get("game", "—")
        expires_at = rental.get("expires_at")
        try:
            expires_dt = datetime.fromisoformat(expires_at) if expires_at else None
        except ValueError:
            expires_dt = None
        if expires_dt:
            remaining_hours = max((expires_dt - now).total_seconds() / 3600, 0)
            expiry_str = expires_dt.strftime("%d.%m %H:%M")
            remain_str = f"{remaining_hours:.1f} ч"
        else:
            expiry_str = "—"
            remain_str = "—"
        lines.append(
            f"・ <b>{login}</b> ({game})\n"
            f"   до {expiry_str} (осталось {remain_str})"
        )
    CARDINAL.telegram.bot.send_message(
        chat_id,
        "<b>📋 Активные аренды</b>\n" + "\n".join(lines),
        parse_mode="HTML",
        reply_markup=_back_markup(),
    )


def _process_rental_issue(message, state, text):
    chat_id = message.chat.id
    step = state.get("step")

    if step == "choose_game":
        games: List[str] = state.get("games", [])
        try:
            idx = int(text)
        except ValueError:
            CARDINAL.telegram.bot.send_message(chat_id, "📝 <b>Выбор игры</b>\n\nВведите номер игры из списка или отправьте /cancel для отмены.", parse_mode="HTML")
            return
        if not (1 <= idx <= len(games)):
            CARDINAL.telegram.bot.send_message(chat_id, "❌ <b>Неверный номер</b>\n\nПопробуйте снова или отправьте /cancel для отмены.", parse_mode="HTML")
            return
        game = games[idx - 1]
        accounts = [
            acc for acc in _load_accounts() if acc.get("status") == "active" and acc.get("game", "Без категории") == game
        ]
        if not accounts:
            CARDINAL.telegram.bot.send_message(
                chat_id,
                "⚠️ <b>Нет доступных аккаунтов</b>\n\nВ выбранной категории нет доступных аккаунтов.",
                parse_mode="HTML",
                reply_markup=_back_markup(),
            )
            _clear_state(chat_id)
            return
        lines = [
            f"{i}. <b>{acc.get('login','?')}</b> (#{acc.get('id')})"
            for i, acc in enumerate(accounts, start=1)
        ]
        _set_state(
            chat_id,
            mode="rental_issue",
            step="choose_account",
            game=game,
            accounts=[acc.get("id") for acc in accounts],
        )
        CARDINAL.telegram.bot.send_message(
            chat_id,
            "<b>Выберите аккаунт для выдачи</b>\n" + "\n".join(lines) + "\n\nОтправьте номер.",
            parse_mode="HTML",
        )
        return

    if step == "choose_account":
        accounts: List[str] = state.get("accounts", [])
        try:
            idx = int(text)
        except ValueError:
            CARDINAL.telegram.bot.send_message(chat_id, "📝 <b>Выбор аккаунта</b>\n\nВведите номер аккаунта из списка или отправьте /cancel для отмены.", parse_mode="HTML")
            return
        if not (1 <= idx <= len(accounts)):
            CARDINAL.telegram.bot.send_message(chat_id, "❌ <b>Неверный номер</b>\n\nПопробуйте снова или отправьте /cancel для отмены.", parse_mode="HTML")
            return
        account_id = accounts[idx - 1]
        _set_state(
            chat_id,
            mode="rental_issue",
            step="enter_hours",
            game=state.get("game"),
            account_id=account_id,
        )
        CARDINAL.telegram.bot.send_message(chat_id, "⏰ <b>Срок аренды</b>\n\n📝 Введите срок аренды в часах (например: <code>12</code> или <code>1.5</code>)\n\n💡 Или отправьте /cancel для отмены.", parse_mode="HTML")
        return

    if step == "enter_hours":
        try:
            hours = float(text.replace(",", "."))
        except ValueError:
            CARDINAL.telegram.bot.send_message(chat_id, "❌ <b>Неверный формат</b>\n\n📝 Введите число часов (например: <code>12</code> или <code>1.5</code>)\n\n💡 Или отправьте /cancel для отмены.", parse_mode="HTML")
            return
        if hours <= 0:
            CARDINAL.telegram.bot.send_message(chat_id, "❌ <b>Неверное значение</b>\n\nКоличество часов должно быть больше нуля.\n\n💡 Или отправьте /cancel для отмены.", parse_mode="HTML")
            return
        _set_state(
            chat_id,
            mode="rental_issue",
            step="enter_note",
            game=state.get("game"),
            account_id=state.get("account_id"),
            hours=hours,
        )
        CARDINAL.telegram.bot.send_message(chat_id, "💬 <b>Комментарий к аренде</b>\n\n📝 Добавьте комментарий или отправьте <code>'-'</code> если без комментария\n\n💡 Или отправьте /cancel для отмены.", parse_mode="HTML")
        return

    if step == "enter_note":
        note = text if text != "-" else ""
        account_id = state.get("account_id")
        hours = state.get("hours")
        game = state.get("game")
        account = _get_account_by_id(account_id)
        if account is None:
            CARDINAL.telegram.bot.send_message(chat_id, "Аккаунт не найден. Начните заново.")
            _clear_state(chat_id)
            return

        rentals = _load_rentals()
        new_id = f"rent-{len(rentals) + 1:04d}"
        created_at = datetime.utcnow().isoformat()
        expires_at = (datetime.utcnow() + timedelta(hours=hours)).isoformat()
        rental_entry = {
            "id": new_id,
            "account_id": account_id,
            "game": game,
            "status": "active",
            "created_at": created_at,
            "expires_at": expires_at,
            "hours": hours,
            "note": note,
        }
        rentals.append(rental_entry)
        _save_rentals(rentals)

        accounts = _load_accounts()
        for acc in accounts:
            if acc.get("id") == account_id:
                acc["status"] = "busy"
                note_suffix = f" Примечание: {note}" if note else ""
                acc.setdefault("notes", []).append(
                    f"Выдана аренда #{new_id} ({created_at}).{note_suffix}"
                )
                break
        _save_accounts(accounts)

        CARDINAL.telegram.bot.send_message(
            chat_id,
            "✅ Аренда создана\n"
            "ID: <code>{rental_id}</code>\n"
            "Аккаунт: <b>{login}</b>\n"
            "Игра: <b>{game}</b>\n"
            "Истекает: <code>{expires}</code>"
            .format(
                rental_id=new_id,
                login=account.get("login", "?"),
                game=game,
                expires=utc_to_moscow(datetime.fromisoformat(expires_at)).strftime("%d.%m.%Y %H:%M"),
            ),
            parse_mode="HTML",
            reply_markup=_back_markup(),
        )
        
        # Отправляем уведомление администраторам в Telegram
        if CARDINAL and hasattr(CARDINAL, "telegram"):
            admin_ids = getattr(CARDINAL.telegram, "admin_ids", [])
            for admin_id in admin_ids:
                try:
                    admin_message = (
                        "✅ <b>Аккаунт успешно выдан</b>\n\n"
                        f"🆔 ID аренды: <code>{new_id}</code>\n"
                        f"🎮 Игра: <b>{game}</b>\n"
                        f"👤 Логин: <code>{account.get('login')}</code>\n"
                        f"⏰ Срок: {hours} ч. (до {utc_to_moscow(datetime.fromisoformat(expires_at)).strftime('%d.%m.%Y %H:%M')})\n"
                        f"📝 Примечание: {note if note else '—'}"
                    )
                    CARDINAL.telegram.bot.send_message(admin_id, admin_message, parse_mode="HTML")
                except Exception as exc:
                    LOGGER.error("%s Failed to send admin notification for rental %s: %s", LOGGER_PREFIX, new_id, exc)
        _clear_state(chat_id)
        return


def _prepare_rental_choice(chat_id: int, mode: str, prompt: str) -> bool:
    rentals = _load_rentals()
    active_rentals = [rent for rent in rentals if rent.get("status") == "active"]
    if not active_rentals:
        CARDINAL.telegram.bot.send_message(
            chat_id,
            "Активных аренд нет.",
            reply_markup=_back_markup(),
        )
        return False
    accounts_map = {acc.get("id"): acc for acc in _load_accounts()}
    lines = []
    rental_ids = []
    for idx, rent in enumerate(active_rentals, start=1):
        account = accounts_map.get(rent.get("account_id"), {})
        login = account.get("login", "?")
        game = rent.get("game", "—")
        expires_at = rent.get("expires_at")
        try:
            expires_dt = datetime.fromisoformat(expires_at) if expires_at else None
            expires_str = expires_dt.strftime("%d.%m %H:%M") if expires_dt else "—"
        except ValueError:
            expires_str = expires_at or "—"
        lines.append(
            f"{idx}. <b>{login}</b> ({game}) — до {expires_str}"
        )
        rental_ids.append(rent.get("id"))
    _set_state(
        chat_id,
        mode=mode,
        step="choose",
        rental_ids=rental_ids,
    )
    CARDINAL.telegram.bot.send_message(
        chat_id,
        f"{prompt}\n" + "\n".join(lines) + "\n\nОтправьте номер или /cancel.",
        parse_mode="HTML",
    )
    return True


def _select_rental(message, state, text):
    chat_id = message.chat.id
    rental_ids = state.get("rental_ids", [])
    try:
        idx = int(text)
    except ValueError:
        CARDINAL.telegram.bot.send_message(chat_id, "📝 <b>Выбор аренды</b>\n\nВведите номер аренды из списка или отправьте /cancel для отмены.", parse_mode="HTML")
        return None
    if not (1 <= idx <= len(rental_ids)):
        CARDINAL.telegram.bot.send_message(chat_id, "❌ <b>Неверный номер</b>\n\nПопробуйте снова или отправьте /cancel для отмены.", parse_mode="HTML")
        return None
    rental_id = rental_ids[idx - 1]
    rentals = _load_rentals()
    rental = next((rent for rent in rentals if rent.get("id") == rental_id), None)
    if rental is None:
        CARDINAL.telegram.bot.send_message(chat_id, "❌ <b>Аренда не найдена</b>\n\nОбновите список или отправьте /cancel для отмены.", parse_mode="HTML")
        _clear_state(chat_id)
        return None
    return rental_id, rental, rentals


def _process_rental_finish(message, state, text):
    selection = _select_rental(message, state, text)
    if selection is None:
        return
    rental_id, rental, rentals = selection
    now_iso = datetime.utcnow().isoformat()
    rental["status"] = "finished"
    rental["finished_at"] = now_iso
    _save_rentals(rentals)

    accounts = _load_accounts()
    for account in accounts:
        if account.get("id") == rental.get("account_id"):
                                      
            mafile = account.get("mafile") or {}
            has_mafile = bool(mafile.get("shared_secret"))
            
            if has_mafile:
                                             
                new_password = _rotate_password_sync(account)
                account["status"] = "active"
                if new_password is not None:
                    old_password = account.get("password")
                    if old_password and old_password != new_password:
                        account.setdefault("previous_passwords", []).append(old_password)
                    account["password"] = new_password
                    account["updated_at"] = datetime.utcnow().isoformat()
                    account["last_password_change"] = datetime.utcnow().isoformat()
                account.setdefault("notes", []).append(
                    f"Аренда #{rental_id} завершена {now_iso}."
                )
            else:
                                                                     
                old_password = account.get("password")
                new_password = generate_password()
                account["status"] = "active"
                
                                               
                try:
                    notification = (
                        "╔═══════════════════════════╗\n"
                        "║  🔔 АРЕНДА ЗАВЕРШЕНА      ║\n"
                        "╚═══════════════════════════╝\n\n"
                        f"⏰ Аренда #{rental_id} завершена вручную\n\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        "🎮 АККАУНТ БЕЗ MAFILE:\n\n"
                        f"👤 Логин:\n"
                        f"   └ <code>{account.get('login')}</code>\n\n"
                        f"🔑 Текущий пароль:\n"
                        f"   └ <code>{old_password}</code>\n\n"
                        f"🆕 Новый пароль:\n"
                        f"   └ <code>{new_password}</code>\n\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        "⚠️ ТРЕБУЕТСЯ РУЧНАЯ СМЕНА!\n\n"
                        "📌 Действия:\n"
                        "   1. Зайдите на аккаунт\n"
                        "   2. Смените пароль на новый\n"
                        "   3. Аккаунт снова готов к аренде\n\n"
                        f"🎮 Игра: {account.get('game', '—')}"
                    )
                    CARDINAL.telegram.bot.send_message(
                        message.chat.id,
                        notification,
                        parse_mode="HTML"
                    )
                except Exception as exc:
                    LOGGER.error("Failed to send manual password change notification: %s", exc)
                
                account.setdefault("notes", []).append(
                    f"Аренда #{rental_id} завершена {now_iso}. "
                    f"Требуется ручная смена пароля (новый: {new_password})"
                )
                account["pending_password_change"] = {
                    "new_password": new_password,
                    "rental_id": rental_id,
                    "notified_at": now_iso,
                }
            break
    _save_accounts(accounts)
    
    # Обновляем статус лота, если он был указан
    lot_id = rental.get("lot_id")
    if lot_id and CARDINAL:
        _update_lot_status_if_needed(CARDINAL, lot_id)

    CARDINAL.telegram.bot.send_message(
        message.chat.id,
        "✅ Аренда <code>{rental_id}</code> завершена.".format(rental_id=rental_id),
        parse_mode="HTML",
        reply_markup=_back_markup(),
    )
    
    # Отправляем уведомление администраторам в Telegram
    if CARDINAL and hasattr(CARDINAL, "telegram"):
        admin_ids = getattr(CARDINAL.telegram, "admin_ids", [])
        account = _get_account_by_id(rental.get("account_id"))
        for admin_id in admin_ids:
            try:
                admin_message = (
                    "🔔 <b>Аренда завершена</b>\n\n"
                    f"🆔 ID аренды: <code>{rental_id}</code>\n"
                    f"👤 Логин: <code>{account.get('login', '—') if account else '—'}</code>\n"
                    f"🎮 Игра: <b>{rental.get('game', '—')}</b>\n"
                    f"⏰ Завершена: {utc_to_moscow(datetime.fromisoformat(now_iso)).strftime('%d.%m.%Y %H:%M')}\n"
                    f"📦 Лот ID: <code>{rental.get('lot_id', '—')}</code>"
                )
                CARDINAL.telegram.bot.send_message(admin_id, admin_message, parse_mode="HTML")
            except Exception as exc:
                LOGGER.error("%s Failed to send admin notification for finished rental %s: %s", LOGGER_PREFIX, rental_id, exc)
    
    _clear_state(message.chat.id)


def _process_rental_cancel(message, state, text):
    selection = _select_rental(message, state, text)
    if selection is None:
        return
    rental_id, rental, rentals = selection
    now_iso = datetime.utcnow().isoformat()
    rental["status"] = "cancelled"
    rental["cancelled_at"] = now_iso
    _save_rentals(rentals)

    accounts = _load_accounts()
    for account in accounts:
        if account.get("id") == rental.get("account_id"):
            account["status"] = "active"
            account.setdefault("notes", []).append(
                f"Аренда #{rental_id} отменена {now_iso}."
            )
            break
    _save_accounts(accounts)
    
    # Обновляем статус лота, если он был указан
    lot_id = rental.get("lot_id")
    if lot_id and CARDINAL:
        _update_lot_status_if_needed(CARDINAL, lot_id)

    CARDINAL.telegram.bot.send_message(
        message.chat.id,
        "❌ Аренда <code>{rental_id}</code> отменена.".format(rental_id=rental_id),
        parse_mode="HTML",
        reply_markup=_back_markup(),
    )
    _clear_state(message.chat.id)


def _process_rental_extend(message, state, text):
    step = state.get("step")
    chat_id = message.chat.id
    if step == "choose":
        selection = _select_rental(message, state, text)
        if selection is None:
            return
        rental_id, _, _ = selection
        _set_state(
            chat_id,
            mode="rental_extend",
            step="enter_hours",
            rental_id=rental_id,
        )
        CARDINAL.telegram.bot.send_message(chat_id, "На сколько часов продлить аренду?")
        return
    if step == "enter_hours":
        try:
            hours = float(text.replace(",", "."))
        except ValueError:
            CARDINAL.telegram.bot.send_message(chat_id, "❌ <b>Неверный формат</b>\n\n📝 Введите число часов (например: <code>12</code> или <code>1.5</code>)\n\n💡 Или отправьте /cancel для отмены.", parse_mode="HTML")
            return
        if hours <= 0:
            CARDINAL.telegram.bot.send_message(chat_id, "Количество часов должно быть больше нуля.")
            return
        rental_id = state.get("rental_id")
        rentals = _load_rentals()
        rental = next((rent for rent in rentals if rent.get("id") == rental_id), None)
        if rental is None:
            CARDINAL.telegram.bot.send_message(chat_id, "Аренда не найдена. Попробуйте снова.")
            _clear_state(chat_id)
            return
        expires_at = rental.get("expires_at")
        try:
            base = datetime.fromisoformat(expires_at) if expires_at else datetime.utcnow()
        except ValueError:
            base = datetime.utcnow()
        new_expiry = base + timedelta(hours=hours)
        rental["expires_at"] = new_expiry.isoformat()
        rental.setdefault("extensions", []).append(
            {
                "extended_at": datetime.utcnow().isoformat(),
                "hours": hours,
                "new_expires": new_expiry.isoformat(),
            }
        )
        _save_rentals(rentals)
        CARDINAL.telegram.bot.send_message(
            chat_id,
            "🔁 Аренда <code>{}</code> продлена до <code>{}</code>".format(
                rental_id,
                utc_to_moscow(new_expiry).strftime("%d.%m.%Y %H:%M"),
            ),
            parse_mode="HTML",
            reply_markup=_back_markup(),
        )
        _clear_state(chat_id)
        return


def _start_background_tasks() -> None:
    global _STOP_EVENT, _SCHEDULER_THREAD

    if CARDINAL is None:
        return

    if _SCHEDULER_THREAD and _SCHEDULER_THREAD.is_alive():
        return

    _STOP_EVENT = threading.Event()

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_rental_scheduler())
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    _SCHEDULER_THREAD = threading.Thread(target=_runner, name="rent-scheduler", daemon=True)
    _SCHEDULER_THREAD.start()


def _stop_background_tasks() -> None:
    global _STOP_EVENT, _SCHEDULER_THREAD

    if _STOP_EVENT is not None:
        _STOP_EVENT.set()

    if _SCHEDULER_THREAD and _SCHEDULER_THREAD.is_alive():
        _SCHEDULER_THREAD.join(timeout=5)

    _SCHEDULER_THREAD = None
    _STOP_EVENT = None


async def _rental_scheduler() -> None:
    LOGGER.info("rent_steam_dante: scheduler started")
    try:
        while CARDINAL is not None and (_STOP_EVENT is None or not _STOP_EVENT.is_set()):
            try:
                await _check_rental_expiration_warnings()                                       
                await _check_expired_rentals()
                await _check_rental_timers()
            except Exception as exc:                
                LOGGER.exception("Scheduler tick error: %s", exc)
            await asyncio.sleep(30)
    except asyncio.CancelledError:
        LOGGER.info("rent_steam_dante: scheduler cancelled")
    finally:
        LOGGER.info("rent_steam_dante: scheduler stopped")


def _build_dashboard_stats() -> str:
    accounts = _load_accounts()
    rentals = _load_rentals()
    total_accounts = len(accounts)
    active_accounts = sum(1 for acc in accounts if acc.get("status") == "active")
    busy_accounts = sum(1 for acc in accounts if acc.get("status") == "busy")
    frozen_accounts = sum(1 for acc in accounts if acc.get("status") == "frozen")

    active_rentals = [rent for rent in rentals if rent.get("status") == "active"]
    finished_today = [
        rent
        for rent in rentals
        if rent.get("status") == "finished" and _is_same_day(rent.get("finished_at"))
    ]
    issued_today = [
        rent
        for rent in rentals
        if rent.get("status") == "active" and _is_same_day(rent.get("created_at"))
    ]

    lines = [
        f"Всего аккаунтов: <b>{total_accounts}</b>",
        f"・ Активных: <b>{active_accounts}</b>",
        f"・ Выдано: <b>{busy_accounts}</b>",
        f"・ Заморожено: <b>{frozen_accounts}</b>",
        "",
        f"Активных аренд: <b>{len(active_rentals)}</b>",
        f"Выдано сегодня: <b>{len(issued_today)}</b>",
        f"Завершено сегодня: <b>{len(finished_today)}</b>",
    ]
    return "\n".join(lines)


def _is_same_day(timestamp: Optional[str]) -> bool:
    if not timestamp:
        return False
    try:
        dt = datetime.fromisoformat(timestamp)
    except ValueError:
        return False
    return dt.date() == datetime.utcnow().date()


def _handle_monitor_callback(call) -> None:
    chat_id = call.message.chat.id
    action = call.data.split(":", 1)[1]
    if action == "logs":
        log_path = Path("logs/log.log")
        if log_path.exists():
            content = "\n".join(log_path.read_text(encoding="utf-8").splitlines()[-20:])
        else:
            content = "Лог-файл не найден."
        CARDINAL.telegram.bot.send_message(
            chat_id,
            f"<b>Последние записи:</b>\n<code>{content}</code>",
            parse_mode="HTML",
            reply_markup=_back_markup(),
        )
        return
    if action == "errors":
        CARDINAL.telegram.bot.send_message(
            chat_id,
            "⚠️ Заглушка: список ошибок.",
            reply_markup=_back_markup(),
        )
        return
    if action == "jobs":
                                              
        scheduler_running = _SCHEDULER_THREAD is not None and _SCHEDULER_THREAD.is_alive()
        text = "Активных задач:\n・ Scheduler: {}".format(
            "🟢 работает" if scheduler_running else "🔴 остановлен"
        )
        CARDINAL.telegram.bot.send_message(
            chat_id,
            text,
            reply_markup=_back_markup(),
        )
        return
    if action == "status":
        scheduler_running = _SCHEDULER_THREAD is not None and _SCHEDULER_THREAD.is_alive()
        CARDINAL.telegram.bot.send_message(
            chat_id,
            "🛡 Статус системы:\n\n・ Scheduler: {}\n・ FunPay handler: зарегистрирован".format(
                "🟢 работает" if scheduler_running else "🔴 остановлен"
            ),
            reply_markup=_back_markup(),
        )
        return


def _handle_lots_callback(call) -> None:
    chat_id = call.message.chat.id
    CARDINAL.telegram.bot.answer_callback_query(call.id)
    action = call.data.split(":", 1)[1]
    if action == "list":
        lots = _load_lots()
        if not lots:
            CARDINAL.telegram.bot.send_message(
                chat_id,
                "📦 <b>Список лотов</b>\n\n"
                "❌ Лоты пока не добавлены.\n\n"
                "➕ Используйте кнопку 'Добавить' для создания нового лота.",
                parse_mode="HTML",
                reply_markup=_back_markup(),
            )
            return
        text = "📦 <b>Список лотов</b>\n\n"
        for lot in lots:
            lot_id = lot.get("lot_id", "?")
            name = lot.get("name", "Без названия")
            game = lot.get("game", "—")
            account_ids = lot.get("account_ids", [])
            available = len([acc for acc in _load_accounts() if acc.get("id") in account_ids and acc.get("status") == "active"])
            status = "🟢" if available > 0 else "🔴"
            text += f"{status} <b>{name}</b>\n"
            text += f"   🆔 ID: <code>{lot_id}</code>\n"
            text += f"   🎮 Игра: {game}\n"
            text += f"   👥 Аккаунтов: {len(account_ids)} (доступно: {available})\n\n"
        
        CARDINAL.telegram.bot.send_message(
            chat_id,
            text,
            parse_mode="HTML",
            reply_markup=_back_markup(),
        )
        return
    if action == "add":
        _set_state(chat_id, mode="lots_add", step="lot_id")
        CARDINAL.telegram.bot.send_message(
            chat_id,
            "📦 <b>Добавление нового лота</b>\n\n"
            "📝 Введите ID лота с FunPay (например: <code>12345678</code>)\n\n"
            "💡 Или отправьте /cancel для отмены",
            parse_mode="HTML",
        )
        return
    if action.startswith("binder_"):
        # Формат: binder_{lot_id}
        lot_id = action.replace("binder_", "")
        _show_lot_binder_menu(chat_id, lot_id)
        return
    if action.startswith("edit_"):
        # Формат: edit_{lot_id}
        lot_id = action.replace("edit_", "")
        _show_lot_edit_menu(chat_id, lot_id)
        return
    if action.startswith("add_acc_"):
        # Формат: add_acc_{lot_id}
        lot_id = action.replace("add_acc_", "")
        _start_add_account_to_lot(chat_id, lot_id)
        return
    if action.startswith("remove_acc_"):
        # Формат: remove_acc_{lot_id}
        lot_id = action.replace("remove_acc_", "")
        _start_remove_account_from_lot(chat_id, lot_id)
        return
    if action.startswith("delete_"):
        # Формат: delete_{lot_id}
        lot_id = action.replace("delete_", "")
        _delete_lot_confirmation(chat_id, lot_id)
        return
    if action.startswith("confirm_delete_"):
        # Формат: confirm_delete_{lot_id}
        lot_id = action.replace("confirm_delete_", "")
        _delete_lot(chat_id, lot_id)
        return
    if action == "binder":
        # Показываем список лотов для выбора привязки
        lots = _load_lots()
        if not lots:
            CARDINAL.telegram.bot.send_message(
                chat_id,
                "❌ Нет лотов для привязки аккаунтов.",
                reply_markup=_back_markup(),
            )
            return
        text = "🔗 <b>Привязка аккаунтов к лоту</b>\n\n"
        text += "📋 Выберите лот:\n\n"
        kb = InlineKeyboardMarkup(row_width=1)
        for lot in lots:
            lot_id = lot.get("lot_id", "?")
            name = lot.get("name", "Без названия")
            kb.add(InlineKeyboardButton(
                f"📦 {name}",
                callback_data=f"lots:binder_{lot_id}"
            ))
        kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="section:back"))
        CARDINAL.telegram.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)
        return
    if action == "edit":
        # Показываем список лотов для редактирования
        lots = _load_lots()
        if not lots:
            CARDINAL.telegram.bot.send_message(
                chat_id,
                "❌ Нет лотов для редактирования.",
                reply_markup=_back_markup(),
            )
            return
        text = "✏️ <b>Редактирование лота</b>\n\n"
        text += "📋 Выберите лот:\n\n"
        kb = InlineKeyboardMarkup(row_width=1)
        for lot in lots:
            lot_id = lot.get("lot_id", "?")
            name = lot.get("name", "Без названия")
            kb.add(InlineKeyboardButton(
                f"📦 {name}",
                callback_data=f"lots:edit_{lot_id}"
            ))
        kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="section:back"))
        CARDINAL.telegram.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)
        return


def _show_lot_binder_menu(chat_id: int, lot_id: str) -> None:
    """Показывает меню привязки аккаунтов к лоту"""
    lot = _get_lot_by_id(lot_id)
    if not lot:
        CARDINAL.telegram.bot.send_message(chat_id, "❌ Лот не найден.", reply_markup=_back_markup())
        return
    
    lot_name = lot.get("name", "Без названия")
    account_ids = lot.get("account_ids", [])
    accounts = _load_accounts()
    
    # Разделяем на привязанные и непривязанные
    bound_accounts = [acc for acc in accounts if acc.get("id") in account_ids]
    unbound_accounts = [acc for acc in accounts if acc.get("id") not in account_ids]
    
    text = f"🔗 <b>Привязка аккаунтов к лоту</b>\n\n"
    text += f"📦 Лот: <b>{lot_name}</b>\n"
    text += f"🆔 ID: <code>{lot_id}</code>\n\n"
    text += f"👥 Привязано: {len(bound_accounts)} аккаунтов\n"
    text += f"📋 Доступно: {len(unbound_accounts)} аккаунтов\n\n"
    
    if bound_accounts:
        text += "✅ <b>Привязанные аккаунты:</b>\n"
        for acc in bound_accounts[:10]:
            status_emoji = "🟢" if acc.get("status") == "active" else "❄️" if acc.get("status") == "frozen" else "🔴"
            text += f"・ {status_emoji} <b>{acc.get('login', '?')}</b> — {acc.get('game', '—')}\n"
        if len(bound_accounts) > 10:
            text += f"... и ещё {len(bound_accounts) - 10}\n"
        text += "\n"
    
    kb = InlineKeyboardMarkup(row_width=2)
    if unbound_accounts:
        kb.row(
            InlineKeyboardButton("➕ Добавить", callback_data=f"lots:add_acc_{lot_id}"),
            InlineKeyboardButton("➖ Удалить", callback_data=f"lots:remove_acc_{lot_id}")
        )
    else:
        kb.add(InlineKeyboardButton("➖ Удалить", callback_data=f"lots:remove_acc_{lot_id}"))
    kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="section:lots"))
    
    CARDINAL.telegram.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)


def _show_lot_edit_menu(chat_id: int, lot_id: str) -> None:
    """Показывает меню редактирования лота"""
    lot = _get_lot_by_id(lot_id)
    if not lot:
        CARDINAL.telegram.bot.send_message(chat_id, "❌ Лот не найден.", reply_markup=_back_markup())
        return
    
    lot_name = lot.get("name", "Без названия")
    game = lot.get("game", "—")
    account_ids = lot.get("account_ids", [])
    accounts = _load_accounts()
    available = len([acc for acc in accounts if acc.get("id") in account_ids and acc.get("status") == "active"])
    
    text = f"✏️ <b>Редактирование лота</b>\n\n"
    text += f"📦 Название: <b>{lot_name}</b>\n"
    text += f"🆔 ID: <code>{lot_id}</code>\n"
    text += f"🎮 Игра: {game}\n"
    text += f"👥 Аккаунтов: {len(account_ids)} (доступно: {available})\n\n"
    
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("🔗 Привязки", callback_data=f"lots:binder_{lot_id}"))
    kb.add(InlineKeyboardButton("🗑️ Удалить", callback_data=f"lots:delete_{lot_id}"))
    kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="section:lots"))
    
    CARDINAL.telegram.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)


def _start_add_account_to_lot(chat_id: int, lot_id: str) -> None:
    """Начинает процесс добавления аккаунта к лоту"""
    lot = _get_lot_by_id(lot_id)
    if not lot:
        CARDINAL.telegram.bot.send_message(chat_id, "❌ Лот не найден.")
        return
    
    account_ids = lot.get("account_ids", [])
    accounts = _load_accounts()
    unbound_accounts = [acc for acc in accounts if acc.get("id") not in account_ids]
    
    if not unbound_accounts:
        CARDINAL.telegram.bot.send_message(chat_id, "❌ Нет доступных аккаунтов для привязки.")
        return
    
    lines = [
        f"{idx}. <b>{acc.get('login', '?')}</b> — {acc.get('game', '—')}"
        for idx, acc in enumerate(unbound_accounts, start=1)
    ]
    
    _set_state(
        chat_id,
        mode="lots_binder",
        step="choose_account",
        lot_id=lot_id,
        action="add",
        accounts=[acc.get("id") for acc in unbound_accounts],
    )
    
    CARDINAL.telegram.bot.send_message(
        chat_id,
        "➕ <b>Добавление аккаунта к лоту</b>\n\n"
        f"📦 Лот: <b>{lot.get('name', 'Без названия')}</b>\n\n"
        "📋 Выберите аккаунт для привязки:\n" + "\n".join(lines) + "\n\n"
        "📝 Отправьте номер или /cancel",
        parse_mode="HTML",
    )


def _start_remove_account_from_lot(chat_id: int, lot_id: str) -> None:
    """Начинает процесс удаления аккаунта из лота"""
    lot = _get_lot_by_id(lot_id)
    if not lot:
        CARDINAL.telegram.bot.send_message(chat_id, "❌ Лот не найден.")
        return
    
    account_ids = lot.get("account_ids", [])
    if not account_ids:
        CARDINAL.telegram.bot.send_message(chat_id, "❌ К этому лоту не привязано ни одного аккаунта.")
        return
    
    accounts = _load_accounts()
    bound_accounts = [acc for acc in accounts if acc.get("id") in account_ids]
    
    if not bound_accounts:
        CARDINAL.telegram.bot.send_message(chat_id, "❌ Аккаунты не найдены.")
        return
    
    lines = [
        f"{idx}. <b>{acc.get('login', '?')}</b> — {acc.get('game', '—')}"
        for idx, acc in enumerate(bound_accounts, start=1)
    ]
    
    _set_state(
        chat_id,
        mode="lots_binder",
        step="choose_account",
        lot_id=lot_id,
        action="remove",
        accounts=[acc.get("id") for acc in bound_accounts],
    )
    
    CARDINAL.telegram.bot.send_message(
        chat_id,
        "➖ <b>Удаление аккаунта из лота</b>\n\n"
        f"📦 Лот: <b>{lot.get('name', 'Без названия')}</b>\n\n"
        "📋 Выберите аккаунт для отвязки:\n" + "\n".join(lines) + "\n\n"
        "📝 Отправьте номер или /cancel",
        parse_mode="HTML",
    )


def _delete_lot_confirmation(chat_id: int, lot_id: str) -> None:
    """Подтверждение удаления лота"""
    lot = _get_lot_by_id(lot_id)
    if not lot:
        CARDINAL.telegram.bot.send_message(chat_id, "❌ Лот не найден.")
        return
    
    lot_name = lot.get("name", "Без названия")
    account_ids = lot.get("account_ids", [])
    
    text = f"⚠️ <b>Подтверждение удаления</b>\n\n"
    text += f"Вы уверены, что хотите удалить лот:\n"
    text += f"📦 <b>{lot_name}</b>\n"
    text += f"🆔 ID: <code>{lot_id}</code>\n"
    text += f"👥 Привязано аккаунтов: {len(account_ids)}\n\n"
    text += f"❌ Это действие нельзя отменить!"
    
    kb = InlineKeyboardMarkup(row_width=2)
    kb.row(
        InlineKeyboardButton("✅ Да, удалить", callback_data=f"lots:confirm_delete_{lot_id}"),
        InlineKeyboardButton("❌ Отмена", callback_data=f"lots:edit_{lot_id}")
    )
    
    CARDINAL.telegram.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)


def _delete_lot(chat_id: int, lot_id: str) -> None:
    """Удаляет лот"""
    lots = _load_lots()
    lot = _get_lot_by_id(lot_id)
    if not lot:
        CARDINAL.telegram.bot.send_message(chat_id, "❌ Лот не найден.")
        return
    
    lot_name = lot.get("name", "Без названия")
    lots = [l for l in lots if str(l.get("lot_id")) != str(lot_id)]
    _save_lots(lots)
    
    CARDINAL.telegram.bot.send_message(
        chat_id,
        f"✅ Лот <b>{lot_name}</b> успешно удален.",
        parse_mode="HTML",
        reply_markup=_back_markup()
    )


def _process_lots_add(message, state, text) -> None:
    """Обработчик добавления нового лота"""
    chat_id = message.chat.id
    step = state.get("step")
    
    if step == "lot_id":
        try:
            lot_id = str(text.strip())
            if not lot_id.isdigit():
                CARDINAL.telegram.bot.send_message(chat_id, "❌ ID лота должен быть числом.")
                return
            
            # Проверяем, существует ли уже такой лот
            if _get_lot_by_id(lot_id):
                CARDINAL.telegram.bot.send_message(chat_id, "❌ Лот с таким ID уже добавлен.")
                return
            
            # Получаем информацию о лоте с FunPay
            try:
                if not CARDINAL or not hasattr(CARDINAL, 'account'):
                    CARDINAL.telegram.bot.send_message(chat_id, "❌ Account недоступен.")
                    return
                
                account = CARDINAL.account
                if hasattr(account, 'get'):
                    account.get()
                
                if not hasattr(account, 'get_lot_fields'):
                    CARDINAL.telegram.bot.send_message(chat_id, "❌ Метод get_lot_fields недоступен.")
                    return
                
                lot_fields = account.get_lot_fields(lot_id)
                fields = lot_fields.fields if hasattr(lot_fields, 'fields') else {}
                name = fields.get("fields[summary][ru]", "Без названия")
                
            except Exception as e:
                LOGGER.error(f"Ошибка при получении информации о лоте: {e}")
                name = "Без названия"
            
            _set_state(chat_id, mode="lots_add", step="game", lot_id=lot_id, name=name)
            CARDINAL.telegram.bot.send_message(
                chat_id,
                f"✅ Лот найден: <b>{name}</b>\n\n"
                f"🎮 <b>Название игры</b>\n\n"
                f"📝 Введите название игры (например: <code>CS2</code>, <code>GTA V</code>)\n\n"
                f"💡 Или отправьте /cancel для отмены",
                parse_mode="HTML"
            )
            return
            
        except Exception as e:
            LOGGER.error(f"Ошибка при обработке ID лота: {e}")
            CARDINAL.telegram.bot.send_message(chat_id, f"❌ Ошибка: {e}")
            return
    
    if step == "game":
        lot_id = state.get("lot_id")
        name = state.get("name", "Без названия")
        game = text.strip()
        
        if not game:
            CARDINAL.telegram.bot.send_message(chat_id, "❌ Название игры не может быть пустым.")
            return
        
        # Добавляем лот
        lots = _load_lots()
        new_lot = {
            "lot_id": lot_id,
            "name": name,
            "game": game,
            "account_ids": [],
            "active": True,
        }
        lots.append(new_lot)
        _save_lots(lots)
        
        CARDINAL.telegram.bot.send_message(
            chat_id,
            f"✅ Лот успешно добавлен!\n\n"
            f"📦 Название: <b>{name}</b>\n"
            f"🆔 ID: <code>{lot_id}</code>\n"
            f"🎮 Игра: {game}\n\n"
            f"🔗 Теперь можно привязать аккаунты к этому лоту.",
            parse_mode="HTML",
            reply_markup=_back_markup()
        )
        _clear_state(chat_id)


def _process_lots_binder(message, state, text) -> None:
    """Обработчик привязки аккаунтов к лоту"""
    chat_id = message.chat.id
    step = state.get("step")
    lot_id = state.get("lot_id")
    
    if step == "choose_account":
        try:
            idx = int(text.strip())
        except ValueError:
            CARDINAL.telegram.bot.send_message(chat_id, "❌ <b>Ошибка ввода</b>\n\n📝 Введите номер из списка или отправьте /cancel для отмены.", parse_mode="HTML")
            return
        
        accounts_list = state.get("accounts", [])
        if not (1 <= idx <= len(accounts_list)):
            CARDINAL.telegram.bot.send_message(chat_id, "❌ Неверный номер.")
            return
        
        account_id = accounts_list[idx - 1]
        action = state.get("action")  # "add" or "remove"
        
        lots = _load_lots()
        lot = _get_lot_by_id(lot_id)
        if not lot:
            CARDINAL.telegram.bot.send_message(chat_id, "❌ Лот не найден.")
            _clear_state(chat_id)
            return
        
        account_ids = lot.get("account_ids", [])
        
        if action == "add":
            if account_id not in account_ids:
                account_ids.append(account_id)
                # Обновляем в списке
                for l in lots:
                    if str(l.get("lot_id")) == str(lot_id):
                        l["account_ids"] = account_ids
                        break
                _save_lots(lots)
                
                account = _get_account_by_id(account_id)
                account_name = account.get("login", "?") if account else "?"
                
                CARDINAL.telegram.bot.send_message(
                    chat_id,
                    f"✅ Аккаунт <b>{account_name}</b> привязан к лоту!",
                    parse_mode="HTML"
                )
                
                # Обновляем статус лота
                if CARDINAL:
                    _update_lot_status_if_needed(CARDINAL, lot_id)
            else:
                CARDINAL.telegram.bot.send_message(chat_id, "❌ Аккаунт уже привязан к этому лоту.")
        
        elif action == "remove":
            if account_id in account_ids:
                account_ids.remove(account_id)
                # Обновляем в списке
                for l in lots:
                    if str(l.get("lot_id")) == str(lot_id):
                        l["account_ids"] = account_ids
                        break
                _save_lots(lots)
                
                account = _get_account_by_id(account_id)
                account_name = account.get("login", "?") if account else "?"
                
                CARDINAL.telegram.bot.send_message(
                    chat_id,
                    f"✅ Аккаунт <b>{account_name}</b> отвязан от лота!",
                    parse_mode="HTML"
                )
                
                # Обновляем статус лота
                if CARDINAL:
                    _update_lot_status_if_needed(CARDINAL, lot_id)
            else:
                CARDINAL.telegram.bot.send_message(chat_id, "❌ Аккаунт не привязан к этому лоту.")
        
        _clear_state(chat_id)
        _show_lot_binder_menu(chat_id, lot_id)


def _show_logs_menu(message) -> None:
    """Показывает меню с логами (как в dempstars)"""
    try:
        chat_id = message.chat.id if hasattr(message, 'chat') else message.from_user.id
        
        if os.path.exists(log_path):
            # Получаем информацию о файле
            file_size = os.path.getsize(log_path)
            file_size_mb = file_size / (1024 * 1024)
            
            # Читаем последние строки лога с обработкой ошибок кодировки
            lines = []
            last_logs = ""
            try:
                # Используем binary mode для чтения, затем пробуем разные кодировки
                with open(log_path, "rb") as f:
                    content = f.read()
                
                # Пробуем разные кодировки для декодирования
                encodings = ['utf-8', 'utf-8-sig', 'cp1251', 'latin-1', 'cp866']
                decoded_text = None
                
                for encoding in encodings:
                    try:
                        decoded_text = content.decode(encoding, errors='replace')
                        break
                    except (UnicodeDecodeError, UnicodeError):
                        continue
                
                # Если не удалось декодировать, используем замену символов
                if not decoded_text:
                    decoded_text = content.decode('utf-8', errors='replace')
                
                # Разбиваем на строки
                lines = decoded_text.splitlines()
                
                # Берем последние 50 строк для фильтрации
                filtered_lines = lines[-50:] if len(lines) > 50 else lines
                
                # Берем последние 20 строк для отображения
                display_lines = filtered_lines[-20:] if len(filtered_lines) > 20 else filtered_lines
                last_logs = "\n".join(display_lines).strip()
                
            except Exception as e:
                LOGGER.error(f"Ошибка при чтении логов: {e}", exc_info=True)
                last_logs = f"Ошибка при чтении логов: {str(e)}"
            
            # Экранируем HTML символы в логах
            if last_logs:
                last_logs_escaped = html.escape(last_logs)
                # Обрезаем до 2000 символов для Telegram
                if len(last_logs_escaped) > 2000:
                    last_logs_escaped = last_logs_escaped[-2000:] + "\n... (обрезано)"
            else:
                last_logs_escaped = "Нет записей в логах"
            
            # Отправляем информацию о логе
            info_text = "📋 <b>Информация о логах</b>\n\n"
            info_text += f"📁 Размер файла: {file_size_mb:.2f} MB\n"
            info_text += f"📄 Всего записей: {len(lines) if lines else 'Неизвестно'}\n\n"
            info_text += "📝 <b>Последние записи:</b>\n"
            info_text += f"<code>{last_logs_escaped}</code>"
            
            CARDINAL.telegram.bot.send_message(chat_id, info_text, parse_mode="HTML")
            
            # Создаем клавиатуру с дополнительными действиями
            kb = InlineKeyboardMarkup(row_width=2)
            kb.row(
                InlineKeyboardButton("📥 Скачать", callback_data="logs:download"),
                InlineKeyboardButton("🗑️ Очистить", callback_data="logs:clear")
            )
            kb.add(InlineKeyboardButton("🔄 Обновить", callback_data="logs:view"))
            kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="section:back"))
            
            CARDINAL.telegram.bot.send_message(chat_id, "🔧 Выберите действие:", reply_markup=kb)
        else:
            CARDINAL.telegram.bot.send_message(chat_id, "❌ Файл логов не найден")
            kb = InlineKeyboardMarkup(row_width=1)
            kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="section:back"))
            CARDINAL.telegram.bot.send_message(chat_id, "🔧 Выберите действие:", reply_markup=kb)
    except Exception as e:
        LOGGER.error(f"Ошибка при показе логов: {e}")
        CARDINAL.telegram.bot.send_message(chat_id, f"❌ Ошибка: {e}")


def _handle_logs_callback(call) -> None:
    """Обработчик callback для раздела логов"""
    chat_id = call.message.chat.id
    action = call.data.split(":", 1)[1]
    
    if action == "view":
        _show_logs_menu(call.message)
        return
    if action == "download":
        if os.path.exists(log_path):
            try:
                with open(log_path, "rb") as f:
                    CARDINAL.telegram.bot.send_document(
                        chat_id,
                        f,
                        caption="📋 Полный файл логов"
                    )
            except Exception as e:
                LOGGER.error(f"Ошибка при отправке логов: {e}")
                CARDINAL.telegram.bot.send_message(chat_id, f"❌ Ошибка при отправке логов: {e}")
        else:
            CARDINAL.telegram.bot.send_message(chat_id, "❌ Файл логов не найден")
        return
    if action == "clear":
        try:
            # Очищаем файл логов
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("")
            LOGGER.info("Логи очищены администратором")
            CARDINAL.telegram.bot.send_message(chat_id, "✅ Логи успешно очищены")
        except Exception as e:
            LOGGER.error(f"Ошибка при очистке логов: {e}")
            CARDINAL.telegram.bot.send_message(chat_id, f"❌ Ошибка при очистке логов: {e}")
        return


def _handle_steam_callback(call) -> None:
    chat_id = call.message.chat.id
    action = call.data.split(":", 1)[1]
    if action == "password":
        _start_change_password(call)
        return
    if action == "guard":
        _start_guard_code(call)
        return


def _load_lots() -> List[Dict[str, Any]]:
    try:
        data = json.loads(LOTS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        LOGGER.error("Невозможно прочитать файл лотов, сбрасываю")
        return []
    lots = data.get("lots", [])
    # Миграция: добавляем account_ids если его нет
    for lot in lots:
        if "account_ids" not in lot:
            lot["account_ids"] = []
    return lots


def _save_lots(lots: List[Dict[str, Any]]) -> None:
    LOTS_FILE.write_text(
        json.dumps({"lots": lots}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _get_lot_by_id(lot_id: str) -> Optional[Dict[str, Any]]:
    """Получает лот по ID"""
    lots = _load_lots()
    return next((lot for lot in lots if str(lot.get("lot_id")) == str(lot_id)), None)


def _get_available_accounts_for_lot(lot_id: str) -> List[Dict[str, Any]]:
    """Получает список доступных аккаунтов для лота"""
    lot = _get_lot_by_id(lot_id)
    if not lot:
        return []
    
    account_ids = lot.get("account_ids", [])
    if not account_ids:
        return []
    
    accounts = _load_accounts()
    available = [
        acc for acc in accounts
        if acc.get("id") in account_ids and acc.get("status") == "active"
    ]
    return available


def _check_lot_availability(lot_id: str) -> bool:
    """Проверяет, есть ли доступные аккаунты для лота"""
    return len(_get_available_accounts_for_lot(lot_id)) > 0


def _toggle_lot_status(cardinal, lot_id: str, active: bool) -> bool:
    """Активирует или деактивирует лот на FunPay"""
    try:
        if not hasattr(cardinal, 'account'):
            LOGGER.warning(f"Account недоступен для изменения статуса лота {lot_id}")
            return False
        
        account = cardinal.account
        if hasattr(account, 'get'):
            account.get()
        
        if not hasattr(account, 'get_lot_fields'):
            LOGGER.warning(f"Метод get_lot_fields недоступен для изменения статуса лота {lot_id}")
            return False
        
        lot_fields = account.get_lot_fields(str(lot_id))
        lot_fields.active = active
        
        if not hasattr(account, 'save_lot'):
            LOGGER.warning(f"Метод save_lot недоступен для изменения статуса лота {lot_id}")
            return False
        
        account.save_lot(lot_fields)
        LOGGER.info(f"Лот {lot_id} успешно {'активирован' if active else 'деактивирован'}")
        return True
    except Exception as e:
        LOGGER.error(f"Ошибка при изменении статуса лота {lot_id}: {e}")
        return False


def _update_lot_status_if_needed(cardinal, lot_id: str) -> None:
    """Обновляет статус лота в зависимости от доступности аккаунтов"""
    if not lot_id or not cardinal:
        return
    
    lot = _get_lot_by_id(lot_id)
    if not lot:
        return
    
    has_available = _check_lot_availability(lot_id)
    lots = _load_lots()
    current_lot = None
    for l in lots:
        if str(l.get("lot_id")) == str(lot_id):
            current_lot = l
            break
    
    if not current_lot:
        return
    
    current_active = current_lot.get("active", True)
    
    # Если есть доступные аккаунты, но лот неактивен - активируем
    if has_available and not current_active:
        if _toggle_lot_status(cardinal, lot_id, True):
            current_lot["active"] = True
            _save_lots(lots)
            LOGGER.info(f"Лот {lot_id} активирован (появились доступные аккаунты)")
    
    # Если нет доступных аккаунтов, но лот активен - деактивируем
    elif not has_available and current_active:
        if _toggle_lot_status(cardinal, lot_id, False):
            current_lot["active"] = False
            _save_lots(lots)
            LOGGER.info(f"Лот {lot_id} деактивирован (нет доступных аккаунтов)")

BIND_TO_PRE_INIT = [init_plugin]
BIND_TO_DELETE = [lambda _: _stop_background_tasks()]
BIND_TO_API: dict = {}

LOT_TEMPLATES = []


def _load_lot_templates() -> None:
    global LOT_TEMPLATES
    try:
        data = json.loads(LOTS_FILE.read_text(encoding="utf-8"))
        LOT_TEMPLATES = data.get("lots", [])
    except Exception:
        LOT_TEMPLATES = []


def _extract_game_from_order(order_title: str) -> Optional[str]:
           
    if not order_title:
        return None
    
                                                                       
    normalized = order_title.lower()
                         
    normalized = normalized.replace("свободен", "").replace("занят", "").replace("аренда", "")
    
                                                                
    game_keywords = {
        "ARC Raiders": ["arc raiders", "arc", "arcraiders", "арк рейдерс", "арк"],
        "Battlefield 6": ["battlefield 6", "battlefield6", "bf6", "bf 6"],
        "Battlefield 2042": ["battlefield 2042", "bf2042", "battlefield2042"],
        "CS2": ["cs2", "counter-strike 2", "counter strike 2", "контр страйк 2"],
        "GTA V": ["gta v", "gta 5", "gtav", "gta5", "grand theft auto"],
        "FIFA": ["fifa", "fc 24", "fc24", "fc 25", "fc25"],
        "Dota 2": ["dota 2", "dota2", "дота 2"],
        "Rust": ["rust", "раст"],
        "Apex Legends": ["apex", "apex legends"],
        "Call of Duty": ["cod", "call of duty", "warzone"],
        "Fortnite": ["fortnite", "фортнайт"],
        "PUBG": ["pubg", "playerunknown"],
        "Valorant": ["valorant", "валорант"],
    }
    
                          
    for game_name, keywords in game_keywords.items():
        for keyword in keywords:
            if keyword in normalized:
                LOGGER.info(
                    "%s Detected game '%s' from order title via keyword: '%s'",
                    LOGGER_PREFIX,
                    game_name,
                    keyword,
                )
                return game_name
    
                                                                           
    try:
        accounts = _load_accounts()
                                                       
        available_games = set()
        for acc in accounts:
            game = acc.get("game", "").strip()
            if game:
                available_games.add(game)
        
        LOGGER.debug(
            "%s Available games from accounts: %s",
            LOGGER_PREFIX,
            list(available_games),
        )
        
                                              
        for game in available_games:
            game_lower = game.lower()
                               
            if game_lower in normalized:
                LOGGER.info(
                    "%s Detected game '%s' from order title via exact match",
                    LOGGER_PREFIX,
                    game,
                )
                return game
            
                                                                           
            game_words = game_lower.split()
            if len(game_words) > 1:
                                                                 
                if all(word in normalized for word in game_words if len(word) > 2):
                    LOGGER.info(
                        "%s Detected game '%s' from order title via partial match",
                        LOGGER_PREFIX,
                        game,
                    )
                    return game
        
                                                                  
        best_match = None
        best_score = 0
        
        for game in available_games:
            game_lower = game.lower()
                                                    
            game_words = set(game_lower.split())
            order_words = set(normalized.split())
            common_words = game_words & order_words
            
                                                           
            common_words = {w for w in common_words if len(w) > 2}
            
            if common_words:
                score = len(common_words) / len(game_words)
                if score > best_score and score >= 0.5:                          
                    best_score = score
                    best_match = game
        
        if best_match:
            LOGGER.info(
                "%s Detected game '%s' from order title via fuzzy match (score: %.2f)",
                LOGGER_PREFIX,
                best_match,
                best_score,
            )
            return best_match
        
    except Exception as exc:
        LOGGER.error("%s Error in universal game detection: %s", LOGGER_PREFIX, exc)
    
    LOGGER.warning("%s Could not detect game from order title: %s", LOGGER_PREFIX, order_title)
    return None


def _normalize_lot_name(value: str) -> str:
                                                                     
    stripped = value.lower()
    stripped = stripped.replace("свободно", "").replace("занят", "")
    return "".join(ch for ch in stripped if ch.isalnum() or ch.isspace())


def _games_match(game1: str, game2: str) -> bool:
           
    if not game1 or not game2:
        return False
    
    g1 = game1.lower().strip()
    g2 = game2.lower().strip()
    
                       
    if g1 == g2:
        return True
    
                                           
    if g1 in g2 or g2 in g1:
        return True
    
                                                                      
    words1 = set(w for w in g1.split() if len(w) > 2)
    words2 = set(w for w in g2.split() if len(w) > 2)
    
    if words1 and words2:
                                         
        common = words1 & words2
        similarity1 = len(common) / len(words1) if words1 else 0
        similarity2 = len(common) / len(words2) if words2 else 0
        if similarity1 >= 0.7 or similarity2 >= 0.7:
            return True
    
    return False


def _calculate_hours_from_quantity(quantity: int) -> float:
           
    try:
        hours = float(quantity) if quantity else 1.0
    except (TypeError, ValueError):
        hours = 1.0
    
                   
    return max(hours, 1.0)


def _auto_issue_rental_by_lot(lot_id: str, hours: float, payload: Dict[str, Any]) -> None:
    """Автоматическая выдача аренды по ID лота"""
    LOGGER.info("%s ✅ Auto-issue authorized for lot: %s", LOGGER_PREFIX, lot_id)
    
    buyer_id = payload.get("buyer_id") or payload.get("buyer")
    chat_id = payload.get("chat_id")
    note = payload.get("note") or payload.get("comment") or ""
    
    # Получаем доступные аккаунты для лота
    accounts = _get_available_accounts_for_lot(lot_id)
    if not accounts:
        LOGGER.warning("No available accounts for lot %s", lot_id)
        if getattr(CARDINAL, "telegram", None):
            admin_ids = getattr(CARDINAL.telegram, "admin_ids", [])
            for admin_id in admin_ids[:1]:
                try:
                    CARDINAL.telegram.bot.send_message(
                        admin_id,
                        f"⚠️ Нет доступных аккаунтов для лота {lot_id} (заказ #{payload.get('id')}).",
                    )
                except Exception:                
                    pass
        # Деактивируем лот, если нет доступных аккаунтов
        if CARDINAL:
            _update_lot_status_if_needed(CARDINAL, lot_id)
        return

    # Берем первый доступный аккаунт
    account = accounts[0]
    lot = _get_lot_by_id(lot_id)
    game = lot.get("game", "") if lot else ""
    
    rentals = _load_rentals()
    new_id = f"rent-{len(rentals) + 1:04d}"
    created_at = datetime.utcnow().isoformat()
    expires_at = (datetime.utcnow() + timedelta(hours=hours)).isoformat()
    rental_entry = {
        "id": new_id,
        "account_id": account.get("id"),
        "lot_id": lot_id,
        "game": game,
        "status": "active",
        "created_at": created_at,
        "expires_at": expires_at,
        "hours": hours,
        "note": note,
        "order_id": payload.get("id"),
        "buyer_id": buyer_id,
        "chat_id": chat_id,
    }
    rentals.append(rental_entry)
    _save_rentals(rentals)

    # Обновляем статус аккаунта
    accounts_all = _load_accounts()
    for acc in accounts_all:
        if acc.get("id") == account.get("id"):
            acc["status"] = "busy"
            acc.setdefault("notes", []).append(
                f"Автовыдача аренды #{new_id} ({created_at})."
            )
            break
    _save_accounts(accounts_all)
    
    # Проверяем доступность лота и обновляем статус
    if CARDINAL:
        _update_lot_status_if_needed(CARDINAL, lot_id)

    buyer_notes = _load_buyer_notes()
    if buyer_id:
        history = buyer_notes.setdefault(str(buyer_id), [])
        history.append({
            "rental_id": new_id,
            "account_id": account.get("id"),
            "created_at": created_at,
        })
        _save_buyer_notes(buyer_notes)

    # Отправляем уведомление администраторам в Telegram ПЕРЕД отправкой в FunPay
    if CARDINAL and hasattr(CARDINAL, "telegram"):
        admin_ids = getattr(CARDINAL.telegram, "admin_ids", [])
        if admin_ids:
            try:
                expires_at_msk = utc_to_moscow(datetime.fromisoformat(expires_at))
                admin_message = (
                    "✅ <b>Аккаунт успешно выдан</b>\n\n"
                    f"🆔 ID аренды: <code>{new_id}</code>\n"
                    f"🎮 Игра: <b>{game}</b>\n"
                    f"👤 Логин: <code>{account.get('login')}</code>\n"
                    f"⏰ Срок: {hours} ч. (до {expires_at_msk.strftime('%d.%m.%Y %H:%M')})\n"
                    f"📦 Лот ID: <code>{lot_id}</code>\n"
                    f"🆔 Заказ: <code>{payload.get('id', '—')}</code>"
                )
                for admin_id in admin_ids:
                    try:
                        CARDINAL.telegram.bot.send_message(admin_id, admin_message, parse_mode="HTML")
                    except Exception as exc:
                        LOGGER.error("%s Failed to send admin notification to %s for rental %s: %s", LOGGER_PREFIX, admin_id, new_id, exc)
            except Exception as exc:
                LOGGER.error("%s Failed to prepare admin notification for rental %s: %s", LOGGER_PREFIX, new_id, exc)
                                                      
    message = (
        "╔═══════════════════════════╗\n"
        "║   ✅ АККАУНТ ВЫДАН!       ║\n"
        "╚═══════════════════════════╝\n\n"
        f"🎮 Игра: {game}\n\n"
        "┏━━━━━━━━━━━━━━━━━━━━━━━┓\n"
        "┃  ДАННЫЕ ДЛЯ ВХОДА      ┃\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━━┛\n\n"
        "👤 Логин:\n"
        f"   └ {account.get('login')}\n\n"
        "🔒 Пароль:\n"
        f"   └ {account.get('password')}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "⏰ Срок аренды:\n"
        f"   └ До {utc_to_moscow(datetime.fromisoformat(expires_at)).strftime('%d.%m.%Y в %H:%M')}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📌 ВАЖНАЯ ИНФОРМАЦИЯ:\n\n"
        "🔐 Steam Guard код:\n"
        "   └ Отправьте: !код\n\n"
        "📚 Список команд:\n"
        "   └ Отправьте: !аренда\n\n"
        "⚠️ ПРАВИЛА ИСПОЛЬЗОВАНИЯ:\n\n"
        "🚫 ЗАПРЕЩЕНО играть с читами!\n"
        "   └ Все действия отслеживаются\n"
        "   └ Использование читов = БАН\n\n"
        "🔄 После окончания аренды пароль\n"
        "   будет автоматически изменен!\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💬 Приятной игры! 🎮"
    )
    
    if chat_id and CARDINAL:
        success = _send_funpay_message(CARDINAL, chat_id, message)
        if success:
            LOGGER.info("%s Auto-issued rental %s for order %s", LOGGER_PREFIX, new_id, payload.get("id"))
        else:
            LOGGER.error("%s Failed to send auto-issue message for rental %s", LOGGER_PREFIX, new_id)
    else:
        LOGGER.warning("%s Cannot send message: chat_id=%s CARDINAL=%s", LOGGER_PREFIX, chat_id, CARDINAL is not None)


def _auto_issue_rental(game: str, hours: float, payload: Dict[str, Any]) -> None:
    """Старая функция для обратной совместимости (использует поиск по игре)"""
    LOGGER.info("%s ✅ Auto-issue authorized for game: %s", LOGGER_PREFIX, game)
    
    buyer_id = payload.get("buyer_id") or payload.get("buyer")
    chat_id = payload.get("chat_id")
    note = payload.get("note") or payload.get("comment") or ""

                                                               
    accounts = [
        acc for acc in _load_accounts() 
        if acc.get("status") == "active" and _games_match(acc.get("game", ""), game)
    ]
    if not accounts:
        LOGGER.warning("No available accounts for game %s", game)
        if getattr(CARDINAL, "telegram", None):
            admin_ids = getattr(CARDINAL.telegram, "admin_ids", [])
            for admin_id in admin_ids[:1]:
                try:
                    CARDINAL.telegram.bot.send_message(
                        admin_id,
                        f"⚠️ Нет доступных аккаунтов для {game} (заказ #{payload.get('id')}).",
                    )
                except Exception:                
                    pass
        return

    account = accounts[0]
    rentals = _load_rentals()
    new_id = f"rent-{len(rentals) + 1:04d}"
    created_at = datetime.utcnow().isoformat()
    expires_at = (datetime.utcnow() + timedelta(hours=hours)).isoformat()
    note = payload.get("note") or payload.get("comment") or ""
    rental_entry = {
        "id": new_id,
        "account_id": account.get("id"),
        "game": game,
        "status": "active",
        "created_at": created_at,
        "expires_at": expires_at,
        "hours": hours,
        "note": note,
        "order_id": payload.get("id"),
        "buyer_id": buyer_id,
        "chat_id": chat_id,
    }
    rentals.append(rental_entry)
    _save_rentals(rentals)

    accounts_all = _load_accounts()
    for acc in accounts_all:
        if acc.get("id") == account.get("id"):
            acc["status"] = "busy"
            acc.setdefault("notes", []).append(
                f"Автовыдача аренды #{new_id} ({created_at})."
            )
            break
    _save_accounts(accounts_all)

    buyer_notes = _load_buyer_notes()
    if buyer_id:
        history = buyer_notes.setdefault(str(buyer_id), [])
        history.append({
            "rental_id": new_id,
            "account_id": account.get("id"),
            "created_at": created_at,
        })
        _save_buyer_notes(buyer_notes)

                                                      
    message = (
        "╔═══════════════════════════╗\n"
        "║   ✅ АККАУНТ ВЫДАН!       ║\n"
        "╚═══════════════════════════╝\n\n"
        f"🎮 Игра: {game}\n\n"
        "┏━━━━━━━━━━━━━━━━━━━━━━━┓\n"
        "┃  ДАННЫЕ ДЛЯ ВХОДА      ┃\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━━┛\n\n"
        "👤 Логин:\n"
        f"   └ {account.get('login')}\n\n"
        "🔒 Пароль:\n"
        f"   └ {account.get('password')}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "⏰ Срок аренды:\n"
        f"   └ До {utc_to_moscow(datetime.fromisoformat(expires_at)).strftime('%d.%m.%Y в %H:%M')}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📌 ВАЖНАЯ ИНФОРМАЦИЯ:\n\n"
        "🔐 Steam Guard код:\n"
        "   └ Отправьте: !код\n\n"
        "📚 Список команд:\n"
        "   └ Отправьте: !аренда\n\n"
        "⚠️ ПРАВИЛА ИСПОЛЬЗОВАНИЯ:\n\n"
        "🚫 ЗАПРЕЩЕНО играть с читами!\n"
        "   └ Все действия отслеживаются\n"
        "   └ Использование читов = БАН\n\n"
        "🔄 После окончания аренды пароль\n"
        "   будет автоматически изменен!\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💬 Приятной игры! 🎮"
    )
    
    if chat_id and CARDINAL:
        success = _send_funpay_message(CARDINAL, chat_id, message)
        if success:
            LOGGER.info("%s Auto-issued rental %s for order %s", LOGGER_PREFIX, new_id, payload.get("id"))
        else:
            LOGGER.error("%s Failed to send auto-issue message for rental %s", LOGGER_PREFIX, new_id)
    else:
        LOGGER.warning("%s Cannot send message: chat_id=%s CARDINAL=%s", LOGGER_PREFIX, chat_id, CARDINAL is not None)


def _load_buyer_notes() -> Dict[str, Any]:
    try:
        return json.loads(BUYER_NOTES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_buyer_notes(notes: Dict[str, Any]) -> None:
    BUYER_NOTES_FILE.write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")


def _format_free_accounts(limit: int = 10) -> str:
                                           
    accounts = [acc for acc in _load_accounts() if acc.get("status") == "active"]
    if not accounts:
        return "Свободных аккаунтов нет."
    lines = []
    for account in accounts[:limit]:
        lines.append(
            f"・ {account.get('login')} — {account.get('game', '—')}"
        )
    rest = len(accounts) - limit
    if rest > 0:
        lines.append(f"… и ещё {rest} аккаунтов")
    return "\n".join(lines)


def _format_free_accounts_beautiful(limit: int = 10) -> str:
                                                             
    accounts = [acc for acc in _load_accounts() if acc.get("status") == "active"]
    
    if not accounts:
        return (
            "╔═══════════════════════════╗\n"
            "║   🎮 СВОБОДНЫЕ АККАУНТЫ   ║\n"
            "╚═══════════════════════════╝\n\n"
            "😔 К сожалению, сейчас нет\n"
            "   свободных аккаунтов\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "⏰ Попробуйте позже или\n"
            "💬 Напишите продавцу напрямую"
        )
    
                                                       
    games_dict = {}
    for acc in accounts[:limit]:
        game = acc.get("game", "Без категории")
                                                  
        game_key = game
        if game_key not in games_dict:
            games_dict[game_key] = []
        games_dict[game_key].append(acc.get("login", "unknown"))
    
                              
    lines = [
        "╔═══════════════════════════╗",
        "║   🎮 СВОБОДНЫЕ АККАУНТЫ   ║",
        "╚═══════════════════════════╝",
        "",
    ]
    
    for game, logins in sorted(games_dict.items()):
        lines.append(f"🟢 {game}")
        for login in logins:
            lines.append(f"   └ {login}")
        lines.append("")
    
    rest = len(accounts) - limit
    if rest > 0:
        lines.append(f"... и ещё {rest} аккаунтов\n")
    
    lines.extend([
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📊 Всего доступно: {len(accounts)} аккаунтов",
        "✨ При заказе аккаунт выдастся",
        "   автоматически!"
    ])
    
    return "\n".join(lines)


                                                                              
                             
                                                                              

                                                            
FUNPAY_STATES: Dict[tuple, Dict[str, Any]] = {}


def _send_funpay_message(cardinal, chat_id: str | int, message: str, *, parse_mode: Optional[str] = None) -> bool:
           
    try:
        LOGGER.debug(
            "%s Attempting to send FunPay message: chat=%s, message_len=%d, preview=%r",
            LOGGER_PREFIX,
            chat_id,
            len(message),
            message[:100],
        )
        cardinal.send_message(int(chat_id), message)
        LOGGER.info("%s Successfully sent FunPay message to chat %s", LOGGER_PREFIX, chat_id)
        return True
    except Exception as exc:
        LOGGER.error(
            "%s Failed to send FunPay message to %s: %s (message was: %r)",
            LOGGER_PREFIX,
            chat_id,
            exc,
            message[:200],
            exc_info=True
        )
        return False


def _handle_chat_command(cardinal, chat_id: int, buyer_id: Optional[int], text: str) -> None:
           
    normalized = text.lower().strip()
    LOGGER.debug("%s Command parsed: chat=%s buyer=%s text=%r", LOGGER_PREFIX, chat_id, buyer_id, normalized)

                                         
    if normalized.startswith("!аккаунты"):
        accounts_text = _format_free_accounts_beautiful()
        _send_funpay_message(cardinal, chat_id, accounts_text)
        return

                      
    if normalized.startswith("!аренда"):
        message = (
            "╔═══════════════════════════╗\n"
            "║    📚 ПОМОЩЬ ПО АРЕНДЕ    ║\n"
            "╚═══════════════════════════╝\n\n"
            "💬 Доступные команды:\n\n"
            "🎮 !аккаунты\n"
            "   └ Показать свободные аккаунты\n\n"
            "🔑 !код [логин]\n"
            "   └ Получить Steam Guard код\n"
            "   └ Без логина - для вашего аккаунта\n\n"
            "📖 !аренда\n"
            "   └ Показать эту справку\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "💡 Команды работают только в этом чате"
        )
        _send_funpay_message(cardinal, chat_id, message)
        return

                                 
    if normalized.startswith("!код"):
        parts = text.split()
        account = None
        
                                                                 
        if len(parts) > 1:
                                                                              
            login = parts[1].strip()
                                                                                  
            login = ''.join(ch for ch in login if ch.isprintable() and not ch.isspace())
            login = login.strip()
            
                                                     
            LOGGER.info(
                "%s Guard code request: chat=%s buyer=%s login=%r (cleaned: %r, bytes: %s)",
                LOGGER_PREFIX,
                chat_id,
                buyer_id,
                parts[1],
                login,
                login.encode('utf-8'),
            )
            
                                       
            all_accounts = _load_accounts()
            account = next(
                (acc for acc in all_accounts if acc.get("login", "").lower() == login.lower()),
                None
            )
            
            if not account:
                                                              
                available_logins = [acc.get("login") for acc in all_accounts if acc.get("login")]
                LOGGER.warning(
                    "%s Account not found by login %r (case-insensitive). Available logins: %s",
                    LOGGER_PREFIX,
                    login,
                    available_logins[:10],                        
                )
                                                                                
        elif buyer_id:
            LOGGER.info(
                "%s Guard code request without login: chat=%s buyer=%s",
                LOGGER_PREFIX,
                chat_id,
                buyer_id,
            )
            notes = _load_buyer_notes()
            history = notes.get(str(buyer_id), [])
            if history:
                last_entry = history[-1]
                account = _get_account_by_id(last_entry.get("account_id"))
                LOGGER.info(
                    "%s Found last rental account: %s",
                    LOGGER_PREFIX,
                    account.get("id") if account else None,
                )

        if not account:
            error_msg = (
                "╔═══════════════════════════╗\n"
                "║       ⚠️ ОШИБКА           ║\n"
                "╚═══════════════════════════╝\n\n"
                "❌ Аккаунт не найден\n\n"
                "Возможные причины:\n"
                "・ Неверный логин аккаунта\n"
                "・ У вас нет активной аренды\n"
                "・ Аренда уже завершена\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "💡 Попробуйте:\n"
                "   └ !аккаунты - список аккаунтов\n"
                "   └ !код без логина - автоопределение"
            )
            _send_funpay_message(cardinal, chat_id, error_msg)
            LOGGER.warning("%s Guard command: account not found (chat=%s, buyer=%s)", LOGGER_PREFIX, chat_id, buyer_id)
            return

        mafile = account.get("mafile") or {}
        shared = mafile.get("shared_secret")
        if not shared:
            error_msg = (
                "╔═══════════════════════════╗\n"
                "║       ⚠️ ОШИБКА           ║\n"
                "╚═══════════════════════════╝\n\n"
                "❌ Steam Guard недоступен\n\n"
                "Для этого аккаунта не настроен\n"
                "мобильный аутентификатор.\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "💬 Обратитесь к продавцу для\n"
                "   получения кода вручную"
            )
            _send_funpay_message(cardinal, chat_id, error_msg)
            LOGGER.warning("%s Guard command: no shared_secret for %s", LOGGER_PREFIX, account.get("id"))
            return

        try:
            code = generate_guard_code(shared)
            LOGGER.info(
                "%s Generated guard code for account %s (login=%s)",
                LOGGER_PREFIX,
                account.get("id"),
                account.get("login"),
            )
            
                                                     
            response = (
                f"Код Steam Guard для {account.get('login')}: {code}\n"
                f"(действителен ~30 секунд)"
            )
            
            LOGGER.info(
                "%s Sending guard code to FunPay chat %s: %r",
                LOGGER_PREFIX,
                chat_id,
                response,
            )
            
            success = _send_funpay_message(cardinal, chat_id, response)
            
            if success:
                LOGGER.info("%s Guard code successfully sent for account %s", LOGGER_PREFIX, account.get("id"))
            else:
                LOGGER.error("%s Failed to send guard code to chat %s", LOGGER_PREFIX, chat_id)
                
        except Exception as exc:
            LOGGER.error("%s Failed to generate guard code: %s", LOGGER_PREFIX, exc, exc_info=True)
            _send_funpay_message(cardinal, chat_id, "❌ Ошибка генерации Guard-кода.")
        return



def _handle_new_order(cardinal, order_id: str, chat_id: int, buyer_id: int) -> None:
           
    try:
                                      
        order = cardinal.account.get_order(order_id)
        
        # Получаем chat_id из заказа, если он не был передан
        if not chat_id and hasattr(order, 'chat_id'):
            chat_id = order.chat_id
        elif not chat_id and hasattr(order, 'chat') and hasattr(order.chat, 'id'):
            chat_id = order.chat.id
        
                                 
        if order.status in [OrderStatuses.CLOSED, OrderStatuses.REFUNDED]:
            LOGGER.info("%s Order %s already closed/refunded, skipping", LOGGER_PREFIX, order_id)
            return
        
                                                                          
        order_title = getattr(order, "title", "") or getattr(order, "description", "")
        if "аренда" not in order_title.lower():
            LOGGER.info(
                "%s Order %s skipped - no 'Аренда' keyword in title: %s",
                LOGGER_PREFIX,
                order_id,
                order_title,
            )
            return
            
        LOGGER.info(
            "%s New order detected: #%s from %s (buyer_id=%s)",
            LOGGER_PREFIX,
            order_id,
            order.buyer_username,
            buyer_id,
        )

        # Получаем lot_id из заказа
        lot_id = None
        
        # Способ 1: Прямое поле lot_id
        if hasattr(order, "lot_id"):
            lot_id = str(order.lot_id)
        
        # Способ 2: Через объект lot
        if not lot_id and hasattr(order, "lot") and order.lot:
            if hasattr(order.lot, "id"):
                lot_id = str(order.lot.id)
            elif isinstance(order.lot, dict):
                lot_id = str(order.lot.get("id", ""))
        
        # Способ 3: Через lot_params_dict
        if not lot_id and hasattr(order, "lot_params_dict"):
            lot_params = order.lot_params_dict
            if isinstance(lot_params, dict):
                # Пробуем разные ключи
                for key in ["lot_id", "lotId", "id", "lot_id_", "lotID", "lot", "game_id"]:
                    if key in lot_params:
                        lot_id = str(lot_params[key])
                        break
                # Если не нашли по ключам, проверим все значения
                if not lot_id:
                    # Попробуем найти числовое значение, которое может быть lot_id
                    for key, value in lot_params.items():
                        if isinstance(value, (int, str)) and str(value).isdigit():
                            # Проверим, существует ли лот с таким ID в конфигурации
                            test_lot = _get_lot_by_id(str(value))
                            if test_lot:
                                lot_id = str(value)
                                break
        
        # Способ 4: Через lot_params (может быть строкой или объектом)
        if not lot_id and hasattr(order, "lot_params"):
            lot_params = order.lot_params
            if isinstance(lot_params, dict):
                for key in ["lot_id", "lotId", "id", "lot_id_", "lotID"]:
                    if key in lot_params:
                        lot_id = str(lot_params[key])
                        break
            elif isinstance(lot_params, str):
                # Пробуем распарсить JSON если это строка
                try:
                    import json
                    parsed = json.loads(lot_params)
                    if isinstance(parsed, dict):
                        for key in ["lot_id", "lotId", "id", "lot_id_", "lotID"]:
                            if key in parsed:
                                lot_id = str(parsed[key])
                                break
                except Exception:
                    pass
        
        # Способ 5: Через subcategory (может содержать ID)
        if not lot_id and hasattr(order, "subcategory"):
            subcategory = order.subcategory
            if subcategory:
                # Если subcategory - это объект с id
                if hasattr(subcategory, "id"):
                    lot_id = str(subcategory.id)
                elif isinstance(subcategory, dict) and "id" in subcategory:
                    lot_id = str(subcategory["id"])
        
        # Способ 6: Попробуем получить из других полей
        if not lot_id:
            for attr in ["lotId", "lot_id", "lotID"]:
                if hasattr(order, attr):
                    lot_id = str(getattr(order, attr))
                    break
        
        # Способ 7: Если все еще не нашли, попробуем найти через все лоты в конфигурации
        # Сравним title заказа с названиями лотов
        if not lot_id:
            lots = _load_lots()
            for lot in lots:
                lot_name = lot.get("name", "").lower()
                if lot_name and lot_name in order_title.lower():
                    lot_id = str(lot.get("lot_id"))
                    break
        
        if not lot_id:
            LOGGER.warning(
                "%s Could not extract lot_id from order %s",
                LOGGER_PREFIX,
                order_id,
            )
            message = (
                "╔═══════════════════════════╗\n"
                "║  ✅ ЗАКАЗ ПОЛУЧЕН!        ║\n"
                "╚═══════════════════════════╝\n\n"
                "🙏 Спасибо за покупку!\n\n"
                "⏳ Ваш заказ обрабатывается...\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "📌 Я выдам аккаунт в ближайшее\n"
                "   время (обычно 1-5 минут)\n\n"
                "💬 Пожалуйста, оставайтесь на связи!"
            )
            _send_funpay_message(cardinal, chat_id, message)
            return
        
        # Проверяем, есть ли лот в конфигурации
        lot = _get_lot_by_id(lot_id)
        if not lot:
            # Попробуем получить информацию о лоте через API и найти его в конфигурации по названию
            try:
                if hasattr(cardinal, 'account') and hasattr(cardinal.account, 'get_lots'):
                    account = cardinal.account
                    if hasattr(account, 'get'):
                        account.get()
                    lots_from_api = account.get_lots()
                    
                    # Ищем лот по ID в API
                    api_lot = None
                    if lots_from_api:
                        for api_l in lots_from_api:
                            api_lot_id = None
                            if hasattr(api_l, 'id'):
                                api_lot_id = str(api_l.id)
                            elif isinstance(api_l, dict):
                                api_lot_id = str(api_l.get('id', ''))
                            
                            if api_lot_id == str(lot_id):
                                api_lot = api_l
                                break
                    
                    # Если нашли лот в API, пробуем найти его в конфигурации по названию
                    if api_lot:
                        api_lot_name = None
                        if hasattr(api_lot, 'name'):
                            api_lot_name = api_lot.name
                        elif isinstance(api_lot, dict):
                            api_lot_name = api_lot.get('name', '')
                        
                        if api_lot_name:
                            # Ищем в конфигурации по названию
                            lots = _load_lots()
                            for cfg_lot in lots:
                                cfg_lot_name = cfg_lot.get("name", "").lower()
                                if cfg_lot_name == api_lot_name.lower():
                                    # Обновляем lot_id на тот, что в конфигурации
                                    lot_id = str(cfg_lot.get("lot_id"))
                                    lot = cfg_lot
                                    break
            except Exception as exc:
                LOGGER.error("%s Error while trying to get lot from API: %s", LOGGER_PREFIX, exc, exc_info=True)
        
        # Если все еще не нашли, пробуем найти по названию заказа среди всех лотов в конфигурации
        if not lot:
            lots = _load_lots()
            
            # Пробуем найти по названию более точно
            for cfg_lot in lots:
                cfg_lot_name = cfg_lot.get("name", "").lower()
                cfg_lot_id = str(cfg_lot.get("lot_id", ""))
                # Проверяем, совпадает ли lot_id (на случай если были проблемы с типами)
                if cfg_lot_id == str(lot_id):
                    lot = cfg_lot
                    break
                # Или проверяем по названию
                if cfg_lot_name and cfg_lot_name in order_title.lower():
                    lot_id = cfg_lot_id
                    lot = cfg_lot
                    break
                # Или проверяем обратное - название заказа в названии лота
                if cfg_lot_name and order_title.lower() in cfg_lot_name:
                    lot_id = cfg_lot_id
                    lot = cfg_lot
                    break
        
        if not lot:
            LOGGER.warning(
                "%s Could not find lot %s in configuration",
                LOGGER_PREFIX,
                lot_id,
            )
            message = (
                "╔═══════════════════════════╗\n"
                "║  ✅ ЗАКАЗ ПОЛУЧЕН!        ║\n"
                "╚═══════════════════════════╝\n\n"
                "🙏 Спасибо за покупку!\n\n"
                "⏳ Ваш заказ обрабатывается...\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "📌 Я выдам аккаунт в ближайшее\n"
                "   время (обычно 1-5 минут)\n\n"
                "💬 Пожалуйста, оставайтесь на связи!"
            )
            _send_funpay_message(cardinal, chat_id, message)
            return
        
        # Получаем игру из конфигурации лота
        game = lot.get("game", "")
        if not game:
            LOGGER.warning(
                "%s Lot %s has no game specified in configuration for order %s",
                LOGGER_PREFIX,
                lot_id,
                order_id,
            )
            message = (
                "╔═══════════════════════════╗\n"
                "║  ✅ ЗАКАЗ ПОЛУЧЕН!        ║\n"
                "╚═══════════════════════════╝\n\n"
                "🙏 Спасибо за покупку!\n\n"
                "⏳ Ваш заказ обрабатывается...\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "📌 Я выдам аккаунт в ближайшее\n"
                "   время (обычно 1-5 минут)\n\n"
                "💬 Пожалуйста, оставайтесь на связи!"
            )
            _send_funpay_message(cardinal, chat_id, message)
            return
        
                                                        
        quantity = getattr(order, "amount", 1)
        hours = _calculate_hours_from_quantity(quantity)
        
        LOGGER.info(
            "%s Auto-issuing rental for order %s: lot_id=%s, game=%s, hours=%.1f (quantity=%s)",
            LOGGER_PREFIX,
            order_id,
            lot_id,
            game,
            hours,
            quantity,
        )
        
                           
        payload = {
            "id": order_id,
            "buyer_id": buyer_id,
            "chat_id": chat_id,
            "note": "",
        }
        
                                     
        _auto_issue_rental_by_lot(lot_id, hours, payload)
        
    except Exception as exc:
        LOGGER.error("%s Failed to handle order %s: %s", LOGGER_PREFIX, order_id, exc, exc_info=True)
                                             
        if CARDINAL and hasattr(CARDINAL, "telegram"):
            admin_ids = getattr(CARDINAL.telegram, "admin_ids", [])
            for admin_id in admin_ids[:1]:
                try:
                    CARDINAL.telegram.bot.send_message(
                        admin_id,
                        f"⚠️ Ошибка автовыдачи для заказа #{order_id}:\n{exc}",
                    )
                except Exception:
                    pass


def handle_funpay_message(cardinal, event: NewMessageEvent) -> None:
           
    
                                          
    message = event.message
    
                                
    text = (message.text or "").strip()
    chat_id = message.chat_id
    author_id = message.author_id
    
    LOGGER.debug(
        "%s FunPay message: chat=%s author=%s text=%r",
        LOGGER_PREFIX,
        chat_id,
        author_id,
        text[:50] if text else "",
    )

                                 
    if not chat_id or not text:
        return

                                                                              
                                                      
                                                                              
    
                                                                             
    if author_id == 0 and "оплатил заказ" in text.lower():
        match = re.search(r'заказ\s+#?(\w+)', text, re.IGNORECASE)
        if match:
            order_id = match.group(1)
            try:
                order = cardinal.account.get_order(order_id)
                buyer_id = order.buyer_id
                
                LOGGER.info(
                    "%s System message: order %s paid by buyer %s",
                    LOGGER_PREFIX,
                    order_id,
                    buyer_id,
                )
                
                                                               
                threading.Thread(
                    target=_handle_new_order,
                    args=(cardinal, order_id, chat_id, buyer_id),
                    daemon=True,
                ).start()
                
            except Exception as exc:
                LOGGER.error("%s Failed to process order notification: %s", LOGGER_PREFIX, exc)
        return

                                                                              
                                
                                                                              
    
                              
    if text.startswith("!"):
        try:
            _handle_chat_command(cardinal, chat_id, author_id, text)
        except Exception as exc:
            LOGGER.error("%s Failed to process command %s: %s", LOGGER_PREFIX, text, exc)
        return

                                                                              
                                          
                                                                              
    
                                                             
    state_key = (chat_id, author_id)
    state = FUNPAY_STATES.get(state_key)
    
    if state:
        pass
    
