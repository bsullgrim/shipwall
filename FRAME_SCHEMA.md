# Ship Wall — Register Frame Schema (firmware contract)

The authoritative wire contract between `register_service.py` (producer, on the Pi)
and `register_esp32.ino` (consumer, on the MatrixPortal S3). Derived directly from
`build_frame()`. **If `build_frame()` changes, change this doc and the firmware in
the same commit.**

## Transport

- **Link:** USB serial, Pi → MatrixPortal, 115200 baud, 8N1.
- **Framing:** one frame per line, compact JSON (`separators=(",",":")` — no spaces),
  terminated by a single `\n`. No length prefix; the newline is the delimiter.
- **Cadence:** one frame every `PUSH_INTERVAL` seconds (default 10). The firmware
  must tolerate gaps (reconnects, Pi restarts) and show a "waiting" state after
  `DATA_TIMEOUT_MS` with no complete frame.
- **Size:** a full register (~30 ships) plus `emmett` is ~6–8 KB per line. Size the
  serial RX buffer and JSON document for the worst case, never the typical case —
  a truncated line is the #1 silent failure mode.

## Top-level object

| Field    | Type            | Always? | Meaning |
|----------|-----------------|---------|---------|
| `ts`     | int (epoch s)   | yes     | Frame build time. |
| `bright` | int 0–255       | yes     | Sun-based brightness target; drive panel brightness from this. |
| `closed` | bool            | yes     | Seaway winter closure. `true` → show the closed screen. |
| `hours`  | float           | yes     | Register retention window, for the header ("last 18h"). |
| `home`   | float 0–1       | yes     | Danger Island's river-progress. Pair with each ship's `progress` to place it relative to home. |
| `ships`  | array           | yes     | May be empty → show "register clear" idle screen. |
| `emmett` | object OR null  | yes     | `null` when no fresh Lake Guardian fix. Non-null → Emmett panel mode is available. |

## `ships[]` element

Sorted by the producer: named ships first (newest-first), then bare-MMSI fragments.

| Field      | Type                  | Always? | Notes |
|------------|-----------------------|---------|-------|
| `mmsi`     | int                   | yes     | |
| `name`     | str (≤20)             | yes     | `"MMSI <n>"` placeholder if AIS name not yet resolved. |
| `op`       | str                   | yes     | **Operator KEY — feeds `spriteForKey()`/`miniForKey()` directly.** `"UNKNOWN"` → ghost sprite. |
| `code`     | str (3)               | yes     | Board code, e.g. `ALG`. `"???"` fallback. |
| `type`     | str                   | yes     | AIS type label; `"VSL"` fallback. |
| `dir`      | str — `D`/`U`/`M`/`?` | yes     | Down / up / moored / unknown. Drives the glyph. |
| `sog`      | float (1dp)           | yes     | `0.0` when missing (sentinel, not "stopped"). |
| `cog`      | int (deg)             | yes     | `0` when missing (sentinel). |
| `navstat`  | str OR **null**       | no      | Omit the line when null. |
| `flag`     | str (2) OR **null**   | no      | ISO country; null when MID unknown. |
| `length`   | int (m) OR **null**   | no      | |
| `beam`     | int (m) OR **null**   | no      | |
| `draught`  | number OR **null**    | no      | **Key is `draught`, not `drft`.** |
| `dest`     | str (≤18)             | yes     | `""` when missing. |
| `eta`      | str `"DD HH:MM"` OR **null** | no | |
| `callsign` | str OR **null**       | no      | |
| `imo`      | int OR **null**       | no      | |
| `age`      | str — `now`/`Nm`/`Nh` | yes     | Compact, for the board. |
| `age_secs` | int                   | yes     | Raw age if you'd rather format on-device. |
| `progress` | float 0–1 OR **null** | no      | Null when no position. Compare to top-level `home`. |

**Nullable group:** `navstat, flag, length, beam, draught, eta, callsign, imo, progress`.
These arrive as JSON `null`. On the detail card, treat null as "line absent" — do
not print `0` or `null`. `sog`/`cog`/`dest` are never null; they use sentinels.

## `emmett` object (when non-null)

| Field      | Type             | Notes |
|------------|------------------|-------|
| `lat`,`lon`| float            | Position on the Lakes. |
| `sog`      | float OR null    | |
| `cog`      | float OR null    | |
| `navstat`  | int OR null      | Raw AIS code here (NOT the string label used in `ships[]`). |
| `dest`     | str (may be "")  | AIS destination, for port-icon lighting. |
| `name`     | str              | Vessel name. |
| `age_secs` | int              | Fix age. |

## Operator keys (must match `ship_sprites.h`)

`op` is passed verbatim to `spriteForKey()`. The compiled sprite table keys are the
contract. As of the current header (30 keys + UNKNOWN fallback): ALGOMA, ANDRIE, ASC,
BIGLIFT, BRIESE, CALFORNAV, CARISBROOKE, CCG, CLIFFS, COASTAL, CSL, DESGAGNES, FEDNAV,
G3, GRANATH, GROUPOCEAN, LOWERLAKES, MCASPHALT, MCKEIL, NACC, NEAS, NMBULGARE, POLSTEAM,
SPLIETHOFF, TBMARINE, USCG, USESPA, VTB, WAGENBORG, UNKNOWN. Any `op` not in the table
falls back to the UNKNOWN ghost — by design, never an error.
