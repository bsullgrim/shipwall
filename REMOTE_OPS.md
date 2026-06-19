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

## 6. Maintenance reference -- the things that actually come up

### Quick remote-health checklist

```bash
systemctl is-active shipwall shipwall-stats   # both 'active'?
journalctl -u shipwall -n 30 --no-pager       # recent [push] frames?
systemctl list-timers shipwall-clean          # compaction scheduled?
ls -lh /home/grims/shipwall/*.csv             # file sizes sane?
tail -5 /home/grims/shipwall/passages.csv     # crossings still logging?
df -h /                                        # SD card not full?
```

### "The panel says STARTING UP / WAITING / nothing"

- **STARTING UP** = panel up, Pi hasn't sent a frame yet. Normal for 30-90s
  after a shared-power reboot (the Pi 3 is slow to boot + connect). If it sticks:
  `systemctl is-active shipwall` (is the service even running?), then
  `journalctl -u shipwall -n 30` for `[serial] open` and `[push]` lines.
- **WAITING for data** = the panel HAD frames and then lost them for 60s. The
  service died or the USB link dropped. `systemctl status shipwall`; check
  `dmesg | tail` for USB disconnect/re-enumeration.
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