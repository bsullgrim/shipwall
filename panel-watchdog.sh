#!/usr/bin/env bash
# panel-watchdog.sh -- detect a wedged panel link and recover it with usbreset.
# The service logs "[push]" on every successful serial write (~every 10s). If
# none appear for WEDGE_SECS, the USB link wedged -- reset the panel by its
# stable VID:PID so it re-enumerates (udev symlink + serial-reopen recover it).
set -uo pipefail

WEDGE_SECS=150          # no [push] for this long => wedged
VID_PID="239a:8125"     # Adafruit MatrixPortal ESP32-S3

last_push=$(journalctl -u shipwall --since "-10 min" -o short-unix 2>/dev/null \
            | grep -F '[push]' | tail -1 | awk '{print int($1)}')
now=$(date +%s)

if [ -z "${last_push:-}" ]; then
    if systemctl is-active --quiet shipwall; then
        echo "watchdog: no [push] in 10min, service active -> usbreset $VID_PID"
        usbreset "$VID_PID"
    else
        echo "watchdog: no [push] but service not active; skipping"
    fi
    exit 0
fi

age=$(( now - last_push ))
if [ "$age" -ge "$WEDGE_SECS" ]; then
    echo "watchdog: last [push] ${age}s ago (>=${WEDGE_SECS}) -> usbreset $VID_PID"
    usbreset "$VID_PID"
else
    echo "watchdog: last [push] ${age}s ago, healthy"
fi
