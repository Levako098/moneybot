from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class BotConfig:
    bot_token: str
    owner_id: int
    golden_key: str
    support_phpsessid: str


def load_config(path: Path = CONFIG_PATH) -> BotConfig:
    try:
        with path.open("r", encoding="utf-8-sig") as config_file:
            data = json.load(config_file)
    except FileNotFoundError as error:
        raise ConfigError(f"Файл конфигурации не найден: {path}") from error
    except json.JSONDecodeError as error:
        raise ConfigError(
            f"Некорректный JSON в {path}, строка {error.lineno}, "
            f"столбец {error.colno}"
        ) from error
    except OSError as error:
        raise ConfigError(f"Не удалось прочитать {path}: {error}") from error

    if not isinstance(data, dict):
        raise ConfigError("В корне config.json должен быть JSON-объект")

    bot_token = data.get("BOT_TOKEN")
    golden_key = data.get("SOURCE_GOLDEN_KEY")
    support_phpsessid = data.get("FUNPAY_SUPPORT_PHPSESSID")
    owner_id = data.get("OWNERID")

    if not isinstance(bot_token, str) or not bot_token.strip():
        raise ConfigError("В config.json отсутствует BOT_TOKEN")
    if not isinstance(golden_key, str) or not golden_key.strip():
        raise ConfigError("В config.json отсутствует SOURCE_GOLDEN_KEY")
    if not isinstance(support_phpsessid, str):
        support_phpsessid = ""
    if isinstance(owner_id, bool):
        raise ConfigError("OWNERID должен быть целым числом")
    try:
        owner_id = int(owner_id)
    except (TypeError, ValueError) as error:
        raise ConfigError("OWNERID должен быть целым числом") from error

    return BotConfig(
        bot_token.strip(),
        owner_id,
        golden_key.strip(),
        support_phpsessid.strip(),
    )
