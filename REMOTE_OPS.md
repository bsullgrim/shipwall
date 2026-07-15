# Ship Wall -- Unattended & Remote Operations

Running the wall at Danger Island while you're in Massachusetts. The layers:
keep it alive (systemd), keep it tame (monthly compaction), reach it (Tailscale
or Pi Connect), reflash it without being there (arduino-cli over USB), and a
maintenance reference for the things that actually go wrong.

## 1. Keep it alive -- systemd services

Two services run unattended, each its own unit:
- **`shipwall.service`** -- the AIS ingestion + panel feeder (`register_service.py`).
- **`shipwall-stats.service`** -- the passage-stats web page (`passage_stats.py`,
  port 8090). Independent of the panel service; reads `passages.csv` read-only.

```bash
sudo cp shipwall.service /etc/systemd/system/
# edit the Environment= lines and User/paths first!
sudo systemctl daemon-reload
sudo systemctl enable --now shipwall.service
journalctl -u shipwall -f          # live logs
```

`Restart=always` covers crashes; `WantedBy=multi-user.target` + `enable` covers
reboots (cottage power blips). The user must be in `dialout` to reach the ESP32:

```bash
sudo usermod -aG dialout grims      # then log out/in, or reboot
```

### Things that bit us setting these up (so they don't again)

- **Use the venv Python in `ExecStart`.** The deps (`aiohttp`, `websockets`,
  `pyserial`) live in `/home/grims/shipwall/.venv`, not system Python. The unit
  must run `/home/grims/shipwall/.venv/bin/python3`, or it crash-loops on
  `ModuleNotFoundError: No module named 'aiohttp'`.
- **systemd does NOT read `.env`.** Config the script picks up from `.env` when
  run by hand must be repeated as `Environment=` lines in the unit, or it's
  missing under systemd (silent: warm-seed empty, names ghost, etc.).
- **`MMSI_DB` path:** the env var is `MMSI_DB`, the file is `mmsi_database.json`
  (NOT `mmsi_db.json`). Point it at the real filename or the identity DB never
  loads -- the banner will say `[mmsidb] loaded N ...` when it's right.
- **`StartLimitIntervalSec`/`StartLimitBurst` go in `[Unit]`,** not `[Service]`
  (systemd ignores them in the wrong section).
- **`PYTHONUNBUFFERED=1`** in the unit makes the service's own prints (`[push]`,
  banner) stream to the journal live instead of sitting in a buffer.
- **Write unit files with `tee`, not nano over SSH.** Hand-editing scrambled
  lines once (an `ExecStart` ended up outside `[Service]`). `sudo tee /etc/
  systemd/system/NAME.service > /dev/null <<'EOF' ... EOF` writes atomically.

### The stats page service (shipwall-stats)

```bash
sudo tee /etc/systemd/system/shipwall-stats.service > /dev/null <<'EOF'
[Unit]
Description=St. Lawrence Ship Wall passage stats page
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=120
StartLimitBurst=6

[Service]
Type=simple
User=grims
WorkingDirectory=/home/grims/shipwall
Environment=PASSAGE_LOG=/home/grims/shipwall/passages.csv
Environment=MMSI_DB=/home/grims/shipwall/mmsi_database.json
Environment=FUN_STATS=/home/grims/shipwall/fun_stats.json
Environment=STATS_PORT=8090
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/grims/shipwall/.venv/bin/python3 /home/grims/shipwall/passage_stats.py
Restart=always
RestartSec=8
OOMPolicy=continue

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now shipwall-stats.service
```

Reach it at `http://shipwall.local:8090` (or `http://<pi-ip>:8090`). It's
stateless -- re-reads `passages.csv` on every load -- so it never needs a
restart to stay current. The Hall of Fame tab appears only if `fun_stats.json`
exists (build it with `fun_stats.py`).

## 2. Keep it tame -- monthly register compaction

`register.csv` grows as vessels are logged. It's not a disk problem (~MBs/season)
but every service RESTART reads the whole file (warm-start, dedup seed, passage
backfill), so an un-compacted file slowly lengthens boot time. The timer runs
`clean_register.py` monthly, atomically swapping in the compacted file with a
dated backup.

```bash
sudo cp shipwall-clean.service shipwall-clean.timer /etc/systemd/system/
sudo cp clean_register.sh /home/grims/shipwall/
sudo chmod +x /home/grims/shipwall/clean_register.sh
sudo systemctl daemon-reload
sudo systemctl enable --now shipwall-clean.timer
systemctl list-timers shipwall-clean        # confirm next run
sudo systemctl start shipwall-clean.service # test it once, now
journalctl -u shipwall-clean -n 20          # check it worked
```

The wrapper briefly stops the service around the swap (panel holds its last
frame for ~2s; AISStream backfills on reconnect), keeps the 3 most recent
monthly backups, and aborts harmlessly if the cleaned file looks wrong --
leaving the original untouched.

NOTE: the register now also writes a periodic position-refresh row (every
`REGISTER_REFRESH_MINS`, default 30) so continuously-present ships stay fresh
enough to warm-seed after a restart. This adds modest, bounded growth (≈1 row
per present ship per interval), which the monthly clean keeps in check. The
flicker-resistant `log_vessel` still suppresses per-ping spam, so the file
stays small overall.

## 3. Reach it -- Tailscale (and Pi Connect as backup)

MA <-> Danger Island works: Tailscale builds an encrypted WireGuard tunnel over
the public internet, so distance and NAT are irrelevant. The only requirement is
that the Pi has working OUTBOUND internet at the cottage (Starlink / cellular /
DSL -- whatever's there must be up).

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --ssh                      # --ssh manages SSH keys for you
```

Then in the Tailscale admin console (login.tailscale.com):
- Find the Pi, open its machine settings, and **disable key expiry**. Otherwise
  the node's auth key expires (~6 months) and you lose access exactly when you
  can't drive up to fix it. This is the classic remote-cabin footgun.

From your laptop (also on your tailnet):

```bash
tailscale status                 # find the Pi's 100.x.y.z address or name
ssh grims@danger-pi             # MagicDNS name, or use the 100.x address
journalctl -u shipwall -f       # now you're operating it from your couch
```

**Backup path -- Raspberry Pi Connect.** Free, pre-installed on Raspberry Pi OS
Lite (the `rpi-connect-lite` package = remote shell only, which is all the Pi 3
supports). Link it to a free account as a second way in if Tailscale ever wedges
-- belt and suspenders for a box you can't physically reach:

```bash
rpi-connect on
rpi-connect signin               # opens a link to authorize against your account
```

Reach it from connect.raspberrypi.com. On Trixie it also supports remote
updates that queue even while the Pi is offline.

### Unreachable for ~3 min after every reboot (clock skew → Tailscale)

The Pi 3 has no RTC, so on boot its clock is wrong until NTP corrects it. Until
then, Tailscale's TLS to the DERP relays fails — symptom: after a reboot you
can't SSH/Tailscale in for several minutes, and the panel shows WAITING (the AIS
websocket can't connect either). The journal shows `network is unreachable` /
`no-derp-connection`, then a `time-jumped(Xm)` line once NTP syncs. This is NOT
a hang and NOT a crash — it self-resolves in a few minutes.

Fix: `fake-hwclock` restores the last-saved time at boot so the clock starts
close and TLS succeeds immediately. On Trixie the monolithic
`fake-hwclock.service` is **masked** — that's expected; the active units are
`fake-hwclock-load.service` (restores at boot), `-save.service`, and
`-save.timer` (re-saves hourly). Install:

    sudo apt install -y fake-hwclock
    sudo fake-hwclock save                            # seed with current time
    systemctl is-enabled fake-hwclock-load.service    # expect: enabled
    cat /etc/fake-hwclock.data                         # should show current UTC

Do NOT run `systemctl enable fake-hwclock` — it's masked and errors. The split
units auto-enable on install.

## 4. Adding a Wi-Fi network (e.g. Dad's, for the river)

Add the river network BEFORE you travel, while the Pi is still reachable at home.
Current Raspberry Pi OS (Trixie) uses NetworkManager:

```bash
sudo nmcli connection add type wifi con-name "dads-wifi" ssid "DADS_SSID"
sudo nmcli connection modify "dads-wifi" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "DADS_PASSWORD"
sudo nmcli connection modify "dads-wifi" connection.autoconnect yes
nmcli connection show            # confirm home + dads-wifi both saved
```

Both networks stay saved; NetworkManager auto-connects to whichever is in range.
Notes:
- **The Pi 3B is 2.4GHz-only.** Confirm Dad's router broadcasts a 2.4GHz band
  (some mesh/band-steering setups hide it).
- **Get SSID + password exactly right** (case-sensitive) -- a wrong credential
  means a headless Pi that can't get online and can't be reached.
- **Ethernet beats Wi-Fi** for an always-on box if a cable can reach the router.
- Reaching it on Dad's network: `shipwall.local` may or may not resolve there;
  this is exactly why Tailscale/Pi Connect matter -- they give a stable address
  regardless of which network it's on.

### WiFi power saving drops Tailscale (must disable)

The Pi 3's WiFi has an aggressive idle power-save mode that drops the connection
after a few minutes idle -- which takes Tailscale down with it, locking you out.
Symptom: SSH/Tailscale works for ~10 min then dies. Disable it:

```bash
iw dev wlan0 get power_save                  # "on" = the problem
sudo iw dev wlan0 set power_save off          # now
sudo nmcli connection modify "<wifi-name>" 802-11-wireless.powersave 2   # persist
```

`powersave 2` = disabled in NetworkManager's encoding. Apply it to every saved
WiFi (home AND dads-wifi). Verify it survives a reboot: `iw dev wlan0 get
power_save` should still say "off". If it reverts (netplan overriding NM), add a
boot service that forces it:

```bash
sudo tee /etc/systemd/system/wifi-powersave-off.service > /dev/null <<'EOF'
[Unit]
Description=Disable WiFi power saving
After=network.target
[Service]
Type=oneshot
ExecStart=/usr/sbin/iw dev wlan0 set power_save off
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl enable wifi-powersave-off.service
```

Ethernet sidesteps this entirely -- no WiFi means no WiFi power-save.

## 5. Reflash the panel remotely -- arduino-cli on the Pi

Changing a sprite (`ship_sprites.h`) or the firmware (`register_esp32.ino`) means
recompiling and flashing the ESP32. Because the Pi is wired to the MatrixPortal
over USB serial, you can do this over Tailscale SSH -- no physical presence.

One-time setup on the Pi:

```bash
curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh
sudo mv bin/arduino-cli /usr/local/bin/
arduino-cli config init
arduino-cli core update-index
arduino-cli core install esp32:esp32          # ESP32-S3 support
arduino-cli lib install ArduinoJson
arduino-cli lib install "Adafruit GFX Library"
arduino-cli lib install "Adafruit Protomatter"   # the 6484 needs Protomatter
```

To reflash after editing firmware (over SSH):

```bash
cd /home/grims/shipwall/register_esp32
# IMPORTANT: stop the service first -- it holds the serial port, and the flash
# needs exclusive access to /dev/ttyACM0.
sudo systemctl stop shipwall.service

# CDCOnBoot=cdc is CRITICAL for remote work: the ESP32-S3 uses native USB, and
# without this flag the serial port can disappear after a flash -- which would
# strand you, unable to reach the board for the NEXT reflash. With it, the CDC
# serial port comes up on boot every time.
FQBN="esp32:esp32:adafruit_matrixportal_esp32s3:CDCOnBoot=cdc"
arduino-cli compile --fqbn "$FQBN" register_esp32.ino
arduino-cli upload  --fqbn "$FQBN" -p /dev/ttyACM0 register_esp32.ino

sudo systemctl start shipwall.service
```

**Remote-reset:** we confirmed this S3 core auto-resets after upload (the upload
log ended with `Hard resetting via RTS pin...` and the sketch ran without a
button press). So remote reflashing works. If a future core stops doing this, a
remotely-switchable USB power port (smart hub) lets you power-cycle the board
over the network. The port re-enumerates after flashing (e.g. COM5->COM4, or
ttyACM0->ttyACM1) -- `ls /dev/ttyACM*` before/after, and use the post-flash
device for `arduino-cli monitor`.

Confirm the board ID with `arduino-cli board listall | grep -i matrixportal`.

## 6. The panel USB link -- drops, and how the system survives them

**The problem:** the MatrixPortal periodically drops off the Pi's USB bus
(`dmesg` shows `USB disconnect` + `device descriptor read ... error -32`, every
~45-80 min). The Pi's own power is fine (`vcgencmd get_throttled` = `0x0`); it's
the per-port power/signal through the Pi 3's onboard USB hub. The real fix is a
**powered (wall-supplied) USB hub** between Pi and panel -- but that's a third
wall plug. Instead, three layers make the drops harmless without new hardware:

**1. Stable device name (udev rule)** -- a drop re-enumerates the panel, often
to a DIFFERENT port number (ttyACM0 -> ttyACM1), which would break the link
since the service targets a fixed port. A udev rule keyed on the panel's stable
VID:PID gives it a permanent name that follows it across re-enumeration:

```bash
echo 'SUBSYSTEM=="tty", KERNEL=="ttyACM*", ATTRS{idVendor}=="239a", ATTRS{idProduct}=="8125", SYMLINK+="shipwall-panel"' \
  | sudo tee /etc/udev/rules.d/99-shipwall-panel.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
# point the service at the stable name:
sudo sed -i 's#ESP32_SERIAL=/dev/ttyACM0#ESP32_SERIAL=/dev/shipwall-panel#' /etc/systemd/system/shipwall.service
sudo systemctl daemon-reload && sudo systemctl restart shipwall.service
ls -l /dev/shipwall-panel        # -> ttyACMx, follows the device
```

(`239a:8125` is this MatrixPortal S3; confirm yours with `lsusb | grep -i adafruit`.)

**2. Serial resilience (in register_service.py)** -- the service no longer
exits when the port vanishes (it used to `sys.exit(1)`, which then tripped
systemd's start limit and stayed dead). It now retries every cycle: you'll see
`[serial] could not open ... (will retry)` / `[push] serial not available` until
the port returns, then it resumes. A transient drop is just a brief WAITING blip.

**3. Auto-recovery watchdog** -- if a drop WEDGES (doesn't cleanly reconnect),
the manual fix is `sudo usbreset 239a:8125`. The watchdog automates it: every
minute it checks whether `[push]` lines are still appearing in the journal; if
none for 150s while the service is active, it runs usbreset (panel re-enumerates,
udev symlink follows, service reopens). Setup:

```bash
# the script is panel-watchdog.sh in the repo; it greps the journal for [push]
# and runs `usbreset 239a:8125` if pushes have stalled. Then:
sudo tee /etc/systemd/system/panel-watchdog.service > /dev/null <<'EOF'
[Unit]
Description=Ship Wall panel USB watchdog
After=shipwall.service
[Service]
Type=oneshot
ExecStart=/home/grims/shipwall/panel-watchdog.sh
EOF
sudo tee /etc/systemd/system/panel-watchdog.timer > /dev/null <<'EOF'
[Unit]
Description=Run the panel USB watchdog every minute
[Timer]
OnBootSec=3min
OnUnitActiveSec=1min
[Install]
WantedBy=timers.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now panel-watchdog.timer
journalctl -u panel-watchdog -n 3 --no-pager   # expect "... healthy"
```

The watchdog depends on `PYTHONUNBUFFERED=1` in shipwall.service (so `[push]`
reaches the journal promptly) and on usbreset needing root (the unit runs as
root by default -- no User= line).

**Net effect:** the drops still happen, but the link survives re-enumeration, the
service survives the port vanishing, and a true wedge self-heals within ~3 min.
No third plug, no drive to the river. If you ever want to eliminate the drops at
the source rather than tolerate them, add the powered USB hub.

## 7. Maintenance reference -- the things that actually come up

### Quick remote-health checklist

```bash
systemctl is-active shipwall shipwall-stats   # both 'active'?
systemctl list-timers panel-watchdog          # watchdog scheduled?
journalctl -u shipwall -n 30 --no-pager       # recent [push] frames?
journalctl -u panel-watchdog -n 5 --no-pager  # "healthy", or recent usbreset?
dmesg | grep -iE 'disconnect|error -32|dwc_otg|FSM NP' | tail # how often is the panel dropping?
systemctl list-timers shipwall-clean          # compaction scheduled?
ls -lh /home/grims/shipwall/*.csv             # file sizes sane?
tail -5 /home/grims/shipwall/passages.csv     # crossings still logging?
df -h /                                        # SD card not full?
```
### Persistent logs (so you can diagnose the PREVIOUS boot)

By default the journal is volatile — wiped each reboot — so you can't see how the
last boot ended. Enable persistence once:

    sudo mkdir -p /var/log/journal
    sudo systemd-tmpfiles --create --prefix /var/log/journal
    sudo systemctl restart systemd-journald

After the next reboot, `journalctl --list-boots` shows history and
`journalctl -b -1 -n 40` reveals how the prior boot ended: orderly "Stopped
target" lines = clean reboot (software/scheduled/manual); a log that cuts off
mid-line = hard power loss (suspect the Pi's 5V supply or cable — not the panel's
own supply). This is the missing piece for answering *why did it reboot*.

### "The panel says STARTING UP / WAITING / nothing"

- **STARTING UP** = panel up, Pi hasn't sent a frame yet. Normal for 30-90s
  after a shared-power reboot (the Pi 3 is slow to boot + connect). If it sticks:
  `systemctl is-active shipwall` (is the service even running?), then
  `journalctl -u shipwall -n 30` for `[serial] open` and `[push]` lines.
- **WAITING for data** = the panel HAD frames and then lost them for 60s. Most
  often the USB link dropped/wedged (see section 6) -- the watchdog should
  usbreset it within ~3 min; if it's stuck, `sudo usbreset 239a:8125` by hand.
  Otherwise the service died: `systemctl status shipwall`; check `dmesg | tail`
  for USB disconnect/re-enumeration. Third possibility: the Pi recently rebooted and is in the ~3-min clock-skew
  window (see §3) — wait a couple minutes; it self-recovers, and fake-hwclock
  shrinks this to seconds. `journalctl --list-boots` confirms a reboot just
  happened.
- **Blank/dark panel** = almost always hardware: confirm the panel's own 5V
  supply, the ribbon on J-IN, and (the big one for this panel) that the firmware
  uses 5 address pins. See HARDWARE_BRINGUP.md.

### "Names show as MMSI numbers / ghosts"

The identity DB isn't loading. Check the banner for `[mmsidb] loaded N known
vessels`. If it's missing or the path is wrong, fix `MMSI_DB` in the unit to
`/home/grims/shipwall/mmsi_database.json` and restart. New unknown vessels
collect in `unknown_vessels.json`; classify them with `operator_worklist.py`.

### "Warm-start seeds 0 vessels even though ships are on the board"

A ship is only re-seeded on restart if its last LOGGED row is within the
retention window (`REGISTER_HOURS`, 18h). The periodic-refresh logging (section
2) keeps present ships fresh; if you still see 0, confirm the register is being
written: `tail -1 register.csv` should show a recent timestamp.

### "Where's Emmett never shows"

The Emmett frame is built only when the Lake Guardian (MMSI 338021074) has a fix
within `EMMETT_STALE_SECS` (default 1800). With `SHIPWALL_DEBUG=1` (or watching
`[push]` lines) you'll see `emmett=None` when there's no recent fix. Most often
this is AISStream coverage (his terrestrial station isn't in their network), not
a bug. `EMMETT_IGNORE_STALE=1` only helps if a position was ever received.

### Restarting / updating after a code change

```bash
cd /home/grims/shipwall && git pull       # or scp the changed files
sudo systemctl restart shipwall           # picks up Python changes
# firmware changes need a reflash -- see section 5
```

### API key rotation

If the key is exposed, rotate it (create new + REVOKE old at aisstream.io), then:

```bash
sudo sed -i 's/AISSTREAM_KEY=.*/AISSTREAM_KEY=NEW_KEY/' /etc/systemd/system/shipwall.service
sudo systemctl daemon-reload && sudo systemctl restart shipwall
```

The real key lives only in the deployed unit at `/etc/systemd/system/`; the
repo's `shipwall.service` keeps a `PUT_YOUR_KEY_HERE` placeholder. Keep it that
way.

### SSH host-key warning after reflashing the SD card

A fresh OS image regenerates host keys, so SSH refuses to connect ("REMOTE HOST
IDENTIFICATION HAS CHANGED"). Not an attack -- clear the old key and reconnect:

```bash
ssh-keygen -R shipwall.local      # and -R <pi-ip> if you connect by IP too
```

This is the single most valuable section to have set up before you leave: remote
access + remote reflash is the difference between "I can fix a typo from my
laptop" and "I have to drive three hours."

## 8. Corruption, recovery, and offsite backup — the day the card ate itself

On **2026-07-11** the unit got unplugged (physical power-yank at the cottage). The power-cut corrupted a batch of files **mid-write** — source `.py` files, the entire `.venv`, and `register.csv` — injecting null bytes into files that were clean in git and on the last backup. The services crash-looped, `dmesg` threw EXT4 errors, and it looked exactly like a dying SD card. **It was not:** it was recoverable corruption, fixed entirely over SSH without a card swap. This is the runbook, because the next time should take ten minutes, not a day.

**The meta-lesson first:** when a service won't start and something looks broken, the question is *which of three things it is* — **file corruption**, **hardware failure**, or a **git/config problem**. They look alike but have opposite fixes, and the worst move is to start restarting or restoring before you know. A restart destroys the journal that tells you what happened (see §7 persistent logs — have that on *before* you need it); restoring files onto a still-failing card is pointless.

Read the error in `journalctl -u shipwall -n 30` and branch:

| Error in the journal | Cause | Go to |
|---|---|---|
| `source code string cannot contain null bytes` / `UnicodeDecodeError: invalid continuation byte` | File corruption | 8a (card health), then 8c/8d |
| `ModuleNotFoundError: No module named 'aiohttp'` | venv corrupt or missing | 8c (rebuild venv) |
| `cannot execute binary file: Exec format error` on `.venv/bin/pip` | venv binaries corrupted | 8c (rebuild venv) |
| Feed/board looks wrong but service runs | Usually **not** a fault, just a downtime gap | 8e |

### 8a. Is the card FAILING, or just CORRUPTED? (the decisive test)

This is the question the whole 2026-07-11 session hinged on, and we almost got it wrong. Power-cut corruption is repairable; a worn-out card is not.

```bash
# Recent filesystem errors? (errors ONLY at boot = normal recovery; recent = damage)
sudo dmesg -T | grep -iE 'EXT4-fs error|I/O error|checksum invalid|Structure needs cleaning|Bad message' | tail -20

# Did the kernel remount read-only to protect itself? (want: rw)
mount | grep ' / ' | grep -oE 'r[ow]'

# THE DECISIVE TEST -- can the card hold a LARGE synced write? A tiny write can
# succeed on a dying card; 1.5MB forced to disk is the real test.
cd /home/grims/shipwall
python3 -c "open('cardtest.bin','w').write('x'*1500000)"
sync
python3 -c "d=open('cardtest.bin','rb').read(); print('readback:', 'CORRUPT' if b'\x00' in d else 'CLEAN', len(d))"
rm -f cardtest.bin
```

**Verdict:** no recent dmesg errors + `rw` mount + large write survives `sync` = the card is **healthy**, this was corruption, restore in place. Recurring errors, or files that re-corrupt within minutes of being restored = the card is **failing**, plan a swap (8f). On 2026-07-11 the 1.5 MB test came back CLEAN — that's what proved it was the power-cut, not the hardware, and saved an unnecessary card swap.

### 8b. Find which files are corrupted

Power-cut corruption injects NUL bytes. Text files (`.py`/`.js`/`.csv`/`.json`) must never contain a NUL, so that's the detector:

```bash
cd /home/grims/shipwall

# corrupt tracked source files:
for f in $(git ls-files '*.py' '*.js'); do
  python3 -c "import sys;sys.exit(1 if b'\x00' in open('$f','rb').read() else 0)" 2>/dev/null || echo "CORRUPT: $f"
done

# corrupt data files:
for f in passages.csv register.csv mmsi_database.json ship_to_operator.json unknown_vessels.json fun_stats.json; do
  [ -e "$f" ] && python3 -c "d=open('$f','rb').read(); print('$f:', 'CORRUPT' if b'\x00' in d else 'ok', len(d))"
done

# where are the nulls in one file? (end-clustered = a torn final write)
python3 -c "d=open('register.csv','rb').read(); print('nulls',d.count(0),'first at',d.find(0),'of',len(d))"

# a totally-corrupt file shows as 'data' not 'ASCII text':
file font5x7.js
```

### 8c. Restore corrupted SOURCE files from git (and rebuild the venv)

Code is on GitHub, so tracked files restore from the object store. **Gotcha we hit:** a plain `git checkout` *silently no-ops* on a null-corrupted file (git's index thinks it's current), leaving it corrupt. Force it, and fall back to writing clean bytes straight from the object store:

```bash
cd /home/grims/shipwall
git fetch origin
git checkout --force origin/main -- $(git ls-files '*.py' '*.js')

# if any are STILL corrupt, bypass the index entirely:
for f in $(git ls-files '*.py' '*.js'); do
  python3 -c "import sys;sys.exit(1 if b'\x00' in open('$f','rb').read() else 0)" 2>/dev/null \
    || { git cat-file -p "origin/main:$f" > "$f"; echo "rewrote $f"; }
done

# verify they're clean AND STAY clean (re-corruption = failing card):
for f in $(git ls-files '*.py' '*.js'); do python3 -c "import sys;sys.exit(1 if b'\x00' in open('$f','rb').read() else 0)" 2>/dev/null || echo "STILL CORRUPT: $f"; done

# Sanity-check GitHub itself isn't poisoned (deploy.sh committing mid-corruption
# once pushed null bytes UP to origin -- then every clone/checkout restored garbage):
git cat-file -p origin/main:register_service.py | python3 -c "import sys;d=sys.stdin.buffer.read();print('git has:','CORRUPT' if b'\x00' in d else 'CLEAN',len(d))"
# if origin is corrupt, restore that file from a clean laptop working copy and push.
```

The `.venv` is a prime corruption target (thousands of small files). You never *restore* a venv — delete and rebuild (this is also the §1 `ModuleNotFoundError` fix):

```bash
cd /home/grims/shipwall
rm -rf .venv
python3 -m venv .venv
.venv/bin/pip install --no-cache-dir --retries 5 aiohttp websockets pyserial   # or -r requirements.txt
.venv/bin/python3 -c "import aiohttp, websockets, serial; print('deps OK')"
.venv/bin/python3 -c "import register_service; print('imports OK')"
```

> A pip `Error -3 while decompressing data: invalid block type` is a **network** decompression failure (flaky cottage link), not the card — just retry.

### 8d. Restore corrupted DATA files (not in git)

Data files are gitignored in the code repo — restore from the `shipwall-data` backup repo (8g) or a laptop copy. **Hard-learned rule:** verify the replacement is clean *in its own name* on the Pi **before** renaming it into place. On 2026-07-11 we repeatedly renamed corrupt files over corrupt files blind, because a file's clean-looking header hides nulls further down.

```bash
cd /home/grims/shipwall
# get a candidate onto the Pi under a DISTINCT name, then VERIFY it first:
#   scp <laptop>\shipwall-backup\register.csv grims@shipwall:~/shipwall/register_GOOD.csv
#   (or: cp ~/shipwall-data/register.csv register_GOOD.csv)
python3 -c "d=open('register_GOOD.csv','rb').read(); print('candidate:', 'CORRUPT' if b'\x00' in d else 'CLEAN', len(d))"
head -1 register_GOOD.csv

# ONLY if CLEAN, swap it in (stop the service so it isn't appending during the swap):
sudo systemctl stop shipwall.service
mv register.csv register.csv.corrupt-$(date +%s)     # keep the corrupt one, don't delete
mv register_GOOD.csv register.csv
python3 -c "d=open('register.csv','rb').read(); print('now:', 'CORRUPT' if b'\x00' in d else 'CLEAN', len(d))"
sudo systemctl start shipwall.service
```

**Salvage** a lightly-corrupted file instead of using a stale backup — strip the nulls and keep valid rows (this recovered `register.csv` nearly whole on 2026-07-11, losing ~100 bytes instead of falling back weeks):

```bash
tr -d '\000' < register.csv.corrupt-* \
  | grep -aE '^timestamp,|^[0-9]{4}-[0-9]{2}-[0-9]{2}T' > register_salvaged.csv
python3 -c "d=open('register_salvaged.csv','rb').read(); print('salvaged:', 'CORRUPT' if b'\x00' in d else 'CLEAN', len(d))"
wc -l register_salvaged.csv
```

**Priority order under pressure:** `passages.csv` is the **irreplaceable** hand-curated crossing history — protect it first. `register.csv` is the **regenerable** coverage log (rebuilt live from AIS) — losing rows is harmless; it refills. Don't burn time salvaging `register.csv` if `passages.csv` is the one at risk.

### 8e. "The board is sparse after a restart" — usually NOT broken

The last-18h warm-start only shows vessels whose last sighting is within `REGISTER_HOURS`. If the service was **down** for hours (crash-loop, outage), that window genuinely has a hole — the wall couldn't see ships while it was down. It refills live. Before assuming breakage:

```bash
curl -s localhost:8080/latest | python3 -c "import sys,json,time; d=json.load(sys.stdin); print('feed age', int(time.time()-d['ts']) if d['ts'] else 'NO DATA', 's | ships', len(d['ships']))"
journalctl -u shipwall.service --no-pager | grep -iE '\[seed\]|\[backfill\]|warm' | tail

# how much recent data is actually in the window (gaps = downtime, not a bug):
python3 -c "import csv,datetime as dt; c=(dt.datetime.now()-dt.timedelta(hours=18)).isoformat(); r=[x for x in csv.DictReader(open('register.csv')) if x['timestamp']>=c]; print(len(r),'rows /',len({x['mmsi'] for x in r}),'vessels in 18h')"
```

Passage-logging check (passages are **rare** — an old `passages.csv` timestamp is often normal, not a fault): `tail -3 passages.csv`, then grep the journal for `[passage]` / `[backfill]` lines to confirm the machinery ran. Watch for a passage with a huge `gap_min` (hundreds/thousands of minutes) — that's a crossing *interpolated across a downtime gap*; recorded correctly, but the `pass_time` is a low-confidence guess.

### 8f. If the card really is failing (8a came back bad)

You can only reach this box remotely, so a failing card is the one thing SSH **can't** fix — it needs hands at the cottage. What you *can* do remotely:

- Salvage every still-clean file off it **now** (8b to identify clean ones, `scp` them to the laptop).
- Minimize writes — stop the crash-looping service so it stops hammering the card.
- **Don't reboot** if the data isn't backed up (a corrupt root may not boot again).

Then the rebuild needs a fresh high-endurance card physically installed: flash OS, `git clone` the code repo, run `restore.sh` (in the repo) to drop the backed-up data files in, recreate `.env` (the `AISSTREAM_KEY` — §7), rebuild `.venv`, reinstall the systemd units. All the code is on GitHub and all the data is in `shipwall-data`, so a rebuild loses nothing but the hours since the last backup.

### 8g. Offsite data backup — `shipwall-data` (the thing that saved us)

The runtime data files (`passages.csv`, `register.csv`, `mmsi_database.json`, etc.) are gitignored in the code repo — so they have their **own** private backup repo, `bsullgrim/shipwall-data`, pushed on a timer. This is what turned 2026-07-11 from a data-loss catastrophe into "restore from a few hours ago." It lives at `~/shipwall-data` on the Pi; `backup.sh` (in the code repo) copies the data files in and commits/pushes; a systemd timer runs it every 3 hours.

**Install (one-time):**

```bash
# create the PRIVATE repo on GitHub first, then:
mkdir -p ~/shipwall-data && cd ~/shipwall-data
git init && git branch -M main
git remote add origin git@github.com:bsullgrim/shipwall-data.git   # SSH, not HTTPS
git config user.email "grimsley.ben@gmail.com" && git config user.name "Ben Grimsley"
cd ~/shipwall && chmod +x backup.sh
sed -i 's/OnUnitActiveSec=6h/OnUnitActiveSec=3h/' shipwall-backup.timer   # 3h cadence
sudo cp shipwall-backup.service shipwall-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now shipwall-backup.timer
```

**Verify it actually reaches GitHub** — backups committing *locally* but not *pushing* is a silent failure (GitHub shows stale timestamps while commits pile up on the very card you're protecting against — this exact thing happened on 2026-07-11):

```bash
sudo systemctl start shipwall-backup.service          # a timer with blank NEXT needs one manual run to anchor
journalctl -u shipwall-backup.service -n 20 --no-pager
systemctl list-timers shipwall-backup.timer           # NEXT should now show ~3h out
cd ~/shipwall-data && git log origin/main..HEAD --oneline   # EMPTY = all pushed; non-empty = backlog stuck
```

**Gotchas (all hit on 2026-07-11):**

- **systemd has no ssh-agent:** the push needs a **passphrase-free** on-disk SSH key, and the remote must be **SSH** (`git@github.com:...`), not HTTPS. Test: `ssh -T git@github.com`.
- **Push rejected `(fetch first)`** = the remote diverged (a web-UI edit, or a push from the laptop). `backup.sh` now self-heals this (`pull --rebase` then retry); to fix by hand: `cd ~/shipwall-data && git pull --rebase origin main && git push origin main`.
- **`backup.sh` refuses to commit a NUL-corrupt file** (keeps the last good copy) so a backup taken right after corruption can't overwrite a clean one. Confirm the running copy has both guards: `grep -c 'is CORRUPT' backup.sh ; grep -c 'attempting pull --rebase' backup.sh` (both → 1).

**Restore *from* this repo on a fresh card:** `git clone` it alongside the code repo, then `restore.sh` reads the data files out of it (or `scp` them). See 8f.

### 8h. Root cause — stop the power-cuts

The whole incident was a physical unplug interrupting writes. Mitigations, by impact:

- **A small UPS / USB power bank inline** lets the Pi ride out a yank or shut down cleanly. Biggest single lever, and it also kills the reboot clock-skew window (§3).
- `vcgencmd get_throttled` == `0x0` confirms current power is clean; nonzero = suspect the supply.
- **TODO:** harden the asset loaders (`load_font` / `load_sprites` / `load_emmett_data`) with `errors="replace"` + try/except, so **one** corrupt byte degrades to a missing glyph instead of crash-looping the whole web app — on 2026-07-11 a corrupt `font5x7.js` took the entire site down, which it never should.
