# Ship Wall — Raspberry Pi Setup

Gets a Pi from a blank SD card to the exact state `REMOTE_OPS.md` assumes:
the service installed and running, panel on USB, AISStream connected. Work top
to bottom. When you finish §7, switch to `REMOTE_OPS.md` for systemd, Tailscale,
and remote reflash — this doc deliberately stops where that one begins.

The Pi's job: run `register_service.py`, which connects to AISStream over
WebSocket, tracks vessels, and pushes JSON frames down USB serial to the panel.
It is the always-on half of the wall; the MatrixPortal is just the display.

---

## 0. What you need

- A Raspberry Pi. **This guide is tuned for the Pi 3 Model B (v1.2)** you have —
  a capable but older board: 4×1.2GHz, 1GB RAM, 2.4GHz-only Wi-Fi. It runs this
  one always-on service comfortably; just expect slower warm-starts (see §7).
- microSD card (16 GB+), and a way to flash it.
- The Pi's own power supply (separate from the panel's 5V — do not back-feed).
- **Network: strongly prefer Ethernet.** The service is dead without network,
  and the 3B's 2.4GHz Wi-Fi at cottage range is the flakiest link in the whole
  system. If it must be Wi-Fi, put the Pi close to the access point.
- Your AISStream.io API key.
- The MatrixPortal, flashed and confirmed working (shows STARTING UP on its own).

---

## 1. Flash the OS

Use Raspberry Pi Imager (raspberrypi.com/software).

- OS: **Raspberry Pi OS Lite (64-bit)** — currently Debian 13 "Trixie", the
  current release as of 2026. Lite = no desktop; this is a headless appliance.
  The Pi 3B is 64-bit capable and is explicitly listed as compatible with the
  Trixie Lite 64-bit image.
  - **Gotcha on the Pi 3:** if you set the Imager's device filter to "Raspberry
    Pi 3," it may steer you to an older *Legacy* image. To get current Trixie
    64-bit Lite, set the device filter to **"No filtering"** (or choose the OS
    manually under "Raspberry Pi OS (other)"), then pick **Raspberry Pi OS Lite
    (64-bit)**.
  - The Trixie-on-older-boards hiccups reported online were all about *desktop*
    environments; you're running Lite (no desktop), so none of that applies.
    Headless Trixie runs smoothly on a 3B.
  - 32-bit Lite is an acceptable alternative on this 1GB board and is the
    no-fuss pick if you'd rather not touch the filter — no performance
    difference for this workload. The service is 64-bit-clean either way.
- Click the gear / "Edit Settings" before writing and set:
  - **Hostname**: e.g. `shipwall` (so it's `shipwall.local` on the LAN).
  - **Username**: `grims` — match this to the service unit, which runs as
    `User=grims`. If you pick a different name you must edit `shipwall.service`.
  - **Password**: set one.
  - **Wi-Fi**: SSID + password + country, if not on Ethernet. (Country is
    required for the radio to enable. 2.4GHz network only on this board.)
  - **Enable SSH**: yes, password auth (or paste a public key).

Write the card, boot the Pi, give it a minute (first boot is slower on a 3B).

---

## 2. First login

From your computer:

```bash
ssh grims@shipwall.local
```

(If `.local` doesn't resolve, find the Pi's IP from your router and
`ssh grims@192.168.x.y`.)

Update the system once:

```bash
sudo apt update && sudo apt full-upgrade -y
sudo reboot
```

Reconnect after it reboots.

---

## 3. System packages

```bash
sudo apt install -y python3 python3-pip python3-venv git
```

Python 3 ships with the OS; this just guarantees pip, venv, and git are present.

---

## 4. Get the code onto the Pi

The repo is at github.com/bsullgrim/shipwall. Clone it to the path the service
expects — `/home/grims/shipwall`:

```bash
cd ~
git clone https://github.com/bsullgrim/shipwall.git
cd shipwall
```

If the repo is private, either make a personal access token or, simplest for a
one-off, `scp` the files from your Windows box:

```bash
# from Windows PowerShell, in C:\Users\grims\Projects\Python\ShipWall\:
scp -r * grims@shipwall.local:/home/grims/shipwall/
```

Either way you want `register_service.py`, `operators.py`, `schedule.py`, the
JSON data files (`mmsi_db.json`, `ship_to_operator.json`, etc.), and the CSVs
(or let the service create fresh ones) sitting in `/home/grims/shipwall`.

> **Don't commit the runtime data files.** `register.csv`, `passages.csv`,
> `mmsi_database.json`, and `unknown_vessels.json` are gitignored on purpose —
> the service rewrites them live. If a clone or scp leaves them tracked, a later
> `git pull` can write merge-conflict markers into a file the service reads and
> break it silently. Confirm `git status` doesn't list them as tracked; if it
> does, `git rm --cached <file>`.

---

## 5. Python dependencies

The service imports `aiohttp`, `websockets`, and `pyserial` (plus stdlib).
Trixie (like Bookworm) marks the system Python as "externally managed," so use a
virtual environment — it's cleaner and avoids `--break-system-packages`:

```bash
cd ~/shipwall
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install aiohttp websockets pyserial
```
(Add `pillow` to that install line only if you'll build sprites on the Pi;
the service itself doesn't need it.)

This puts a Python with the deps at `~/shipwall/.venv/bin/python3`. Note that
path — the service unit's `ExecStart` must point at it (see §7).

(If you'd rather not use a venv: `pip install --break-system-packages aiohttp
websockets pyserial` installs system-wide and lets `ExecStart` stay
`/usr/bin/python3`. The venv is the tidier choice for an appliance.)

---

## 6. Serial port — find the panel and grant access

Plug the MatrixPortal into the Pi via USB. Then:

```bash
ls /dev/ttyACM*        # expect /dev/ttyACM0 (the panel)
```

If you see `/dev/ttyACM0`, that matches the service default. If it's a different
number, note it for the env config.

Add `grims` to the `dialout` group so the service can open the port without
root, then re-login for it to take effect:

```bash
sudo usermod -aG dialout grims
# log out and back in (or reboot) for the group to apply
exit
```

Reconnect with `ssh grims@shipwall.local`.

> The S3's port can change number across reboots/reflashes (native USB
> re-enumerates). If it ever comes up as `ttyACM1`, either update
> `ESP32_SERIAL` or — better, later — pin it with a udev rule so it's always the
> same name. Not needed to get started.

---

### 6.5 Clock and logs (do this before you leave it unattended)

The Pi 3 has no real-time clock. Without help it boots with a wrong clock until
NTP syncs, which blocks Tailscale for a few minutes after every reboot. Install
`fake-hwclock` so it restores the last-known time instantly:

```bash
sudo apt install -y fake-hwclock
sudo fake-hwclock save
systemctl is-enabled fake-hwclock-load.service   # expect: enabled
```

(On Trixie the bare `fake-hwclock.service` is masked — expected. The active
units are `fake-hwclock-load/-save/-save.timer`, auto-enabled by the install.)

Enable persistent journald so the *previous* boot's logs survive a reboot —
essential for diagnosing why an unattended Pi rebooted:

```bash
sudo mkdir -p /var/log/journal
sudo systemd-tmpfiles --create --prefix /var/log/journal
sudo systemctl restart systemd-journald
```
---
### 6.6 Persistent journald logging (survives reboots)

**Problem:** Raspberry Pi OS ships `/usr/lib/systemd/journald.conf.d/40-rpi-volatile-storage.conf`
with `Storage=volatile` to spare the SD card. This **silently overrides** `Storage=persistent`
set in the main `/etc/systemd/journald.conf` — drop-ins in `journald.conf.d/` always win over
the main file. Symptom: `journalctl --list-boots` only ever shows boot 0; `journalctl --header`
reports `/run/log/journal/...` (tmpfs) instead of `/var/log/journal/...`. Logs are lost on every
reboot, so you can't diagnose anything that happened before the last boot.

**Do NOT** edit `40-rpi-volatile-storage.conf` directly — a package update reverts it.

**Fix:** add a higher-sort-order drop-in in `/etc` (wins by both sort order and /etc > /usr/lib):

    sudo mkdir -p /etc/systemd/journald.conf.d
    sudo tee /etc/systemd/journald.conf.d/99-persistent.conf >/dev/null <<'EOF'
    [Journal]
    Storage=persistent
    SystemMaxUse=200M
    SystemMaxFileSize=50M
    EOF
    sudo systemctl restart systemd-journald
    sudo journalctl --flush

**Verify:**

    # persistent must appear LAST (last value wins):
    sudo systemd-analyze cat-config systemd/journald.conf | grep -i Storage
    # header must show /var/log, not /run/log:
    journalctl --header | grep "File path" | head -1
    # after a reboot, a boot -1 must appear:
    journalctl --list-boots

The `SystemMaxUse`/`SystemMaxFileSize` caps bound SD-card write wear — this is the tradeoff
the volatile default was avoiding, so keep them when enabling persistence.

**Note:** `getfacl`/`setfacl` aren't installed by default (`sudo apt-get install -y acl`).
Standard Debian ACL on `/var/log/journal` (`group:adm:r-x`) is normal and does NOT block
journald — don't chase it.

### 6.7 Benign boot-time "time jump" / "slept Ns" messages (with fake-hwclock, no RTC)

The Pi 3B has no RTC. On boot the clock starts at the saved fake-hwclock value, then NTP steps
it to true time. This produces harmless log noise that is NOT a real sleep or power event:

- `systemd-journald: Time jumped backwards, rotating` — journald reacting to the NTP correction.
- `tailscaled: monitor: time jump detected (slept 36s), probably wake from sleep` — tailscaled
  mislabels the NTP step as a "sleep." A 30–40s value = normal clock-settling window.

**A real event looks different:** the 2026-06-21 outage was `slept 43m23s` — minutes, not seconds,
and mid-run rather than at boot. Scale and timing distinguish benign settling from a genuine
power/hang event. Cross-check with `vcgencmd get_throttled` (sticky bit 16 = under-voltage since
boot) — `0x0` means no brownout was recorded.
--- 
## 7. Configure and smoke-test by hand

Before handing it to systemd, run it once in the foreground so you can see it
work and read any errors directly.

Create a `.env` in `~/shipwall` (the service reads it — see the `os.environ`
loader near the top of `register_service.py`):

```bash
cd ~/shipwall
cat > .env <<'ENV'
AISSTREAM_KEY=your_real_key_here
REGISTER_HOURS=18
REGISTER_LOG=/home/grims/shipwall/register.csv
PASSAGE_LOG=/home/grims/shipwall/passages.csv
ESP32_SERIAL=/dev/ttyACM0
EMMETT_MMSI=338021074
ENV
chmod 600 .env       # the key is in here; keep it private
```

Run it in the foreground:

```bash
.venv/bin/python3 register_service.py
```

What you should see, and what it means:

- `[serial] open /dev/ttyACM0 @ 115200` — the panel link is up. **The panel
  should leave STARTING UP and begin showing frames within a few seconds.**
- AISStream connect messages, then `[push] N ships -> serial ...` lines as
  vessels come in.
- `[mmsidb] loaded ... known vessels` — your identity DB loaded.

If it errors:

- `ERROR: set AISSTREAM_KEY` — the `.env` isn't being read or the key line is
  wrong. Confirm `.env` is in the working directory and the key has no quotes.
- `could not open /dev/ttyACM0` — either the group change hasn't taken effect
  (did you re-login?), the panel isn't plugged in, or it's on a different
  ttyACM number.
- WebSocket/connection errors — check the Pi has internet
  (`ping -c1 aisstream.io`) and the key is valid.

Let it run a minute and watch the panel update with live traffic. When you're
satisfied, `Ctrl+C` to stop.

> **Pi 3B timing note:** on this board, cold boot → live panel can take 60–90s
> (it's reading the register CSV and the MMSI DB, then connecting to AISStream,
> all on a 1.2GHz core). That's normal. The panel sits on STARTING UP the whole
> time and won't falsely show PI OFFLINE during a cold boot — the firmware only
> shows PI OFFLINE after it has seen at least one frame and then lost the feed.
> Also: `pip install` in §5 compiles a couple of wheels and can take several
> minutes on a 3B — let it run, it isn't hung.

---

## 8. Hand off to systemd → `REMOTE_OPS.md`

The Pi is now in the state `REMOTE_OPS.md` assumes. Go there for:

- installing `shipwall.service` so it runs on boot and restarts on crash —
  **edit `ExecStart` to your venv python**:
  `ExecStart=/home/grims/shipwall/.venv/bin/python3 /home/grims/shipwall/register_service.py`
  (and confirm `User=grims`, `WorkingDirectory=/home/grims/shipwall`, and the
  `Environment=` lines match — or rely on the `.env` you just made and trim the
  unit's env lines to taste);
- the monthly `register.csv` compaction timer;
- Tailscale, so you can reach the Pi from Massachusetts (remember: disable key
  expiry, or you lose access in ~6 months);
- `arduino-cli` on the Pi for remote panel reflashing over this same USB link.

Once `shipwall.service` is enabled, the whole wall is power-cycle resilient:
lose power, the Pi reboots, systemd restarts the service, the panel goes from
STARTING UP to live on its own.

---

## Quick reference — the state this doc leaves you in

| Thing | Value |
|---|---|
| User | `grims` |
| Repo path | `/home/grims/shipwall` |
| Python | `/home/grims/shipwall/.venv/bin/python3` |
| Config | `/home/grims/shipwall/.env` (chmod 600) |
| Panel serial | `/dev/ttyACM0`, `grims` in `dialout` |
| Verified | service runs in foreground, panel shows live frames |
| Next | `REMOTE_OPS.md` §1 (systemd) |
