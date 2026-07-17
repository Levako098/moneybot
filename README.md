# MoneyBot

Telegram-бот для управления аккаунтом FunPay. Работает на aiogram 3, принимает команды только от владельца и поддерживает плагины формата Cardinal.

## Возможности

- просмотр информации об аккаунте и ограничений рейтинга FunPay;
- экспорт активных лотов в CSV с названиями, ID, категориями и ссылками;
- настройка автоматической выдачи отдельного текста для каждого лота;
- пересылка сообщений FunPay в Telegram;
- ответ в чат FunPay через кнопку в уведомлении;
- гибкая фильтрация уведомлений по категориям;
- автоматические ответы на сообщения с шаблонами и задержкой;
- отдельные шаблоны ответов на отзывы для каждой оценки;
- просмотр и создание тикетов FunPay Support;
- загрузка, включение и отключение совместимых Cardinal-плагинов;
- регистрация команд включённых плагинов в меню Telegram.
- просмотр CPU, RAM, диска и аптайма через `/system`;
- скачивание файловых логов ZIP-архивом через `/log`;
- управление логированием и автоочисткой из Telegram.

## Требования

- Windows 10/11 или Linux с systemd;
- Python 3.11 или новее;
- Git для установки Steam-совместимости;
- Telegram Bot Token;
- активный FunPay Golden Key.

Для плагинов могут потребоваться дополнительные ключи API и зависимости.

## Установка на Windows

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

## Установка на Linux

Для Debian 12 или другого дистрибутива с Python 3.11:

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv git
git clone https://github.com/Levako098/moneybot.git
cd moneybot
cp config.example.json config.json
nano config.json
chmod +x start.sh
./start.sh
```

Скрипт создаст `bot/.venv`, установит зависимости и запустит бота. Для другого исполняемого файла Python задайте его явно:

```bash
PYTHON_BIN=/usr/bin/python3.12 ./start.sh
```

## Служба systemd

Рекомендуемый путь установки службы — `/opt/moneybot`:

```bash
sudo useradd --system --create-home --home-dir /opt/moneybot --shell /usr/sbin/nologin moneybot
sudo git clone https://github.com/Levako098/moneybot.git /opt/moneybot
sudo cp /opt/moneybot/config.example.json /opt/moneybot/config.json
sudo nano /opt/moneybot/config.json
sudo chown -R moneybot:moneybot /opt/moneybot
sudo chmod 600 /opt/moneybot/config.json
sudo chmod +x /opt/moneybot/start.sh
sudo cp /opt/moneybot/deploy/moneybot.service /etc/systemd/system/moneybot.service
sudo systemctl daemon-reload
sudo systemctl enable --now moneybot
```

Управление службой:

```bash
sudo systemctl status moneybot
sudo systemctl restart moneybot
sudo systemctl stop moneybot
sudo journalctl -u moneybot -f
```

Unit автоматически перезапускает процесс после сбоя и запускает его после перезагрузки сервера. Основной файловый журнал сохраняется в `bot/data/moneybot.log` и доступен владельцу через `/log`.

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
├── start.sh
├── config.example.json
├── config.json              # локальный файл, не попадает в Git
├── bot/
│   ├── main.py
│   ├── config.py
│   ├── requirements.txt
│   ├── compat/
│   └── services/
├── deploy/
│   └── moneybot.service
└── plugins/
```

Настройки автоответов, уведомлений и состояния плагинов создаются автоматически и хранятся в runtime-файлах, исключённых из Git.

## Безопасность

Не публикуйте `config.json`, Telegram Bot Token, Golden Key, PHPSESSID и API-ключи. Если секрет попал в открытый репозиторий, немедленно отзовите или замените его.

Плагины в каталоге `plugins/` выполняют сторонний код. Перед включением проверяйте их исходники и используемые ими API-ключи.
