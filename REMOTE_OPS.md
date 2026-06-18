# Ship Wall -- Unattended & Remote Operations

Running the wall at Danger Island while you're in Massachusetts. Three layers:
keep it alive (systemd), keep it tame (monthly compaction), reach it (Tailscale),
and reflash it without being there (arduino-cli over the Pi's USB link).

## 1. Keep it alive -- systemd service

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

NOTE: with the flicker-resistant `log_vessel` change also applied, the file
barely grows in the first place; the monthly clean becomes belt-and-suspenders
rather than essential. Keep it anyway -- it also re-resolves operators through
your latest rules.

## 3. Reach it -- Tailscale

Yes, MA <-> Danger Island works: Tailscale builds an encrypted WireGuard tunnel
over the public internet, so distance and NAT are irrelevant. The only
requirement is that the Pi has working OUTBOUND internet at the cottage
(Starlink / cellular / DSL -- whatever's there must be up).

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

Everything in this doc -- editing the JSON mapping files, running
operator_worklist.py / heal_passage_names.py / seed_mmsi_db.py, restarting the
service, pulling logs -- you do over this SSH session.

## 4. Reflash the panel remotely -- arduino-cli on the Pi

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
arduino-cli lib install "Adafruit Protomatter" # or the HUB75 DMA lib you use
```

To reflash after editing firmware (over SSH):

```bash
cd /home/grims/shipwall/firmware
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

**The remote-reset trap:** the S3 normally needs a physical reset-button press
after upload before the new sketch runs -- impossible from Massachusetts. Two
ways around it:
- Add `--upload-field reset=true` isn't reliable on the S3; instead rely on
  esptool's `--after hard_reset` (arduino-cli's esp32 upload does this by
  default in recent cores -- verify with a test flash while you're physically
  there, BEFORE you leave).
- Failing that, a remotely-switchable USB power port (a smart USB hub) lets you
  power-cycle the board over the network, which also resets it. Cheap insurance
  for an unattended install.

Confirm the exact board ID with `arduino-cli board listall | grep -i matrixportal`.
The MatrixPortal S3 sometimes enumerates as a different ACM device in bootloader
mode -- `ls /dev/ttyACM*` before and after to see which is which.

This is the single most valuable thing to set up before you leave: it's the
difference between "I can add a funnel from my laptop" and "I have to drive three
hours to fix a typo."

## Quick remote-health checklist

```bash
systemctl is-active shipwall            # should say 'active'
journalctl -u shipwall -n 30            # recent frames pushing?
systemctl list-timers shipwall-clean    # compaction scheduled?
ls -lh /home/grims/shipwall/*.csv       # file sizes sane?
tail -5 /home/grims/shipwall/passages.csv   # crossings still logging?
```
