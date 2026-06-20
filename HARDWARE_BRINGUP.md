# Ship Wall — Hardware Bring-Up & Bench Test Runbook

Getting the register panel from bare board to a trustworthy, unattended display.
Work top to bottom; don't skip the order — each stage assumes the previous passed.

The deployment target is the register firmware (`register_esp32.ino`) driven over
USB serial by `register_service.py` on the Pi. This runbook covers the panel side.

---

## 0. What you need on the bench

- Adafruit MatrixPortal S3 (ESP32-S3).
- The 128×64 HUB75 panel (the 6484: 1/32 scan, **5-address / ABCDE**, HUB75E).
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
2. Seat the MatrixPortal's HUB75 connector onto the panel's HUB75 **input**
   (the J-IN side; arrows point away from input). Line it up carefully — it's
   easy to misalign by one row.
3. Wire the panel's 5V/GND to the separate supply **and** to the MatrixPortal's
   5V/GND screw terminals (the board reads logic power from there).
4. Tie a panel GND pin to an ESP32 GND pin so the HUB75 signals share a
   reference — a missing common ground is the usual cause of flicker/artifacts.
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
arduino-cli lib install "Adafruit Protomatter"       # the HUB75 driver for the 6484
```

**Why Protomatter, not the DMA library.** The 6484 panel uses **5-address
(ABCDE) multiplexing**. The `ESP32-HUB75-MatrixPanel-I2S-DMA` library defaults
to 4-address (A–D) addressing and leaves this panel **completely dark** — not
scrambled, dark — because half the scan rows are never addressed. Adafruit
**Protomatter** is the library Adafruit validates for the 6484 + MatrixPortal S3
combo and drives all five address pins. Use Protomatter; the DMA library is the
single biggest dead end in bringing this panel up.

Protomatter pulls in **Adafruit GFX** for text (`setCursor`/`setTextColor`/
`print`/`getTextBounds`) — install GFX separately as shown above; it brings
Adafruit BusIO with it. The firmware's include is `<Adafruit_Protomatter.h>`.

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
received a frame, should display its cold-boot screen. Note that nothing renders
until the firmware calls `matrix.show()` — it does this once per frame, and once
for the splash.

### 4a. Is there a coherent image at all?

- [ ] Panel lights up; you see text, not noise/garbage/scramble.
- [ ] You can read **STARTING UP** centered on the panel.

If the panel is **completely dark** (not scrambled — dark), the most likely
cause is address-pin count: the 6484 needs **5 address pins (A–E)**. Confirm the
`Adafruit_Protomatter` constructor in `register_esp32.ino` is built with all
five address pins for this 1/32-scan panel. A 4-address configuration addresses
only half the rows and the panel stays dark. This is the first thing to check.

If the image is **scrambled / shifted / doubled** (as opposed to dark), recheck
the ribbon seating on J-IN and confirm the constructor's width (128), height
(64), and pin assignments match the MatrixPortal S3 mapping.

### 4b. Are colors right?

The "STARTING UP" text is dim gray, not a great color test. Better: jump to
Stage 6 and check a known sprite. **This panel batch has red and blue swapped**
relative to the Adafruit reference pinout — if reds render as blue (greens and
whites correct), that's the known per-panel variance, not a data bug. The
firmware's `rgbPins[]` already swaps them for our unit
(`{40,41,42, 37,39,38}` rather than the stock `{42,41,40, 38,39,37}`). Confirm
with a pure-R/G/B/white swatch if unsure; flip back to stock order only if your
panel renders correctly with it.

### 4c. Watch the boot serial log

```bash
arduino-cli monitor -p /dev/ttyACM0 -c baudrate=115200    # Windows: -p COM5
```

- [ ] No allocation failure / no boot loop.
- [ ] If you see a memory alloc error: the Protomatter framebuffer didn't fit.
      The MatrixPortal S3 has PSRAM; confirm the build is using it. (Sprites are
      in flash PROGMEM, ~64 KB — not the RAM pressure; the framebuffer is.)

---

## 5. Cold-boot state behavior

With still no Pi attached, confirm the firmware tells the truth about its state:

- [ ] At power-on it shows **STARTING UP** (not "REGISTER CLEAR" / "WAITING").
      This distinguishes "haven't heard from the Pi yet" from "register is
      empty." If you see a CLEAR/WAITING screen with no Pi ever attached, the
      boot-state flag is wrong.

You can't test the **WAITING / for data** screen yet — that one needs data to
arrive and then stop for 60 s (Stage 6).

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
      right — this is your real color check, and where the R↔B swap (4b) shows up
      if the `rgbPins` order is wrong for your panel.
- [ ] **The board view lists ships** with mini chips, codes, direction glyphs.
- [ ] **Direction glyphs make sense** — downbound vs upbound vs moored.
- [ ] **EPA / Lake Guardian** (if MMSI 338021074 is live or seeded): confirm it
      resolves to the `USESPA` funnel, not the UNKNOWN ghost. (This validates the
      whole resolver→sprite chain.)
- [ ] **Emmett panel** (if a fresh Emmett fix is present): the WHERE'S EMMETT
      mode appears in the cycle.
- [ ] **WAITING screen.** Stop the service (or unplug the Pi's USB). After the
      data-timeout (~60s) the panel should switch from live to **WAITING / for
      data** — not back to STARTING UP. Restart the service; it returns to live.

---

## 7. Power-on resilience (the unattended test)

This is the gate before you trust the box alone. Simulate the cottage
power-blip: the panel and Pi share power and reboot together; the panel comes up
in ~1s, the Pi takes 30–90s.

1. With everything running and live, **cut power to the whole bench at once**
   (the shared strip, not just one device).
2. Restore power. Watch the panel:
   - [ ] Panel boots within a second or two, shows **STARTING UP**.
   - [ ] It *stays* on STARTING UP (truthful) while the Pi boots — up to a
         minute or so is expected, not a bug.
   - [ ] When the Pi's service comes up and pushes a frame, the panel goes live
         on its own. **No human intervention.**
3. Repeat once more to be sure it's deterministic, not lucky.

If the panel goes live after a reboot with nobody touching it, the unattended
story holds. (The Pi side of this — `Restart=always`, `enable`, plus
`fake-hwclock` so the clock is sane at boot and Tailscale comes back fast — is
covered in `REMOTE_OPS.md`; verify those are installed so the service actually
returns.)

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
- [ ] `fake-hwclock` installed (so the Pi isn't unreachable for minutes after a
      reboot while its clock syncs — see `REMOTE_OPS.md`).
- [ ] systemd `shipwall.service` enabled (survives reboot) and the monthly
      `shipwall-clean.timer` installed.

---

## Quick troubleshooting reference

| Symptom | Likely cause | Try |
|---|---|---|
| **Panel completely dark** (not scrambled) | 4-address config on a 5-address panel | Confirm Protomatter constructor drives **5 address pins (A–E)**; the DMA library defaults to 4 and leaves it dark |
| Scrambled/doubled/shifted image | ribbon seating or constructor geometry | Reseat on J-IN; confirm width 128 / height 64 / S3 pin map |
| Colors swapped (R↔B) | this panel batch has R/B swapped | `rgbPins` already swapped to `{40,41,42, 37,39,38}`; confirm with R/G/B/W swatch |
| Boot loop / blank + serial alloc error | framebuffer didn't fit RAM | Confirm Protomatter is using PSRAM on the S3 |
| Stuck on STARTING UP with Pi attached | no frame / no `\n` terminator | Check Pi is pushing; check serial port + baud (115200) |
| All funnels are the ghost | resolver keys not matching sprite keys | Re-check `operators.py` / JSON spellings against `ship_sprites.h` |
| Port vanishes after flash | S3 native USB, missing CDC flag | Reflash with `CDCOnBoot=cdc` |
| Flicker / sparkle | missing common ground | Tie panel GND to ESP32 GND |
| `No such file: Adafruit_Protomatter.h` | library not installed | `arduino-cli lib install "Adafruit Protomatter"` |
| undefined reference to `setCursor`/`print`/`getTextBounds` | Adafruit GFX not installed | `arduino-cli lib install "Adafruit GFX Library"` |
| `DynamicJsonDocument is deprecated` / capacity warnings | ArduinoJson v7 installed | Firmware uses v7 `JsonDocument doc;` (no capacity arg) — already fixed |
| `expected unqualified-id before numeric constant` on a `const` | name collides with a system macro (e.g. `LINE_MAX` from limits.h) | Already renamed to `FRAME_BUF_MAX`; rename any new offender |
| Library "disappears" mid-compile on Windows | OneDrive syncing the libraries folder | Retry; move the Arduino libraries folder out of OneDrive |