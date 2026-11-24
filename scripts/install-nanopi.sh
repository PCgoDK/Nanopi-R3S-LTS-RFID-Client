#!/bin/sh
set -eu

APP_NAME="nanopi-rfid-client"
APP_USER="rfid-client"
APP_GROUP="rfid-client"
INSTALL_DIR="/opt/${APP_NAME}"
APP_DIR="${INSTALL_DIR}/app"
VENV_DIR="${INSTALL_DIR}/venv"
STATE_DIR="/var/lib/${APP_NAME}"
CONFIG_DIR="/etc/${APP_NAME}"
ENV_FILE="${CONFIG_DIR}/${APP_NAME}.env"
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SOURCE_DIR=$(dirname -- "$SCRIPT_DIR")

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this installer as root: sudo $0" >&2
    exit 1
fi

if ! command -v systemctl >/dev/null 2>&1 || [ ! -d /run/systemd/system ]; then
    echo "This installer requires a running systemd system." >&2
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "Install Python 3 before running this installer." >&2
    exit 1
fi

VENV_CHECK=$(mktemp -d)
trap 'rm -rf "$VENV_CHECK"' EXIT HUP INT TERM
if ! python3 -m venv "${VENV_CHECK}/test" >/dev/null 2>&1; then
    echo "Python venv support is required. On Ubuntu/Debian, run: apt install python3-venv" >&2
    exit 1
fi
rm -rf "${VENV_CHECK}/test"
rmdir "$VENV_CHECK"
trap - EXIT HUP INT TERM

if [ ! -f "${SOURCE_DIR}/requirements.txt" ] || [ ! -d "${SOURCE_DIR}/app" ] \
    || [ ! -f "${SOURCE_DIR}/deploy/${APP_NAME}.service" ]; then
    echo "Run this script from a complete repository checkout." >&2
    exit 1
fi

getent group dialout >/dev/null || {
    echo "The dialout group is required for serial-port access." >&2
    exit 1
}

if ! getent group "$APP_GROUP" >/dev/null; then
    groupadd --system "$APP_GROUP"
fi

if ! id "$APP_USER" >/dev/null 2>&1; then
    useradd --system --gid "$APP_GROUP" --groups dialout \
        --home-dir "$STATE_DIR" --no-create-home --shell /usr/sbin/nologin "$APP_USER"
else
    usermod --append --groups "$APP_GROUP",dialout "$APP_USER"
fi

install -d -o root -g root -m 0755 "$INSTALL_DIR" "$APP_DIR"
install -d -o root -g "$APP_GROUP" -m 0750 "$STATE_DIR"
install -d -o "$APP_USER" -g "$APP_GROUP" -m 0700 "$STATE_DIR/sniffer"
install -d -o root -g root -m 0755 "$CONFIG_DIR"

cp -a "${SOURCE_DIR}/app/." "${APP_DIR}/"
install -o root -g root -m 0644 "${SOURCE_DIR}/requirements.txt" "${INSTALL_DIR}/requirements.txt"
chown -R root:root "$APP_DIR"
chmod -R u=rwX,go=rX "$APP_DIR"

if [ ! -x "${VENV_DIR}/bin/python" ]; then
    python3 -m venv "$VENV_DIR"
fi
"${VENV_DIR}/bin/python" -m pip install --upgrade pip
"${VENV_DIR}/bin/python" -m pip install -r "${INSTALL_DIR}/requirements.txt"

if [ ! -f "$ENV_FILE" ]; then
    umask 077
    printf '%s\n' \
        'RFID_HOST=127.0.0.1' \
        'RFID_PORT=8080' > "$ENV_FILE"
fi
chown root:root "$ENV_FILE"
chmod 0600 "$ENV_FILE"

install -o root -g root -m 0644 "${SOURCE_DIR}/deploy/${APP_NAME}.service" "$SERVICE_FILE"
systemctl daemon-reload
systemctl enable "${APP_NAME}.service"
systemctl restart "${APP_NAME}.service"

echo "Installed and started ${APP_NAME}.service"
echo "Check status with: systemctl status ${APP_NAME}.service"
echo "The web app listens on 127.0.0.1:8080 by default."
echo "For USB serial access, log out and back in if the dialout membership was just added."
