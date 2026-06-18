# Ship Wall — Hardware Bring-Up & Bench Test Runbook

Getting the register panel from bare board to a trustworthy, unattended display.
Work top to bottom; don't skip the order — each stage assumes the previous passed.

The deployment target is the register firmware (`register_esp32.ino`) driven over
USB serial by `register_service.py` on the Pi. This runbook covers the panel side.

---

## 0. What you need on the bench

- Adafruit MatrixPortal S3 (ESP32-S3).
- The 128×64 HUB75 panel (the 6484: 1/32 scan, 5-address / HUB75E).
- A **separate 5V supply for the panel** (4A+). Never power the panel from the
  board's USB — the 5V/GND screw terminals on the MatrixPortal feed the panel.
- USB-C cable from your computer (or the Pi) to the MatrixPortal.
- The repo checked out, with `register_esp32.ino` and `ship_sprites.h` together
  in the same sketch folder.

Bench order of operations: get a picture first (Stage 4), *then* wire it to the
Pi's live data (Stage 6). Don't try to debug HUB75 and serial at the same time.

---

## 1. Physical assembly

1. Power off everything.
2. Seat the MatrixPortal's 2×8 HUB75 connector onto the panel's HUB75 **input**
   (left side, arrows point away from input). The socket is 2×10 so it's easy to
   misalign by one row — line it up carefully.
3. Wire the panel's 5V/GND to the separate supply **and** to the MatrixPortal's
   5V/GND screw terminals (the board reads logic power from there).
4. Tie a panel GND pin to an ESP32 GND pin if you see flicker or artifacts later
   — a missing common ground is the usual cause.
5. USB-C from your computer to the MatrixPortal.
6. Power the panel supply first, then plug in USB.

---

## 2. Toolchain (one-time, on whatever machine flashes)

If you're flashing from your laptop for the bench, install locally. For remote
reflashing later, this same setup goes on the Pi (see `REMOTE_OPS.md`).

**arduino-cli install** (one-time):

```bash
# Linux / Pi:
curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh
sudo mv bin/arduino-cli /usr/local/bin/      # or add to PATH
```

On Windows, install arduino-cli via `winget install ArduinoSA.CLI` or download
the release zip and add it to PATH. The `arduino-cli` subcommands below are
identical on both platforms.

**Core + libraries** (same on every platform):

```bash
arduino-cli config init
arduino-cli core update-index
arduino-cli core install esp32:esp32         # ESP32-S3 support

arduino-cli lib install ArduinoJson                  # v7.x — the firmware uses the v7 API
arduino-cli lib install "Adafruit GFX Library"       # REQUIRED: text/print methods
arduino-cli lib install "ESP32 HUB75 LED MATRIX PANEL DMA Display"
```

The HUB75 library is Adafruit-GFX-compatible but does **not** bundle GFX — the
firmware's `setCursor`/`setTextColor`/`print`/`getTextBounds` calls come from
Adafruit GFX, so it must be installed separately (it pulls in Adafruit BusIO
automatically). The firmware's `#include` is
`<ESP32-HUB75-MatrixPanel-I2S-DMA.h>` — note the `I2S` in the filename.

Confirm the board ID:

```bash
arduino-cli board listall | grep -i matrixportal
# expect: Adafruit MatrixPortal ESP32-S3   esp32:esp32:adafruit_matrixportal_esp32s3
```

(On Windows PowerShell, replace `grep` with `Select-String matrixportal`.)

---

## 3. Flashing

**The CDCOnBoot flag matters** — the S3 uses native USB, and without it the
serial port can vanish after a flash, which strands you on the *next* reflash
(critical once the board is remote). Always flash with it set.

**Windows / PowerShell** (where you're flashing for the bench):

```powershell
cd C:\path\to\register_esp32        # folder with register_esp32.ino + ship_sprites.h
$FQBN = "esp32:esp32:adafruit_matrixportal_esp32s3:CDCOnBoot=cdc"

arduino-cli compile --fqbn $FQBN register_esp32.ino
arduino-cli upload  --fqbn $FQBN -p COM5 register_esp32.ino
```

**Linux / Pi** (for remote reflashing later):

```bash
cd path/to/register_esp32
FQBN="esp32:esp32:adafruit_matrixportal_esp32s3:CDCOnBoot=cdc"

arduino-cli compile --fqbn "$FQBN" register_esp32.ino
arduino-cli upload  --fqbn "$FQBN" -p /dev/ttyACM0 register_esp32.ino
```

Note the variable syntax differs: PowerShell is `$FQBN = "..."` referenced as
`$FQBN`; bash is `FQBN="..."` referenced as `"$FQBN"`. Find the port with
`arduino-cli board list` — Windows shows a `COM` port (COM5, etc.), Linux a
`/dev/ttyACM*`. On the S3, if upload can't find the board: double-tap reset to
force the ROM bootloader (NeoPixel turns purple), then upload — it may enumerate
as a different port.

**After upload:** the S3 often needs a reset press before the new sketch runs.
On the bench that's the reset button. Verify whether your core auto-resets
(recent esp32 cores usually do `--after hard_reset`) **before** the board goes
remote, because there's no button to press from Massachusetts.

---

## 4. First-light checks (panel, no Pi yet)

Power on with USB only (no Pi connected). The firmware boots and, having never
received a frame, should display its cold-boot screen.

### 4a. Is there a coherent image at all?

- [ ] Panel lights up; you see text, not noise/garbage/scramble.
- [ ] You can read **STARTING UP** centered on the panel.

If the image is **scrambled / shifted / doubled**, it's the HUB75 init, not your
code. In `register_esp32.ino` `setup()`, try in this order, reflashing between:
1. Uncomment `cfg.driver = HUB75_I2S_CFG::FM6126A;`
2. Flip `cfg.clkphase = false;` to `true`.
3. If rows look interleaved/half-height, the panel's scan rate or E-pin mapping
   is off — confirm it's a true 1/32-scan 64-high panel and the E pin is wired.

### 4b. Are colors right?

The "STARTING UP" text is dim gray, not a great color test. Better: let it sit
until the timeout, or jump to Stage 6 and check a sprite. RGB565 byte-order
problems show as swapped colors (red↔blue). The sprites use standard `0xRRRRR…`
packing, so if a known funnel renders with right colors, byte order is fine.

### 4c. Watch the boot serial log

```bash
arduino-cli monitor -p /dev/ttyACM0 -c baudrate=115200    # Windows: -p COM5
```

- [ ] No DMA allocation failure / no boot loop.
- [ ] If you see a memory/DMA alloc error: the framebuffer didn't fit. Confirm
      the HUB75 lib is using PSRAM, or lower color depth in `cfg`. (Sprites are
      flash, ~64 KB, not the problem; the DMA buffer is the RAM pressure.)

---

## 5. Cold-boot state behavior

With still no Pi attached, confirm the firmware tells the truth about its state:

- [ ] At power-on it shows **STARTING UP** (not "REGISTER CLEAR"). This is the
      fix that distinguishes "haven't heard from the Pi yet" from "register is
      empty." If you see REGISTER CLEAR with no Pi, the boot-state flag is wrong.

You can't test the **PI OFFLINE** screen yet — that one needs data to arrive and
then stop (Stage 6).

---

## 6. Live data (connect the Pi)

Now wire the MatrixPortal to the Pi over USB and start the service (or run
`register_service.py` with `ESP32_SERIAL=/dev/ttyACM0`). Frames are
newline-delimited JSON at 115200; see `FRAME_SCHEMA.md` for the contract.

Run through these in order:

- [ ] **Frame parses → live display.** Within a push interval (~10s) the panel
      leaves STARTING UP and shows the board / a vessel. If it stays on STARTING
      UP, the Pi isn't sending or the line isn't terminating in `\n`.
- [ ] **A sprite renders, correct colors.** Watch the detail view cycle. Pick a
      vessel you can identify (e.g. an Algoma laker) and confirm the funnel looks
      right — this is your real RGB565 color check.
- [ ] **The board view lists ships** with mini chips, codes, direction glyphs.
- [ ] **Direction glyphs make sense** — downbound vs upbound vs moored.
- [ ] **EPA / Lake Guardian** (if MMSI 338021074 is live or seeded): confirm it
      resolves to the `USESPA` funnel, not the UNKNOWN ghost. (This validates the
      whole resolver→sprite chain we converged.)
- [ ] **Emmett panel** (if a fresh Emmett fix is present): the WHERE'S EMMETT
      mode appears in the cycle.
- [ ] **PI OFFLINE screen.** Stop the service (or unplug the Pi's USB). After the
      data-timeout (~60s) the panel should switch from live to **PI OFFLINE** —
      not back to STARTING UP. Restart the service; it returns to live.

---

## 7. Power-on resilience (the unattended test)

This is the gate before you trust the box alone. Simulate the cottage
power-blip: the panel and Pi share power and reboot together; the panel comes up
in ~1s, the Pi takes 30–60s.

1. With everything running and live, **cut power to the whole bench at once**
   (the shared strip, not just one device).
2. Restore power. Watch the panel:
   - [ ] Panel boots within a second or two, shows **STARTING UP**.
   - [ ] It *stays* on STARTING UP (truthful) while the Pi boots — up to a
         minute is expected, not a bug.
   - [ ] When the Pi's service comes up and pushes a frame, the panel goes live
         on its own. **No human intervention.**
3. Repeat once more to be sure it's deterministic, not lucky.

If the panel goes live after a reboot with nobody touching it, the unattended
story holds. (The Pi side of this — `Restart=always`, `enable` — is covered in
`REMOTE_OPS.md`; verify that unit is installed so the service actually comes
back.)

---

## 8. Pre-departure checklist (before the box goes remote)

Don't leave until all of these are true with the board in hand:

- [ ] Flashed with `CDCOnBoot=cdc`.
- [ ] Verified the board auto-resets after upload (or you've added a
      network-switchable USB power port so you can power-cycle remotely).
- [ ] One clean Stage 7 power-cycle with no human help.
- [ ] `arduino-cli` installed **on the Pi** and a test reflash done over the
      Pi's USB link (see `REMOTE_OPS.md`) — proves you can fix firmware remotely.
- [ ] Tailscale up on the Pi with key expiry disabled.
- [ ] systemd `shipwall.service` enabled (survives reboot) and the monthly
      `shipwall-clean.timer` installed.

---

## Quick troubleshooting reference

| Symptom | Likely cause | Try |
|---|---|---|
| Scrambled/doubled image | HUB75 driver init | `FM6126A` driver, then `clkphase=true` |
| Colors swapped (R↔B) | RGB565 byte order | Confirm against a known funnel; rare with these sprites |
| Half-height / interleaved rows | scan rate / E pin | Confirm 1/32-scan 64-high panel, E pin wired |
| Boot loop / blank + serial error | DMA buffer didn't fit RAM | Use PSRAM for framebuffer, or lower color depth |
| Stuck on STARTING UP with Pi attached | no frame / no `\n` terminator | Check Pi is pushing; check serial port + baud |
| All funnels are the ghost | resolver keys not matching | Already converged; re-check `operators.py` / JSON if it recurs |
| Port vanishes after flash | S3 native USB, missing CDC flag | Reflash with `CDCOnBoot=cdc` |
| Flicker / sparkle | missing common ground | Tie panel GND to ESP32 GND |
| `No such file or directory: ESP32-HUB75-MatrixPanel-...` | wrong include name | Header is `ESP32-HUB75-MatrixPanel-I2S-DMA.h` (note `I2S`) |
| `expected unqualified-id before numeric constant` on a `const` | name collides with a system macro (e.g. `LINE_MAX` from limits.h) | Already renamed to `FRAME_BUF_MAX`; if it recurs, rename the offending constant |
| `DynamicJsonDocument is deprecated` / capacity warnings | ArduinoJson v7 installed | Firmware uses v7 `JsonDocument doc;` (no capacity arg) — already fixed |
| undefined reference to `setCursor`/`print`/`getTextBounds` | Adafruit GFX not installed | `arduino-cli lib install "Adafruit GFX Library"` |
| Library "disappears" mid-compile on Windows | OneDrive syncing the libraries folder | Retry; consider moving the Arduino libraries folder out of OneDrive |