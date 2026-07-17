# MoneyBot

Telegram-бот для управления аккаунтом FunPay. Работает на aiogram 3, принимает команды только от владельца и поддерживает плагины формата Cardinal.

## Возможности

- просмотр информации об аккаунте и ограничений рейтинга FunPay;
- пересылка сообщений FunPay в Telegram;
- ответ в чат FunPay через кнопку в уведомлении;
- гибкая фильтрация уведомлений по категориям;
- автоматические ответы на сообщения с шаблонами и задержкой;
- отдельные шаблоны ответов на отзывы для каждой оценки;
- просмотр и создание тикетов FunPay Support;
- загрузка, включение и отключение совместимых Cardinal-плагинов;
- регистрация команд включённых плагинов в меню Telegram.

## Требования

- Windows 10/11;
- Python 3.11;
- Telegram Bot Token;
- активный FunPay Golden Key.

Для плагинов могут потребоваться дополнительные ключи API и зависимости.

## Установка

1. Клонируйте репозиторий:

```powershell
git clone https://github.com/Levako098/moneybot.git
cd moneybot
```

2. Создайте рабочий конфиг из примера:

```powershell
Copy-Item config.example.json config.json
```

3. Заполните обязательные поля в `config.json`:

```json
{
  "BOT_TOKEN": "TOKEN_FROM_BOTFATHER",
  "OWNERID": 123456789,
  "SOURCE_GOLDEN_KEY": "FUNPAY_GOLDEN_KEY",
  "FUNPAY_SUPPORT_PHPSESSID": ""
}
```

4. Запустите `start.cmd`.

При первом запуске будет создано виртуальное окружение `bot/.venv` и установлены зависимости.

## Конфигурация

| Поле | Назначение |
| --- | --- |
| `BOT_TOKEN` | Токен Telegram-бота от BotFather |
| `OWNERID` | Telegram ID владельца, которому разрешён доступ |
| `SOURCE_GOLDEN_KEY` | Значение Golden Key аккаунта FunPay |
| `FUNPAY_SUPPORT_PHPSESSID` | Сессия Support для работы с тикетами, необязательно |
| `GEMINI_API_KEY` | Ключ Gemini для совместимых функций и плагинов |
| `GEMINI_MODEL` | Используемая модель Gemini |
| `OPENROUTER_API_KEY` | Ключ OpenRouter для совместимых функций и плагинов |
| `OPENROUTER_MODEL` | Используемая модель OpenRouter |

## Структура

```text
moneybot/
├── start.cmd
├── config.example.json
├── config.json              # локальный файл, не попадает в Git
├── bot/
│   ├── main.py
│   ├── config.py
│   ├── requirements.txt
│   ├── compat/
│   └── services/
└── plugins/
```

Настройки автоответов, уведомлений и состояния плагинов создаются автоматически и хранятся в runtime-файлах, исключённых из Git.

## Безопасность

Не публикуйте `config.json`, Telegram Bot Token, Golden Key, PHPSESSID и API-ключи. Если секрет попал в открытый репозиторий, немедленно отзовите или замените его.

Плагины в каталоге `plugins/` выполняют сторонний код. Перед включением проверяйте их исходники и используемые ими API-ключи.
