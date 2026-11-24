#!/bin/sh
set -eu

URL_FILE=/etc/nanopi-rfid-client/kiosk-url
if [ ! -r "$URL_FILE" ]; then
    echo "Missing kiosk URL: $URL_FILE" >&2
    exit 1
fi
IFS= read -r url < "$URL_FILE"

xset s off
xset -dpms
xset s noblank
openbox &
window_manager=$!
trap 'kill "$window_manager" 2>/dev/null || true' EXIT HUP INT TERM

export LIBGL_ALWAYS_SOFTWARE=1
export GSK_RENDERER=cairo
export WEBKIT_DISABLE_DMABUF_RENDERER=1
export WEBKIT_DISABLE_COMPOSITING_MODE=1

until curl --noproxy '*' --fail --silent --show-error --max-time 3 --output /dev/null "$url"; do
    echo "Waiting for RFID web app at $url" >&2
    sleep 5
done
profile="$HOME/.local/share/org.gnome.Epiphany.WebApp_nanopirfid"
mkdir -p "$profile"
touch "$profile/.app"
LD_LIBRARY_PATH=/usr/lib/aarch64-linux-gnu \
    systemd-run --user --scope --quiet -p MemoryMax=350M -p MemorySwapMax=128M \
    epiphany-browser --application-mode --profile="$profile" "$url" &
browser=$!
(
    if timeout 30s xdotool search --sync --onlyvisible --name 'NanoPi UHF RFID Client' >/dev/null 2>&1; then
        sleep 5
        window=$(xdotool search --onlyvisible --name 'NanoPi UHF RFID Client' | tail -n 1)
        [ -n "$window" ] && xdotool windowactivate --sync "$window" key F11
    else
        echo "RFID browser window did not appear; fullscreen was not activated." >&2
    fi
) &
wait "$browser" || echo "Browser exited with an error." >&2
echo "Browser stopped; restart the display session to try again." >&2
while :; do sleep 60; done
