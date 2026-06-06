# St. Lawrence Ship Wall

A FlightWall-style LED display for ship traffic on the upper St. Lawrence
Seaway, watching the reach from **Cape Vincent down to the Eisenhower Lock**
near Massena. A Python service subscribes to live AIS, identifies each vessel's
operator (and thus its funnel livery), and pushes compact frames to an
ESP32-driven 128×64 LED matrix. The ESP32 is a stateless renderer; all the
logic lives on the Pi side.

## Two display variants

The project ships **two interchangeable front-ends** that share the same
sprites, operator identification, and brightness schedule. Pick whichever
suits your AIS coverage:

- **Live** (`shipwall_*`) — a real-time radar of the American Narrows: detailed
  cards for ships *in the reach right now*, plus a roster of the wider Seaway.
  Best when AIS coverage of the Narrows is good.
- **Register** (`register_*`) — a rolling register of every ship seen in the
  last ~18 h, deduped to its most recent sighting and stamped "seen Xh ago".
  Best when coverage is spotty (the live view is empty most of the time, but a
  multi-hour register is almost always populated): glance out the window, see a
  laker, then check the wall to learn who it was and where it was bound.

Both render on the same hardware and reuse `ship_sprites.h`, `operators.py`,
`schedule.py`, and `font5x7.js`.

---

## File map

Shared:
- `operators.py` + `mmsi_to_operator.json` — map each vessel to an operator key
  (and thus a funnel sprite) by MMSI lookup then vessel-name rules.
- `schedule.py` — sun-based brightness + Seaway winter-season logic.
- `ship_sprites.h` — generated 32×32 RGB565 funnel-livery sprites.
- `photo_to_sprite.py` + `funnels/` — build sprites from funnel photos.
- `suggest_palette.py` — sample a funnel image's dominant colors for a palette.
- `gallery.py` → `gallery.png` — labeled preview of all sprites.
- `font5x7.js` — 5×7 bitmap font shared by the mock panels.

Live variant:
- `shipwall_service.py` — AIS subscriber / frame publisher (two-zone frame).
- `shipwall_esp32.ino` — firmware renderer.
- `mock_panel.py` — browser stand-in for the panel (no hardware needed).
- `simulate.py` — synthetic traffic for the mock, no AIS key needed.

Register variant:
- `register_service.py` — AIS subscriber keeping an N-hour sightings register.
- `register_panel.py` — browser stand-in with BOARD + DETAIL modes.
- `register_simulate.py` — synthetic register for the panel, no AIS key needed.
- `register_esp32.ino` — firmware renderer *(planned; mock is the reference)*.

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

The MatrixPortal S3 is strongly recommended over a bare ESP32 at 128×64: it has
the HUB75 connector and 5V level shifter built in (plug the ribbon straight in
— no jumpers, no sparkle/ghosting), and its PSRAM gives full color and stable
24/7 operation. A plain ESP32 works but forces 8-bit color and sits near its
memory ceiling. A monolithic **128×64 (P2.5)** single panel is a drop-in
alternative to the two chained 64×64s (no seam) — source from AliExpress /
specialist LED suppliers.

---

## Wiring

**MatrixPortal S3 (recommended):** no signal wiring. Plug panel 1's HUB75
ribbon into the board, chain panel 1 OUT → panel 2 IN, wire each panel's 5V/GND
to the supply. Firmware: leave `USE_DEFAULT_PINS true`.

**Classic ESP32 (hand-wired):** set `USE_DEFAULT_PINS false` and wire per the
DMA library defaults:

```
R1  -> 25      G1  -> 26      B1  -> 27
R2  -> 14      G2  -> 12      B2  -> 13
A   -> 23      B   -> 19      C   -> 5
D   -> 17      E   -> 18
CLK -> 16      LAT -> 4       OE  -> 15
GND -> GND (common ground with the 5V supply!)
```

Power the panels from the 5V supply directly; tie its ground to the ESP32
ground. The ESP32 is powered over USB.

---

## Pi-side setup

1. Free API key at https://aisstream.io.
2. Install deps: `pip install websockets aiohttp pillow`
3. Copy `.env.example` to `.env` and add your key (`.env` is gitignored).
4. Run **one** of the services, pointed at your ESP32's IP (or the mock):

   ```bash
   # live variant
   AISSTREAM_KEY=... ESP32_HOST=192.168.1.50 python3 shipwall_service.py

   # register variant (18h default; set REGISTER_HOURS to change)
   AISSTREAM_KEY=... ESP32_HOST=192.168.1.50 python3 register_service.py
   ```

Both subscribe to the same outer box (Cape Vincent → Eisenhower Lock),
hard-coded near the top of each service:
```python
BOUNDING_BOX = [[[44.10, -76.40], [45.02, -74.78]]]
```
The live service also has an inner box (Chippewa Bay → Oak Point) feeding its
detail cards.

---

## ESP32-side setup

1. Arduino IDE → Boards Manager → install **esp32** by Espressif.
2. Library Manager → install `ESP32-HUB75-MatrixPanel-DMA` and `ArduinoJson`.
3. Edit `WIFI_SSID` / `WIFI_PASS` at the top of the `.ino` (2.4 GHz only).
4. Flash; read the assigned IP from the serial monitor at 115200 baud.
5. Put that IP into `ESP32_HOST` when starting the Pi service.

---

## What each variant shows

### Live variant — two zones

*Left — American Narrows detail (~88 px).* One big card for a single vessel, or
a top/bottom split cycling pairs every 6 s: large funnel, name, a down/upbound
glyph, speed, course, draught, destination. Empty → "AMERICAN NARROWS clear".

*Right — upper Seaway roster (~40 px).* Every big commercial ship between Cape
Vincent and the lock, one line each: direction glyph + name. Moving ships sort
first; up to 7 rows.

### Register variant — two auto-alternating modes

*BOARD.* A departure-board list, one line per ship: a small funnel color-chip,
the operator's 3-letter code, a direction arrow, the ship name, and how long
ago it was seen. Scrolls vertically when the list overflows. Most-recent first.

*DETAIL.* One ship at a time, full screen: the large 32×32 funnel plus the rich
AIS fields — type + flag, dimensions (e.g. `225x24m`), draught, navigation
status, destination + ETA, last-seen age. Cycles through the register, then
returns to the board. Missing fields are simply omitted.

### Shared display behavior

**Down / upbound.** Derived from course over ground (the river runs SW↔NE
through the reach). *Downbound* (seaward, NE-ish) → cyan ▼; *upbound* (toward
the lakes, SW-ish) → orange ▲; moored/anchored → grey ■.

**Brightness (sun-based, no extra hardware).** `schedule.py` computes sunrise/
sunset for the reach and ramps over 30 min at dawn/dusk — bright by day, ~11%
glow overnight. The Pi stamps the target into each frame; the ESP32 obeys.

**Idle / status screens.** Boot splash, READY+IP, "WAITING / for data" if the
Pi stops pushing for 60 s, and "SEAWAY CLOSED / reopens March" during the
winter closure (shown only when the reach is also empty).

**Power behavior.** The ESP32 holds no important state — every frame carries
everything. Pull power and restore it and the wall repopulates within seconds.

---

## Funnel-livery sprites

Each operator's funnel is a 32×32 sprite. At this size the band liveries read
cleanly and most crests are legible; logo-heavy funnels collapse to accurate
color blocks (intentional — the panel can't show fine logo detail). The
pathway:

```
AIS name/MMSI ──> operators.py ──> operator key ──> frame ──> renderer ──> sprite
```

**Identifying the operator.** `operators.py` maps a vessel two ways: an explicit
`mmsi_to_operator.json` lookup, then vessel-name rules (Algoma → "ALGO", CSL →
"CSL"/"BAIE", Fednav → "FEDERAL", Desgagnés by substring, etc.). Unmatched
vessels return `UNKNOWN` (shown as a ghost sprite) and are appended to
`unknown_vessels.json` to classify later.

Operator keys with sprites: `ALGOMA CSL FEDNAV ASC INTERLAKE LOWERLAKES
DESGAGNES ANDRIE CLIFFS G3 GLF HOLCIM MCASPHALT NACC VTB`, plus `UNKNOWN`.
Several (Andrie, Cliffs, G3, GLF, Holcim, McAsphalt, NACC, VTB) have **no name
pattern** and only resolve via `mmsi_to_operator.json` — populate that table
from observed sightings (the register's CSV log is good seed data).

**Building sprites from photos.** Drop a funnel image in `funnels/` (transparent
PNG works best), map it in `funnels/config.json`, and run:
```bash
pip install pillow
python3 photo_to_sprite.py     # writes ship_sprites.h + sprites_preview.png
python3 gallery.py             # labeled gallery.png of the whole set
```
For muddy funnels, `python3 suggest_palette.py funnels/X.png` prints the
dominant colors; add them as a `"palette"` so every pixel snaps to true livery
colors. See `funnels/README.md` for the full tuning guide. Note: the Fednav and
UNKNOWN (ghost) sprites are hand-authored directly in `ship_sprites.h`; a
`photo_to_sprite.py` rerun overwrites them and they must be re-injected.

---

## Testing without hardware

Each variant has a browser mock that impersonates the ESP32 (same `POST /frame`
endpoint, draws to a canvas at http://localhost:8080 using the real
`ship_sprites.h`). Run the matching pair — **the mock and service/simulator
must be from the same variant**, since their frame formats differ.

Live variant:
```bash
python3 mock_panel.py          # terminal 1
python3 simulate.py            # terminal 2 (synthetic; no AIS key)
# or, live AIS through the mock:
AISSTREAM_KEY=... ESP32_HOST=localhost:8080 python3 shipwall_service.py
```

Register variant:
```bash
python3 register_panel.py      # terminal 1
python3 register_simulate.py   # terminal 2 (synthetic; no AIS key)
#   set BIG=1 to overflow the board and watch it scroll
# or, live AIS through the panel:
AISSTREAM_KEY=... ESP32_HOST=localhost:8080 python3 register_service.py
```

Both panels serve on port 8080, so run only one at a time. The register page
title reads "recent-sightings register"; the live page reads "live preview".

### Unattended logging

Both services log every vessel the feed delivers to a CSV (first seen, and again
when its name/operator resolves — deduped so it stays compact over long runs):

```bash
SHIPWALL_LOG=ships.csv   python3 shipwall_service.py    # live
REGISTER_LOG=register.csv python3 register_service.py    # register
```

The register CSV includes the resolved operator/code/flag per vessel, making it
the best seed for building `mmsi_to_operator.json`: filter to `operator=UNKNOWN`,
read off the names + MMSIs, and map them. Both CSVs append across restarts.

Both mock panels also auto-save a 6×-upscaled PNG to `captures/` the first time
a vessel appears (deduped), so you can review what came through without watching
live. Requires the browser tab to stay open; `captures/` is gitignored.

---

## Keeping your API key out of git

The AISStream key is read from the environment or a gitignored `.env`:
```bash
cp .env.example .env     # add your real key here
```
`.gitignore` excludes `.env`, `*.key`, `ships*.csv`, `register*.csv`,
`captures/`, and source funnel photos. A real environment variable overrides the
file, so a systemd unit works without a `.env`. If a key lands in a commit,
**rotate it** at aisstream.io — that's faster and safer than scrubbing history.

---

## Run on boot (systemd)

`/etc/systemd/system/shipwall.service` (swap the ExecStart for whichever
variant you run):
```ini
[Unit]
Description=St. Lawrence Ship Wall
After=network-online.target
Wants=network-online.target

[Service]
Environment=AISSTREAM_KEY=your_key_here
Environment=ESP32_HOST=192.168.1.50
ExecStart=/usr/bin/python3 /home/pi/shipwall/register_service.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable --now shipwall
```

---

## Notes on AIS coverage

AISStream is a free, community/terrestrial receiver network; coverage of the
upper St. Lawrence is partial, so some vessels visible on commercial trackers
(MarineTraffic, etc.) may not appear. This is a data-source limitation, not a
bug — and it's the reason the register variant exists. For full local coverage,
a ~$30 RTL-SDR receiver running AIS-catcher near the river produces the same
AIS messages locally and drops into either service unchanged.