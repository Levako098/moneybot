#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
SERVICE_NAME="${MONEYBOT_SERVICE_NAME:-moneybot}"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
DETECTED_USER="$(stat -c '%U' "$ROOT_DIR" 2>/dev/null || true)"
if [[ -z "$DETECTED_USER" || "$DETECTED_USER" == "UNKNOWN" ]]; then
    DETECTED_USER="$(id -un)"
fi
SERVICE_USER="${MONEYBOT_USER:-$DETECTED_USER}"
DETECTED_GROUP="$(stat -c '%G' "$ROOT_DIR" 2>/dev/null || true)"
if [[ -z "$DETECTED_GROUP" || "$DETECTED_GROUP" == "UNKNOWN" ]]; then
    DETECTED_GROUP="$(id -gn "$SERVICE_USER" 2>/dev/null || id -gn 2>/dev/null || true)"
fi
if [[ -z "$DETECTED_GROUP" ]]; then
    DETECTED_GROUP="$SERVICE_USER"
fi
SERVICE_GROUP="${MONEYBOT_GROUP:-$DETECTED_GROUP}"
PRINT_ONLY=false
if [[ "${1:-}" == "--print-unit" ]]; then
    PRINT_ONLY=true
fi

if [[ $EUID -ne 0 && "$PRINT_ONLY" == false ]]; then
    exec sudo env \
        MONEYBOT_SERVICE_NAME="$SERVICE_NAME" \
        MONEYBOT_USER="$SERVICE_USER" \
        MONEYBOT_GROUP="$SERVICE_GROUP" \
        "$0" "$@"
fi

if [[ ! -f "$ROOT_DIR/start.sh" || ! -f "$ROOT_DIR/config.json" ]]; then
    echo "start.sh or config.json was not found in $ROOT_DIR" >&2
    exit 1
fi

chmod +x "$ROOT_DIR/start.sh"

render_unit() {
    cat <<EOF
[Unit]
Description=MoneyBot FunPay Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory="$ROOT_DIR"
ExecStart="$ROOT_DIR/start.sh"
Restart=always
RestartSec=5
KillSignal=SIGINT
TimeoutStopSec=30
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONIOENCODING=utf-8
NoNewPrivileges=true
PrivateTmp=true
UMask=0077

[Install]
WantedBy=multi-user.target
EOF
}

if [[ "$PRINT_ONLY" == true ]]; then
    render_unit
    exit 0
fi

render_unit > "$SERVICE_FILE"

systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"

echo "Installed $SERVICE_NAME.service"
echo "Project: $ROOT_DIR"
echo "User: $SERVICE_USER:$SERVICE_GROUP"
echo "Status: systemctl status $SERVICE_NAME"
echo "Logs: journalctl -u $SERVICE_NAME -f"
