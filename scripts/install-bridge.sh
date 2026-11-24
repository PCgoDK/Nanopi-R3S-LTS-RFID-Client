#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this installer as root." >&2
    exit 1
fi

for tool in systemctl systemd-run nmcli ip nft busybox resolvectl ss python3; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "Missing required command: $tool" >&2
        exit 1
    fi
done
if [ ! -d /run/systemd/system ] || ! getent group rfid-client >/dev/null; then
    echo "Install the RFID application on a running systemd system first." >&2
    exit 1
fi
if systemctl is-active --quiet nanopi-rfid-bridge-session.service; then
    echo "Stop the bridge before installing or updating its controller." >&2
    exit 1
fi
if [ -f /run/nanopi-rfid-bridge/state.json ] || [ -f /run/nanopi-rfid-bridge/watchdog ]; then
    echo "Bridge startup or recovery is incomplete; inspect the bridge service before installing." >&2
    exit 1
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SOURCE_DIR=$(dirname -- "$SCRIPT_DIR")
DEST=/opt/nanopi-rfid-client/bridge
install -d -o root -g root -m 0755 "$DEST"
install -o root -g root -m 0755 "$SOURCE_DIR/deploy/bridge_controller.py" "$DEST/bridge_controller.py"
python3 -c 'from pathlib import Path; p = Path("/opt/nanopi-rfid-client/bridge/bridge_controller.py"); p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n"))'
install -o root -g root -m 0755 "$SOURCE_DIR/deploy/print_capture.py" "$DEST/print_capture.py"
python3 -c 'from pathlib import Path; p = Path("/opt/nanopi-rfid-client/bridge/print_capture.py"); p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n"))'
for service in nanopi-rfid-bridge-controller.service nanopi-rfid-bridge-session.service nanopi-rfid-capture.service; do
    install -o root -g root -m 0644 "$SOURCE_DIR/deploy/$service" "/etc/systemd/system/$service"
done
systemctl daemon-reload
systemctl enable nanopi-rfid-bridge-controller.service
systemctl restart nanopi-rfid-bridge-controller.service
echo "Bridge controller installed. The bridge session is not enabled at boot."
echo "Before starting the bridge, the web app must listen on 0.0.0.0:8080."
