#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

export PYTHONUNBUFFERED=1
export PYTHONIOENCODING=utf-8
export PIP_DISABLE_PIP_VERSION_CHECK=1

find_python() {
    if [[ -n "${PYTHON_BIN:-}" ]]; then
        printf '%s\n' "$PYTHON_BIN"
        return
    fi
    if command -v python3.11 >/dev/null 2>&1; then
        printf '%s\n' "python3.11"
        return
    fi
    if command -v python3 >/dev/null 2>&1; then
        printf '%s\n' "python3"
        return
    fi
    echo "Python 3.11 or newer is required." >&2
    exit 1
}

PYTHON="$(find_python)"
if ! "$PYTHON" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
    echo "Python 3.11 or newer is required." >&2
    exit 1
fi

VENV_DIR="$ROOT_DIR/bot/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"

if [[ ! -x "$VENV_PYTHON" ]]; then
    echo "Creating Python environment..."
    "$PYTHON" -m venv "$VENV_DIR"
fi

if ! "$VENV_PYTHON" -c 'import FunPayAPI, aiogram, telebot, requests, bs4, psutil' >/dev/null 2>&1; then
    echo "Installing dependencies..."
    "$VENV_PYTHON" -m pip install -r "$ROOT_DIR/bot/requirements.txt"
fi

if ! "$VENV_PYTHON" -c 'import pysteamauth' >/dev/null 2>&1; then
    echo "Installing Steam plugin compatibility..."
    "$VENV_PYTHON" -m pip install --no-deps pysteamauth==1.1.2
fi

if ! "$VENV_PYTHON" -c "import importlib.util; raise SystemExit(importlib.util.find_spec('steamlib') is None)" >/dev/null 2>&1; then
    if ! command -v git >/dev/null 2>&1; then
        echo "git is required to install steamlib." >&2
        exit 1
    fi
    echo "Installing Steam library..."
    "$VENV_PYTHON" -m pip install --no-deps "git+https://github.com/sometastycake/steamlib.git"
fi

exec "$VENV_PYTHON" -m bot.main
