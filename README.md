# St. Lawrence Ship Wall

A FlightWall-style live display for ship traffic on the upper St. Lawrence
Seaway, watching the American Narrows from **Chippewa Bay to Oak Point**.

Pieces:

- `shipwall_service.py` — runs on a Raspberry Pi / always-on box. Subscribes
  to AISStream, filters to the reach, identifies operators, and pushes frames.
- `operators.py` + `mmsi_to_operator.json` — map each vessel to an operator
  (and thus a funnel sprite), mostly by name prefix.
- `schedule.py` — sun-based brightness + Seaway-season logic.
- `make_sprites.py` → `ship_sprites.h` — the funnel-livery sprites.
- `shipwall_esp32.ino` — runs on the ESP32 / MatrixPortal, drives the panel.

---

## Parts list (~$110–135)

Recommended build — **two 64×64 panels + MatrixPortal S3**:

| Part | Notes | Approx |
|------|-------|--------|
| 2× 64×64 HUB75E panel, P3 or P4 | Chained to 128×64; HUB75E (E line) required | $50–70 |
| Adafruit MatrixPortal S3 | ESP32-S3 + HUB75 connector + level shifter + PSRAM | $25 |
| 5V / 8A power supply | Two 64×64 panels at full white pull ~6–7 A | $15 |
| Panel-to-panel ribbon | Included with panels | incl. |
| Raspberry Pi (any, incl. Zero 2 W) | Or reuse an always-on box | own? |

The MatrixPortal S3 is strongly recommended over a bare ESP32 at 128×64: it
has the HUB75 connector and 5V level shifter built in (plug the ribbon
straight in — no jumpers, no sparkle/ghosting), and its PSRAM gives full color
and stable 24/7 operation at this resolution. A plain ESP32 works but forces
8-bit color and sits near its memory ceiling.

A genuine **monolithic 128×64 (P2.5)** single panel also exists and is a
drop-in alternative to the two chained 64×64s (no seam, one PCB) — source it
from AliExpress / specialist LED suppliers rather than Western hobby shops.


---

## Wiring

**With a MatrixPortal S3 (recommended):** there is no signal wiring. Plug
panel 1's HUB75 ribbon into the board's connector, chain panel 1 OUT → panel 2
IN, and wire each panel's 5V/GND leads to the supply. In firmware leave
`USE_DEFAULT_PINS true`.

**With a classic ESP32 (hand-wired):** set `USE_DEFAULT_PINS false` in the
firmware and wire per the DMA library defaults below.

```
R1  -> 25      G1  -> 26      B1  -> 27
R2  -> 14      G2  -> 12      B2  -> 13
A   -> 23      B   -> 19      C   -> 5
D   -> 17      E   -> 18
CLK -> 16      LAT -> 4       OE  -> 15
GND -> GND (common ground with the 5V supply!)
```

Power the panels from the 5V/4A supply directly. Tie the supply ground to the
ESP32 ground. The ESP32 itself is powered over USB.

---

## Pi-side setup

1. Get a free API key at https://aisstream.io (sign up, create a key).
2. Install deps:
   ```bash
   pip install websockets aiohttp
   ```
3. Run, pointing at your ESP32's IP (from the serial monitor after flashing):
   ```bash
   AISSTREAM_KEY=your_key_here ESP32_HOST=192.168.1.50 python3 shipwall_service.py
   ```
4. To run on boot, drop it in a systemd unit (see bottom).

The bounding box is hard-coded near the top of the script:
```python
BOUNDING_BOX = [[[44.42, -75.82], [44.58, -75.55]]]
```
Adjust the corners to widen/narrow the reach.

---

## ESP32-side setup

1. Arduino IDE → Boards Manager → install **esp32** by Espressif.
2. Library Manager → install:
   - `ESP32-HUB75-MatrixPanel-DMA`
   - `ArduinoJson`
3. Edit `WIFI_SSID` / `WIFI_PASS` at the top of `shipwall_esp32.ino`
   (must be a **2.4 GHz** network — the ESP32 has no 5 GHz radio).
4. Flash. Open the serial monitor at 115200 baud to read the assigned IP.
5. Put that IP into `ESP32_HOST` when you start the Pi service.

---

## What it shows

Up to 5 vessels, cycling one every 5 seconds:

```
CSL WELLAND
CARGO 11.4kt
CRS 62°  DRFT 8.2m
```

Moving vessels are prioritized over moored/anchored ones, so the wall mostly
shows ships actually transiting the Narrows.

---

## Stack livery sprites

The display shows each operator's funnel livery as a 16×16 pixel sprite on the
right edge, next to the text. The pathway:

```
AIS name/MMSI ──> operators.py ──> operator key ──> frame ──> ESP32 ──> sprite
```

**Identifying the operator.** `operators.py` maps a vessel to an operator key
two ways: an explicit `mmsi_to_operator.json` lookup, then vessel-name prefix
rules (Algoma ships start "ALGO", CSL uses "CSL"/"BAIE", Fednav "FEDERAL",
etc.). On the Seaway this prefix matching catches most traffic with no manual
table. Anything unmatched returns `UNKNOWN` and is appended to
`unknown_vessels.json` so you can classify it later — add the MMSI to
`mmsi_to_operator.json` or add a prefix rule.

**The sprites themselves.** `make_sprites.py` defines each livery in code and
exports two files:
- `sprites_preview.png` — scaled-up visual check
- `ship_sprites.h` — RGB565 PROGMEM arrays the firmware `#include`s

To add or refine a livery: edit/​add a `spr_*()` function, add it to the
`SPRITES` dict with a key matching the operator key in `operators.py`, then:
```bash
pip install pillow
python3 make_sprites.py
```
and re-flash the ESP32. The supplied liveries are **stylised** for readability
at 16×16, not exact reproductions — refine them against funnel reference photos
to taste.

Operator keys currently defined: `CSL`, `ALGOMA`, `FEDNAV`, `LOWERLAKES`,
`ASC`, `INTERLAKE`, `OGLEBAY`, `UNKNOWN`.

---

## Testing without hardware

You can validate the whole system before any panel arrives. `mock_panel.py`
impersonates the ESP32: it exposes the same `POST /frame` endpoint and draws
each frame in a browser at http://localhost:8080, reading the real
`ship_sprites.h` so the preview matches what the panel will show.

**Option A — live AIS, no hardware:**
```bash
# terminal 1
python3 mock_panel.py                       # open http://localhost:8080

# terminal 2 -- real service, pointed at the mock instead of an ESP32
AISSTREAM_KEY=your_key ESP32_HOST=localhost:8080 python3 shipwall_service.py
```
Watch live St. Lawrence traffic render. (In the winter closure, or when the
reach is quiet, you'll see the idle/closed screens instead of vessels — that's
correct.)

**Option B — no AIS key either:** `simulate.py` feeds synthetic Seaway traffic
through the mock, cycling every display state (0 → 1 → 2 → 4 vessels → winter
closed) so you can see the adaptive layout, pair cycling, sprites, and
brightness work end to end:
```bash
python3 mock_panel.py        # terminal 1
python3 simulate.py          # terminal 2
```

When the hardware arrives, nothing about the Pi side changes — you just point
`ESP32_HOST` at the panel's real IP instead of `localhost:8080`.

---



**Adaptive layout.** One vessel on the river → a single large card (big text,
full funnel, type/speed/course/draught/destination). Two or more → a
two-vessel split (top and bottom halves), cycling pairs every 6 s if more than
two are present. The panel switches automatically as traffic changes.

**Brightness (sun-based, no extra hardware).** `schedule.py` computes sunrise
and sunset for the river reach and ramps brightness over a 30-minute window at
dawn and dusk — bright by day, a faint ~11% glow overnight. It tracks the
seasons automatically. Tune `BRIGHT_DAY` / `BRIGHT_NIGHT` in `schedule.py`.
The Pi stamps the target into every frame; the ESP32 just obeys, so there's no
clock or NTP code on-device.

**Idle / status screens.** The panel is never blank or stale:
- *Splash* on boot while WiFi connects.
- *READY + IP* once connected (note the IP for `ESP32_HOST`).
- *"ST LAWRENCE / no vessels"* in season when nothing's on the water.
- *"SEAWAY CLOSED / reopens March"* during the winter closure (auto). A ship
  appearing in that window is still shown — the closure screen only displays
  when the reach is also empty.
- *"WAITING / for data"* if the Pi stops pushing for 60 s, so a crash or drop
  is obvious rather than freezing on a stale ship.

**Power behavior.** The ESP32 holds no important state — every frame carries
vessels, brightness, and season. Pull power and restore it and the wall shows
live traffic within seconds. Run the Pi service under systemd (below) so both
ends auto-start on boot.

---

## Run the Pi service on boot (systemd)

`/etc/systemd/system/shipwall.service`:
```ini
[Unit]
Description=St. Lawrence Ship Wall
After=network-online.target
Wants=network-online.target

[Service]
Environment=AISSTREAM_KEY=your_key_here
Environment=ESP32_HOST=192.168.1.50
ExecStart=/usr/bin/python3 /home/pi/shipwall/shipwall_service.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable --now shipwall
```

---

## Tuning ideas

- **Brightness schedule:** the DMA lib supports `setBrightness8()`; gate it on
  time-of-day pushed from the Pi for a dim night mode.
- **Filter by ship type:** only show CARGO/TANKER (the big salties and lakers)
  by filtering in `build_frame()`.
- **Seaway flavor:** the upper river is seasonal — the Seaway closes in winter.
  A "SEAWAY CLOSED" idle screen for Jan–Mar is a nice touch.
- **Track-a-ship mode:** pin one MMSI and always show it when in box.
