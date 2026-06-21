#!/usr/bin/env bash
# panel-watchdog.sh -- detect a wedged panel link OR a wedged firmware and
# recover. Two distinct failures, two signals:
#
#   1. USB link wedged / panel gone: the service logs "[push]" on every serial
#      write (~10s). No [push] for WEDGE_SECS while the service runs => the link
#      died. Recover with usbreset so the device re-enumerates.
#
#   2. Firmware wedged (render loop hung): pushes keep succeeding (USB peripheral
#      stays alive), so [push] looks healthy -- but the board stops emitting its
#      "[heartbeat]" liveness line. The SERVICE itself now detects this and
#      reopens the port to reboot the chip; this watchdog is the BACKSTOP for the
#      case where even that fails (service stuck, reopen ineffective). Recovery
#      here is a service RESTART, not usbreset -- a usbreset does not reliably
#      reboot a hung ESP32 CPU; opening the port (which restart forces) asserts
#      DTR/RTS and does.
set -uo pipefail
WEDGE_SECS=150          # no [push] for this long (service active) => link wedged
HB_WEDGE_SECS=180       # no [heartbeat] for this long (but pushes fine) => fw hung
                        # (longer than the service's own 90s self-heal, so this
                        #  only fires if the service failed to recover itself)
VID_PID="239a:8125"     # Adafruit MatrixPortal ESP32-S3

# GUARD: a stopped/restarting service is NOT a wedged panel. Resetting USB when
# there is no producer just manufactures re-enumeration events and, because the
# last [push] lingers in the journal window and ages past WEDGE_SECS, fires a
# reset every run -- a self-inflicted reset loop. Do nothing unless active.
if ! systemctl is-active --quiet shipwall; then
    echo "watchdog: shipwall not active; skipping (no reset)"
    exit 0
fi

now=$(date +%s)

# Look back only a little beyond the wedge threshold, so a stale line can't keep
# re-triggering across many runs. (Was 10min, which let one gap fire ~10x.)
last_push=$(journalctl -u shipwall --since "-6 min" -o short-unix 2>/dev/null \
            | grep -F '[push]' | tail -1 | awk '{print int($1)}')
last_hb=$(journalctl -u shipwall --since "-6 min" -o short-unix 2>/dev/null \
            | grep -F '[heartbeat]' | tail -1 | awk '{print int($1)}')

# --- Failure 1: USB link wedged (no pushes at all) ---------------------------
if [ -z "${last_push:-}" ]; then
    echo "watchdog: service active, no [push] in window -> usbreset $VID_PID"
    usbreset "$VID_PID"
    exit 0
fi
push_age=$(( now - last_push ))
if [ "$push_age" -ge "$WEDGE_SECS" ]; then
    echo "watchdog: last [push] ${push_age}s ago (>=${WEDGE_SECS}), service active -> usbreset $VID_PID"
    usbreset "$VID_PID"
    exit 0
fi

# --- Failure 2: firmware wedged (pushes fine, heartbeat stale) ---------------
# Only meaningful if pushes are healthy (checked above) AND we've seen at least
# one heartbeat in the window (older firmware without heartbeats won't trip this;
# absence of any heartbeat line is treated as "feature not present", not a hang).
if [ -n "${last_hb:-}" ]; then
    hb_age=$(( now - last_hb ))
    if [ "$hb_age" -ge "$HB_WEDGE_SECS" ]; then
        echo "watchdog: [push] healthy but no [heartbeat] for ${hb_age}s (>=${HB_WEDGE_SECS}) -> firmware wedged, restarting shipwall"
        systemctl restart shipwall
        exit 0
    fi
    echo "watchdog: last [push] ${push_age}s ago, last [heartbeat] ${hb_age}s ago, healthy"
else
    # No heartbeat line seen -- either pre-heartbeat firmware, or the service
    # hasn't logged one yet (it logs every 30s). Fall back to push-only health.
    echo "watchdog: last [push] ${push_age}s ago, healthy (no heartbeat line yet)"
fi
