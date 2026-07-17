@echo off
setlocal
cd /d "%~dp0"

if not exist "bot\.venv\Scripts\python.exe" (
    echo Creating Python environment...
    py -3.11 -m venv "bot\.venv"
    if errorlevel 1 goto :error
)

"bot\.venv\Scripts\python.exe" -c "import FunPayAPI, aiogram, telebot, requests, bs4" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies...
    "bot\.venv\Scripts\python.exe" -m pip install -r "bot\requirements.txt"
    if errorlevel 1 goto :error
)

"bot\.venv\Scripts\python.exe" -c "import pysteamauth" >nul 2>&1
if errorlevel 1 (
    echo Installing Steam plugin compatibility...
    "bot\.venv\Scripts\python.exe" -m pip install --no-deps pysteamauth==1.1.2
    if errorlevel 1 goto :error
)

"bot\.venv\Scripts\python.exe" -c "import importlib.util; assert importlib.util.find_spec('steamlib')" >nul 2>&1
if errorlevel 1 (
    echo Installing Steam library...
    "bot\.venv\Scripts\python.exe" -m pip install --no-deps "git+https://github.com/sometastycake/steamlib.git"
    if errorlevel 1 goto :error
)

"bot\.venv\Scripts\python.exe" -m bot.main
set "exit_code=%errorlevel%"
echo.
pause
exit /b %exit_code%

:error
echo.
echo Failed to prepare the bot.
pause
exit /b 1
