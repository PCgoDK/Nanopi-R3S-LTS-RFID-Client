#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ] || [ "$#" -ne 1 ]; then
    echo "Usage: sudo sh $0 http://<app-address>:<port>/" >&2
    exit 1
fi

url=$1
python3 - "$url" <<'PY'
import sys
from urllib.parse import urlsplit

try:
    url = urlsplit(sys.argv[1])
    valid = (
        url.scheme == "http" and url.hostname and url.port
        and url.username is None and url.password is None
        and url.path in ("", "/") and not url.query and not url.fragment
        and not any(c.isspace() for c in sys.argv[1])
    )
except ValueError:
    valid = False
if not valid:
    sys.exit("Provide an HTTP app origin with a port, without credentials or a path.")
PY

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
source_dir=$(dirname -- "$script_dir")
session_script="$source_dir/deploy/nanopi-rfid-kiosk.sh"
user=rfid-display
config=/etc/lightdm/lightdm.conf.d/90-nanopi-rfid-kiosk.conf

if [ ! -f "$session_script" ] || ! systemctl is-enabled nanopi-rfid-client.service >/dev/null 2>&1; then
    echo "Install the RFID service and run this script from the complete repository." >&2
    exit 1
fi
if id "$user" >/dev/null 2>&1 && [ ! -f "$config" ]; then
    echo "User $user already exists; refusing to reuse it." >&2
    exit 1
fi

DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    xserver-xorg x11-xserver-utils xinit openbox epiphany-browser xdotool lightdm curl
if ! id "$user" >/dev/null 2>&1; then
    useradd --create-home --shell /bin/sh "$user"
fi
install -d -m 0755 /etc/nanopi-rfid-client /etc/lightdm/lightdm.conf.d
printf '%s\n' "$url" > /etc/nanopi-rfid-client/kiosk-url
chmod 0644 /etc/nanopi-rfid-client/kiosk-url
install -o root -g root -m 0755 "$session_script" /usr/local/bin/nanopi-rfid-kiosk
cat > /usr/share/xsessions/nanopi-rfid-kiosk.desktop <<'EOF'
[Desktop Entry]
Name=NanoPi RFID Kiosk
Exec=/usr/local/bin/nanopi-rfid-kiosk
Type=Application
EOF
cat > "$config" <<EOF
[Seat:*]
autologin-user=$user
autologin-user-timeout=0
user-session=nanopi-rfid-kiosk
EOF
systemctl enable lightdm.service
echo "Kiosk configured. Start with: sudo systemctl restart lightdm.service"
