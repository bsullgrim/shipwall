# St. Lawrence Ship Wall

A FlightWall-style LED display for ship traffic on the upper St. Lawrence
Seaway, watching the reach from **Cape Vincent down to the Snell/Beauharnois
lock** above Montreal. A Python service subscribes to live AIS, identifies each
vessel's operator (and thus its funnel livery), and pushes compact frames to an
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

Data & diagnostics:
- `mmsi_database.json` — persistent MMSI → identity store the service grows
  from static AIS messages (name/operator/type/dimensions). Lets known vessels
  be identified the moment their position arrives, instead of appearing as
  ghosts. Set `MMSI_DB=mmsi_database.json` to enable.
- `seed_mmsi_db.py` — bootstrap that database from an existing register CSV, so
  it starts populated with everything seen so far (operators re-resolved via
  current rules).
- `clean_register.py` — dedupe a messy register: drop restart-duplicate spam,
  prune sightings outside the box, and re-resolve operators against current
  rules. Outputs a drop-in `register.csv` (`-o file` to write directly).
- `operator_worklist.py` — list named-but-UNKNOWN vessels and assign
  MMSI → operator mappings (validated against the sprite-backed set).
- `passage_stats.py` — LAN web page (port 8090) showing which ships have passed
  Danger Island, how often, and recent crossings (`--demo` for sample data).
- `coverage_probe.py` — listen for AIS base stations and render
  `coverage_map.html` (Leaflet) showing inferred receiver coverage vs. the
  vessels actually heard.

---

## Parts list (~$140)

The reference build (what this project is wired and documented for):

| Part | Adafruit ID | Notes | Approx |
|------|-------------|-------|--------|
| 128×64 RGB LED matrix, **2 mm pitch** | 6484 | Single monolithic panel (no chaining/seam). 256×128 mm. **Non-standard 5-address (ABCDE) mux, 1/32 scan.** Includes IDC ribbon + power cable. | $75 |
| Adafruit **MatrixPortal S3** | 5778 | ESP32-S3 renderer; HUB75 connector + level shifter + PSRAM built in. Drives the 6484 with **no solder jumper**. | $20 |
| **Raspberry Pi 3 Model B** (or any always-on box) | 3055 | Runs the Python AIS service over WiFi. The Pi does logic only — not LED refresh — so a Pi 3 is plenty. | $35 |
| 5V 2.5A micro-USB supply | 1995 | Powers the **Pi 3**. | $8 |
| **5V 4A supply + 2.1 mm barrel jack** | 1466 (+368) | Powers the **panel** (up to ~4 A). *Separate from the Pi supply — this is the one easy to forget.* | $15 |
| USB-A → USB-C cable, short | — | **Pi → MatrixPortal**: serial frame link *and* MatrixPortal logic power, one cable. | $5 |
| microSD card, 16 GB+ | — | Raspberry Pi OS Lite for the Pi 3. | $6 |

Optional finishing: a deep shadowbox frame (hides the Pi, MatrixPortal, supply,
and cabling), and a smoked/black diffusion acrylic over the panel face (hides
the LED grid, deepens blacks, gives the "screen" look).

Notes:
- The **6484 panel uses 5-address (ABCDE) multiplexing** — fine on the
  MatrixPortal S3, but the firmware must be configured for it (1/32 scan,
  5-address), not the 4-address default, or the image is scrambled.
- The panel kit **includes its IDC ribbon and 4-pin power cable** — no need to
  buy those.
- A monolithic 128×64 needs **no chaining** — one ribbon to the MatrixPortal,
  one 5 V feed to the panel.

---

## Architecture & wiring

**Pi + ESP32, linked by USB serial, both inside the frame.** The Pi runs the
Python service (AIS → identify → build frames) and sends frames down a USB
cable to the MatrixPortal S3, which renders them to the panel. The ESP32 needs
**no WiFi** — only the Pi is networked (for AIS). One USB cable carries both the
frame data and the MatrixPortal's logic power.

```
   AIS (WiFi) ──> Raspberry Pi 3 ──USB serial──> MatrixPortal S3 ──HUB75──> 128×64 panel
                  (Python service)               (renderer)                 ^
                       ^                                                     │
                  5V 2.5A (1995)                              5V 4A barrel (1466) ── panel power
```

**Connections:**
- Panel IDC ribbon → MatrixPortal HUB75 connector (plug-in, keyed).
- Panel 4-pin power → 5V/4A supply via the barrel jack + screw terminal.
- MatrixPortal USB-C → Pi USB-A (data + MatrixPortal power).
- Pi powered from its own 5V/2.5A micro-USB supply.

**One wall plug:** feed both the panel's 5V/4A supply and the Pi's 5V/2.5A
supply from a small power strip — that strip's plug is your single wall outlet.
(Don't power the panel from the Pi or the MatrixPortal; it needs its own 5 V.)

**Firmware note:** the MatrixPortal renders over USB serial, not WiFi. Configure
the panel as 128×64, 1/32 scan, **5-address (ADDX_E)** in the
`ESP32-HUB75-MatrixPanel-DMA` setup, and read frames as newline-delimited JSON
from the USB serial port.

---

## Pi-side setup

1. Free API key at https://aisstream.io.
2. Install deps: `pip install websockets aiohttp pillow pyserial`
3. Copy `.env.example` to `.env` and add your key (`.env` is gitignored).
4. Run **one** of the services. On hardware, point it at the MatrixPortal's USB
   serial device; for the browser mock, point it at the mock's HTTP host:

   ```bash
   # --- hardware (USB serial to the MatrixPortal) ---
   AISSTREAM_KEY=... ESP32_SERIAL=/dev/ttyACM0 python3 register_service.py

   # --- browser mock (HTTP) ---
   AISSTREAM_KEY=... ESP32_HOST=localhost:8080 python3 register_service.py
   ```
   (The live variant works the same way with `shipwall_service.py`.) The Pi's
   serial device is usually `/dev/ttyACM0` for the MatrixPortal; check with
   `ls /dev/ttyACM*` after plugging it in.

Both subscribe to the same outer box (Cape Vincent → Snell/Beauharnois lock),
hard-coded near the top of each service:
```python
BOUNDING_BOX = [[[44.10, -76.40], [45.3237, -73.9132]]]
```
The box stops at the lock to exclude Montreal harbor traffic. The live service
also has an inner box (the American Narrows) feeding its detail cards.

---

## MatrixPortal (ESP32) setup

1. Arduino IDE → Boards Manager → install **esp32** by Espressif; select
   *Adafruit MatrixPortal ESP32-S3* as the board.
2. Library Manager → install `ESP32-HUB75-MatrixPanel-DMA` and `ArduinoJson`.
3. In the `.ino`, configure the panel: **128×64, 1/32 scan, 5-address
   (ADDX_E)** — the 6484 is a non-standard 5-address panel; the 4-address
   default renders scrambled.
4. No WiFi config needed — the firmware reads newline-delimited JSON frames
   from **USB serial** at 115200 baud.
5. Flash, then plug the MatrixPortal's USB-C into the Pi and start the service
   with `ESP32_SERIAL=/dev/ttyACM0`.

*(The register firmware `register_esp32.ino` is the serial renderer for this
build; the live `shipwall_esp32.ino` is the older WiFi/HTTP renderer, kept for
the archive.)*

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
status, destination + ETA, last-seen age — and a **river progress line** along
the bottom showing where the ship is between Lake Ontario and the lock, with
Danger Island marked as a fixed reference. Only *named* ships get a detail card;
unidentified "ghost" vessels stay on the board but don't get a solo card. Cycles
through the named ships, then returns to the board. Missing fields are omitted.

### Shared display behavior

**Down / upbound.** Derived from course over ground (the river runs SW↔NE
through the reach). *Downbound* (seaward, NE-ish) → cyan ▼; *upbound* (toward
the lakes, SW-ish) → orange ▲; moored/anchored → grey ■.

**Brightness (sun-based, no extra hardware).** `schedule.py` computes sunrise/
sunset for the reach and ramps over 30 min at dawn/dusk — bright by day, ~11%
glow overnight. The Pi stamps the target into each frame; the ESP32 obeys.

**Idle / status screens.** Boot splash, a ready screen, "WAITING / for data" if the
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

### Unattended logging & persistence

The register service can maintain several persistent files (all optional, off
unless their env var is set; the startup banner shows which are active):

```bash
REGISTER_LOG=register.csv \
PASSAGE_LOG=passages.csv \
MMSI_DB=mmsi_database.json \
AISSTREAM_KEY=... ESP32_SERIAL=/dev/ttyACM0 python3 register_service.py
```

- **`REGISTER_LOG`** — every vessel the feed delivers (first seen, and again
  when its name/operator resolves; deduped, and the dedup persists across
  restarts so it stays compact). The register CSV includes the resolved
  operator/code/flag, making it the best seed for `mmsi_to_operator.json` and
  for `seed_mmsi_db.py`. On restart the service also warm-starts the display
  from this file, so the board isn't empty while waiting for ships to re-report.
- **`PASSAGE_LOG`** — vessels inferred to have transited Danger Island. There's
  no AIS coverage at the island itself, so a passage is inferred when a ship's
  position crosses the home point between two sightings (seen above, later
  below = downbound, and vice versa). View the tally at `passage_stats.py`.
- **`MMSI_DB`** — the persistent identity database (see the file map). Grows as
  static messages resolve vessels; pre-fills known MMSIs on sight. Bootstrap it
  from an existing register with `python3 seed_mmsi_db.py register.csv`.

Use absolute paths if the service and `passage_stats.py` run from different
directories. The live variant logs vessels with `SHIPWALL_LOG=ships.csv`.

The register mock can also auto-save a PNG to `captures/` the first time a
vessel appears, but this is **off by default** (set `CAPTURE=true` in
`register_panel.py` to re-enable). `captures/` is gitignored.

---

## Keeping your API key out of git

The AISStream key is read from the environment or a gitignored `.env`:
```bash
cp .env.example .env     # add your real key here
```
`.gitignore` excludes `.env`, `*.key`, the runtime data files (`ships*.csv`,
`register*.csv`, `passages*.csv`, `mmsi_database.json`, `coverage_map*.html`),
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
Environment=ESP32_SERIAL=/dev/ttyACM0
Environment=REGISTER_LOG=/home/pi/shipwall/register.csv
Environment=PASSAGE_LOG=/home/pi/shipwall/passages.csv
Environment=MMSI_DB=/home/pi/shipwall/mmsi_database.json
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