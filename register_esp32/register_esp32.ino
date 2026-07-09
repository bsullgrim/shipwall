/*
 * register_esp32.ino  --  St. Lawrence Ship Wall, REGISTER variant (128x64).
 *
 * Serial renderer for the recent-sightings register. Replaces the older
 * WiFi/HTTP shipwall_esp32.ino: this build takes newline-delimited JSON frames
 * from the Pi over USB serial (no WiFi) and renders them.
 *
 * Hardware:
 *   - Adafruit MatrixPortal S3 (ESP32-S3 + HUB75 + level shifter + PSRAM).
 *   - One monolithic 128x64 HUB75 panel (the 6484: 1/32 scan, 5-address ADDX_E).
 *     NOTE the 6484 is a NON-STANDARD 5-address panel. The 4-address default
 *     renders scrambled. We set DRIVER + ADDX_E mode below.
 *   - 5V/4A panel supply (never power the panel from the board).
 *
 * Libraries (Arduino Library Manager):
 *   - ESP32-HUB75-MatrixPanel-DMA  (by mrcodetastic)
 *   - ArduinoJson                  (by Benoit Blanchon, v6+)
 *
 * Frame contract: see FRAME_SCHEMA.md. Producer: register_service.py build_frame().
 *
 * Display modes (auto):
 *   BOARD   -- departure-board list of the register (mini chip + code + dir + name + age).
 *   DETAIL  -- one ship full-screen: 32px funnel left, AIS fields right; cycles.
 *   EMMETT  -- "Where's Emmett": shown when the frame carries a non-null emmett object.
 *   IDLE / CLOSED / WAITING cover every other state so the panel is never blank.
 */

#include <Adafruit_Protomatter.h>
#include <ArduinoJson.h>
#include "ship_sprites.h"   // SPRITE_SIZE, spriteForKey(), miniForKey(), MINI_SIZE

// ---- Panel geometry ---------------------------------------------------------
#define PANEL_W 128
#define PANEL_H 64

// MatrixPortal ESP32-S3 HUB75 pin mapping. addrPins has FIVE entries (A-E)
// because this is a 64-pixel-tall 1/32-scan panel using 5-address multiplexing.
// rgbPins order is R1,G1,B1,R2,G2,B2. NOTE: this specific panel has red and
// blue swapped relative to the Adafruit default, so R and B are exchanged here
// (R1<->B1 and R2<->B2). Confirmed with an on-panel R/G/B/W swatch test: with
// the stock order, red displayed as blue and blue as red (green/white correct).
static uint8_t rgbPins[]  = {40, 41, 42, 37, 39, 38};
static uint8_t addrPins[] = {45, 36, 48, 35, 21};   // A B C D E  (5 = 64 tall)
static uint8_t clockPin   = 2;
static uint8_t latchPin   = 47;
static uint8_t oePin      = 14;

// width, bit-depth, chain(1), rgbPins, 5 addr pins, clk, latch, oe, double-buffer
// Bit depth 5 (not 6): on the S3 driving 128 wide, depth 6 drops the refresh
// rate enough that dim colors visibly shimmer. Depth 5 roughly doubles the
// headroom for a steady image; the color loss is negligible for these sprites.
// If text still shimmers, try 4. If sprites look posterized, you have room to
// go back toward 6 only if the refresh stays high enough to look steady.
Adafruit_Protomatter matrix(
  PANEL_W, 5, 1, rgbPins, 5, addrPins, clockPin, latchPin, oePin, true);

// ---- Serial frame reader ----------------------------------------------------
// A full frame is unbounded by ship count: on a busy Seaway day the register
// can hold 40+ eligible vessels at ~300 bytes each, which blew past the old
// 12KB ceiling and silently dropped EVERY frame until traffic thinned -- the
// cause of intermittent "PI OFFLINE". 32KB covers ~100 ships. The S3 has plenty
// of RAM (compile showed ~250KB free). The Pi side also caps the list now, so
// this is headroom, not the primary guard.
static const size_t FRAME_BUF_MAX = 32768;     // 32 KB line ceiling
static char     lineBuf[FRAME_BUF_MAX];
static size_t   lineLen = 0;

// ArduinoJson 7: JsonDocument grows elastically on the heap as the frame is
// parsed -- no fixed capacity to size. (v6's DynamicJsonDocument(capacity) is
// deprecated in v7.) The frame buffer above still bounds the raw line length.

// ---- Timing (matched to register_panel.py mock) -----------------------------
const uint32_t DATA_TIMEOUT_MS  = 60000;  // mock: 60s no data -> "WAITING"
const int      ROW_H            = 10;     // px per board row
const int      BOARD_SCROLL_SPEED = 8;    // px/sec
const uint32_t BOARD_SCROLL_PAUSE = 2000; // ms hold at top/bottom
const uint32_t BOARD_FIT_DWELL  = 8000;   // ms board dwell when it fits
const uint32_t DETAIL_MS        = 5000;   // ms per named-ship detail card
const uint32_t EMMETT_MS        = 6000;   // ms Emmett slot when a fresh fix exists

// ---- Latest frame state -----------------------------------------------------
struct Ship {
  long   mmsi;
  String name, op, code, type, dest, eta, callsign, navstat, flag, age;
  char   dir;
  float  sog;  int cog;
  int    length, beam;   // -1 == null
  float  draught;        // NAN == null
  long   imo;            // -1 == null
  int    ageSecs;
  float  progress;       // NAN == null
  bool   hasLen, hasBeam, hasDraught, hasImo, hasProgress;
};

const int SHIPS_MAX = 40;
Ship  ships[SHIPS_MAX];
int   shipCount = 0;

bool      haveEmmett = false;
struct {
  float lat, lon, sog, cog;
  int   navstat;
  String dest, name;
  int   ageSecs;
  bool  hasSog, hasCog, hasNav;
} emmett;
#include "emmett_render.h"

uint8_t  gBright   = 128;
bool     gClosed   = false;
float    gHours    = 18.0f;
float    gHome     = 0.5f;
uint32_t lastFrameMs = 0;
bool     gotFirstFrame = false;     // false until the first complete frame parses
uint32_t lastHeartbeatMs = 0;       // last time we emitted a liveness heartbeat

// ---- Colors -----------------------------------------------------------------
uint16_t C_NAME, C_LABEL, C_VALUE, C_DIM, C_UP, C_DOWN, C_ACCENT, C_CODE;

// ---- Drawing helpers (mirror the mock/old firmware) -------------------------
void drawSpriteScaled(const uint16_t* spr, int ox, int oy, int dim) {
  for (int y = 0; y < dim; y++) {
    int sy = y * SPRITE_SIZE / dim;
    for (int x = 0; x < dim; x++) {
      int sx = x * SPRITE_SIZE / dim;
      uint16_t c = pgm_read_word(&spr[sy * SPRITE_SIZE + sx]);
      if (c != 0x0000) matrix.drawPixel(ox + x, oy + y, c);   // black = transparent
    }
  }
}

void drawMini(const uint16_t* mini, int ox, int oy) {
  for (int y = 0; y < MINI_SIZE; y++)
    for (int x = 0; x < MINI_SIZE; x++) {
      uint16_t c = pgm_read_word(&mini[y * MINI_SIZE + x]);
      if (c != 0x0000) matrix.drawPixel(ox + x, oy + y, c);
    }
}

// Mock dirGlyph: D points down, U points up (D flipped), M = small anchor,
// ? = dim dash. Colors: down = light blue, up = orange (mock C2).
void drawDirGlyph(char dir, int x, int y) {
  uint16_t col = (dir == 'D') ? C_DOWN : (dir == 'U') ? C_UP : C_DIM;
  if (dir == 'D') {
    for (int r = 0; r < 4; r++)
      for (int c = r; c <= 4 - r; c++) matrix.drawPixel(x + c, y + r, col);
  } else if (dir == 'U') {
    for (int r = 0; r < 4; r++)
      for (int c = r; c <= 4 - r; c++) matrix.drawPixel(x + c, y + (3 - r), col);
  } else if (dir == 'M') {
    // small anchor: ring, stock crossbar, shank, curved flukes
    static const int8_t px[][2] = {{2,0},{1,1},{2,1},{3,1},{2,2},{0,3},{2,3},{4,3},{1,4},{2,4},{3,4}};
    for (unsigned i = 0; i < sizeof(px)/sizeof(px[0]); i++)
      matrix.drawPixel(x + px[i][0], y + px[i][1], col);
  } else {
    matrix.fillRect(x + 1, y + 2, 3, 1, col);     // '?' dim dash
  }
}

void textAt(const char* s, int x, int y, uint16_t color) {
  matrix.setTextSize(1);
  matrix.setTextColor(color);
  matrix.setCursor(x, y);
  matrix.print(s);
}

// ---- Parse one frame into state ---------------------------------------------
bool applyFrame(const char* json, size_t len) {
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, json, len);
  if (err) {
    // TEMP DEBUG: report why a line failed and show what arrived. Remove once
    // frames parse. Prints to the same USB serial; watch with arduino-cli monitor
    // on the panel, NOT while register_service.py holds the port.
    Serial.print("parse fail: "); Serial.print(err.c_str());
    Serial.print("  len="); Serial.print((int)len);
    Serial.print("  head="); 
    for (size_t i = 0; i < len && i < 40; i++) Serial.write(json[i]);
    Serial.println();
    return false;                  // bad/truncated line: keep last frame
  }

  gBright = doc["bright"] | 128;
  gClosed = doc["closed"] | false;
  gHours  = doc["hours"]  | 18.0f;
  gHome   = doc["home"]   | 0.5f;
  // (gBright is parsed but not applied: Protomatter has no runtime brightness.)

  JsonArray arr = doc["ships"].as<JsonArray>();
  shipCount = 0;
  for (JsonObject o : arr) {
    if (shipCount >= SHIPS_MAX) break;
    Ship& s = ships[shipCount++];
    s.mmsi     = o["mmsi"]  | 0L;
    s.name     = (const char*)(o["name"] | "");
    s.op       = (const char*)(o["op"]   | "UNKNOWN");
    s.code     = (const char*)(o["code"] | "???");
    s.type     = (const char*)(o["type"] | "VSL");
    const char* d = o["dir"] | "?";
    s.dir      = d[0] ? d[0] : '?';
    s.sog      = o["sog"] | 0.0f;
    s.cog      = o["cog"] | 0;
    s.dest     = (const char*)(o["dest"] | "");
    s.age      = (const char*)(o["age"]  | "");
    s.ageSecs  = o["age_secs"] | 0;
    // nullable group: presence test, not value-or-default
    s.hasLen      = !o["length"].isNull();   s.length   = s.hasLen      ? (int)o["length"]   : -1;
    s.hasBeam     = !o["beam"].isNull();     s.beam     = s.hasBeam     ? (int)o["beam"]     : -1;
    s.hasDraught  = !o["draught"].isNull();  s.draught  = s.hasDraught  ? (float)o["draught"]: NAN;
    s.hasImo      = !o["imo"].isNull();      s.imo      = s.hasImo      ? (long)o["imo"]     : -1;
    s.hasProgress = !o["progress"].isNull(); s.progress = s.hasProgress ? (float)o["progress"] : NAN;
    s.navstat  = o["navstat"].isNull()  ? String() : String((const char*)o["navstat"]);
    s.flag     = o["flag"].isNull()     ? String() : String((const char*)o["flag"]);
    s.eta      = o["eta"].isNull()      ? String() : String((const char*)o["eta"]);
    s.callsign = o["callsign"].isNull() ? String() : String((const char*)o["callsign"]);
  }

  JsonVariant em = doc["emmett"];
  haveEmmett = !em.isNull();
  if (haveEmmett) {
    emmett.lat = em["lat"] | 0.0f;
    emmett.lon = em["lon"] | 0.0f;
    emmett.hasSog = !em["sog"].isNull(); emmett.sog = emmett.hasSog ? (float)em["sog"] : NAN;
    emmett.hasCog = !em["cog"].isNull(); emmett.cog = emmett.hasCog ? (float)em["cog"] : NAN;
    emmett.hasNav = !em["navstat"].isNull(); emmett.navstat = emmett.hasNav ? (int)em["navstat"] : -1;
    emmett.dest = (const char*)(em["dest"] | "");
    emmett.name = (const char*)(em["name"] | "USEPA LAKE GUARDIAN");
    emmett.ageSecs = em["age_secs"] | 0;
  }
  lastFrameMs = millis();
  gotFirstFrame = true;
  return true;
}

// Pull bytes from Serial, assemble lines, apply on newline.
// Self-resyncing: a frame always starts with '{'. After an ESP32 reset or a
// mid-frame RX overflow, the byte stream can begin partway through a frame,
// permanently offsetting every '\n' boundary by one frame -- so each line then
// parses as "tail of N + head of N+1" and fails forever until the Pi restarts
// (observed: a 2h "WAITING" stall after a power-on reset). To recover on our
// own we refuse to buffer anything until we've seen a '{', and only parse a
// line that starts with '{'. The first clean newline after a desync then
// realigns us to a real frame boundary. (register_service.py emits
// json.dumps(frame)+"\n", which always starts with '{', so this never drops a
// valid frame. If the wire format ever gains a prefix/wrapper, revisit.)
void pumpSerial() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n') {
      if (lineLen > 0 && lineLen < FRAME_BUF_MAX && lineBuf[0] == '{')
        applyFrame(lineBuf, lineLen);
      lineLen = 0;                          // also resets after an overlong drop
    } else if (lineLen == 0 && c != '{') {
      continue;                             // between frames: ignore until a '{'
    } else if (lineLen < FRAME_BUF_MAX - 1) {
      lineBuf[lineLen++] = c;
    } else {
      lineLen = FRAME_BUF_MAX - 1;               // overlong: wait for newline, then drop
    }
  }
}

// ---- Screens ----------------------------------------------------------------
void screenCentered(const char* msg, uint16_t color) {
  int16_t x1, y1; uint16_t w, h;
  matrix.setTextSize(1);
  matrix.getTextBounds(msg, 0, 0, &x1, &y1, &w, &h);
  textAt(msg, (PANEL_W - w) / 2, (PANEL_H - h) / 2, color);
}

// Two centered lines (mock uses these for NO SHIPS, WAITING, SEAWAY CLOSED).
void centeredLine(const char* msg, int y, uint16_t color) {
  int16_t x1, y1; uint16_t w, h;
  matrix.setTextSize(1);
  matrix.getTextBounds(msg, 0, 0, &x1, &y1, &w, &h);
  textAt(msg, (PANEL_W - w) / 2, y, color);
}
void screenCentered2(const char* a, const char* b, uint16_t ca, uint16_t cb) {
  centeredLine(a, PANEL_H / 2 - 8, ca);
  centeredLine(b, PANEL_H / 2 + 2, cb);
}

// Board display duration: fixed dwell if the list fits, else long enough to
// scroll down and back with top/bottom pauses (mock boardCycleMs).
// Forward declaration: drawRow/drawBoard below use textClip, which is defined
// later in the file.
void textClip(const char* s, int x, int y, int maxX, uint16_t color);

uint32_t boardCycleMs(int n) {  int totalH = n * ROW_H;
  if (totalH <= PANEL_H) return BOARD_FIT_DWELL;
  int travel = totalH - PANEL_H;
  uint32_t scrollMs = (uint32_t)((long)travel * 1000 / BOARD_SCROLL_SPEED);
  return BOARD_SCROLL_PAUSE + scrollMs + BOARD_SCROLL_PAUSE + scrollMs;
}

void drawRow(Ship& s, int y) {
  char keybuf[16]; s.op.toCharArray(keybuf, sizeof(keybuf));
  drawMini(miniForKey(keybuf), 0, y + 1);             // 8x8 funnel chip
  textAt(s.code.c_str(), 10, y + 2, C_CODE);
  drawDirGlyph(s.dir, 29, y + 2);
  int ageW = s.age.length() ? (int)s.age.length() * 6 - 1 : 0;
  int ageX = PANEL_W - 1 - ageW;          // -1 keeps the last column on-panel
  textClip(s.name.c_str(), 36, y + 2, ageX - 2, C_NAME);
  if (s.age.length()) textAt(s.age.c_str(), ageX, y + 2, C_DIM);
}

// animT = elapsed ms; drives the scroll position within the board phase.
void drawBoard(uint32_t animT) {
  int n = shipCount;
  if (n == 0) { screenCentered2("NO SHIPS", "in window", C_ACCENT, C_DIM); return; }
  int totalH = n * ROW_H;
  int scroll = 0;
  if (totalH > PANEL_H) {
    int travel = totalH - PANEL_H;
    uint32_t scrollMs = (uint32_t)((long)travel * 1000 / BOARD_SCROLL_SPEED);
    uint32_t cycle = BOARD_SCROLL_PAUSE + scrollMs + BOARD_SCROLL_PAUSE + scrollMs;
    uint32_t p = animT % cycle;
    if (p < BOARD_SCROLL_PAUSE) scroll = 0;
    else if (p < BOARD_SCROLL_PAUSE + scrollMs)
      scroll = (int)((long)(p - BOARD_SCROLL_PAUSE) * travel / scrollMs);
    else if (p < BOARD_SCROLL_PAUSE + scrollMs + BOARD_SCROLL_PAUSE)
      scroll = travel;
    else
      scroll = travel - (int)((long)(p - BOARD_SCROLL_PAUSE - scrollMs - BOARD_SCROLL_PAUSE) * travel / scrollMs);
  }
  for (int i = 0; i < n; i++) {
    int y = i * ROW_H - scroll;
    if (y + ROW_H < 0 || y > PANEL_H) continue;       // skip off-screen rows
    drawRow(ships[i], y);
  }
}

// Draw text but stop before any glyph would cross maxX (mock's txtClip).
// GFX font advances 6px/char; a glyph is ~5px wide.
void textClip(const char* s, int x, int y, int maxX, uint16_t color) {
  int gx = x;
  for (const char* p = s; *p; ++p) {
    if (gx + 5 > maxX) break;
    char buf[2] = { *p, 0 };
    textAt(buf, gx, y, color);
    gx += 6;
  }
}

// Horizontal "where on the river" indicator (mock drawRiverLine). Lake Ontario
// at the left end, Montreal at the right; green home tick (Danger Island) and a
// direction-colored ship dot. progress/home are 0..1 fractions; NaN = absent.
void drawRiverLine(float progress, float home, char dir, int y) {
  const int x0 = 2, x1 = PANEL_W - 3, span = x1 - x0;
  for (int x = x0; x <= x1; x++) matrix.drawPixel(x, y, C_DIM);
  uint16_t cap = matrix.color565(85, 85, 85);          // #555 end caps
  for (int d = -1; d <= 1; d++) {
    matrix.drawPixel(x0, y + d, cap);
    matrix.drawPixel(x1, y + d, cap);
  }
  if (!isnan(home)) {
    int hx = x0 + (int)lround(home * span);
    for (int d = -1; d <= 1; d++) matrix.drawPixel(hx, y + d, C_ACCENT);
  }
  if (!isnan(progress)) {
    int sx = x0 + (int)lround(progress * span);
    uint16_t col = (dir == 'D') ? C_DOWN : (dir == 'U') ? C_UP : C_VALUE;
    matrix.fillRect(sx - 1, y - 1, 3, 3, col);
  }
}

void drawDetail(int idx) {
  if (idx < 0 || idx >= shipCount) { drawBoard(millis()); return; }
  Ship& s = ships[idx];
  char keybuf[16]; s.op.toCharArray(keybuf, sizeof(keybuf));

  const int edge = PANEL_W - 1;

  // Name full-width across the very top; dir glyph pinned far right of that row.
  drawDirGlyph(s.dir, edge - 5, 1);
  { String nm = s.name; int maxCh = (edge - 6 - 1) / 6;
    if ((int)nm.length() > maxCh) nm = nm.substring(0, maxCh);
    textAt(nm.c_str(), 1, 1, C_NAME); }

  // Funnel justified top-left, full 32px, just below the name row.
  const int ftop = 9;
  drawSpriteScaled(spriteForKey(keybuf), 0, ftop, SPRITE_SIZE);

  // Fields beside the funnel (to its right).
  const int tx = SPRITE_SIZE + 4;     // x = 36
  int yy = ftop + 2;                   // y = 11
  { String l1 = s.type; if (s.flag.length()) l1 += " " + s.flag;
    textClip(l1.c_str(), tx, yy, edge, C_LABEL); yy += 10; }
  if (s.length > 0 && s.beam > 0) {
    char b[16]; snprintf(b, sizeof(b), "%dx%dm", s.length, s.beam);
    textClip(b, tx, yy, edge, C_VALUE); yy += 10;
  }
  if (s.age.length()) {
    String a = "seen " + s.age;
    textClip(a.c_str(), tx, yy, edge, C_DIM); yy += 10;
  }

  // Below the funnel: full-width rows for the remaining fields.
  int by = ftop + SPRITE_SIZE + 1;     // y ~= 42
  { String line2;
    if (s.hasDraught) { char b[16]; snprintf(b, sizeof(b), "%.1fm draft", s.draught); line2 = b; }
    if (s.navstat.length()) { if (line2.length()) line2 += "  "; line2 += s.navstat; }
    if (line2.length()) { textClip(line2.c_str(), 1, by, edge, C_VALUE); by += 10; }
  }
  if (s.dest.length()) {
    String d = ">" + s.dest; if (s.eta.length()) d += " " + s.eta;
    textClip(d.c_str(), 1, by, edge, C_VALUE);
  }

  // River progress line along the very bottom: Lake Ontario (left) -> Montreal
  // (right), with the home tick (Danger Island) and the ship's position dot.
  drawRiverLine(s.hasProgress ? s.progress : NAN, gHome, s.dir, PANEL_H - 2);
}

// ---- Setup / loop -----------------------------------------------------------
void setup() {
  Serial.begin(115200);

  // The panel object is constructed above with 5 address pins (A-E), which is
  // what a 64-tall 1/32-scan panel needs. Protomatter draws to a back buffer;
  // nothing appears until matrix.show() is called (done at the end of loop()).
  ProtomatterStatus st = matrix.begin();
  if (st != PROTOMATTER_OK) {
    // If init fails the panel can't be driven -- park here so the failure is
    // unambiguous rather than a mystery-black panel. Status is printed to
    // serial for diagnosis (0 = OK; non-zero = a pin/PORT or alloc problem).
    Serial.print("Protomatter begin() failed: ");
    Serial.println((int)st);
    for (;;) delay(1000);
  }
  matrix.fillScreen(0);
  // NOTE: Protomatter has no runtime brightness control (unlike the DMA lib's
  // setBrightness8). The frame's "bright" field is therefore a no-op on the
  // panel for now; if dimming is wanted later it must be done by scaling pixel
  // colors before drawing.

  // Palette matched to register_panel.py mock (C and C2 objects).
  C_NAME   = matrix.color565(255, 200,  40);   // #ffc828 amber
  C_LABEL  = matrix.color565( 90, 140, 255);   // #5a8cff blue
  C_VALUE  = matrix.color565(230, 230, 230);   // #e6e6e6
  C_DIM    = matrix.color565(110, 110, 110);   // #6e6e6e
  C_UP     = matrix.color565(255, 150,  60);   // #ff963c orange (upbound)
  C_DOWN   = matrix.color565( 80, 200, 255);   // #50c8ff light blue (downbound)
  C_ACCENT = matrix.color565( 60, 220, 120);   // #3cdc78 green
  C_CODE   = matrix.color565(154, 208, 255);   // #9ad0ff light blue
}

// True for a ship that should get a detail card: has a real name (not the
// "MMSI ..." placeholder). Ghosts stay on the board only. (Mock's named filter.)
bool isNamed(const Ship& s) {
  return s.name.length() && !s.name.startsWith("MMSI ");
}

void loop() {
  pumpSerial();
  uint32_t nowMs = millis();
  matrix.fillScreen(0);

  if (!gotFirstFrame) {
    // Cold boot: panel up, Pi hasn't sent a frame yet (the Pi takes 30-90s on a
    // 3B to boot, start the service, connect and warm-start). Showing this, not
    // "REGISTER CLEAR", tells anyone looking it's starting up, not broken.
    screenCentered2("STARTING", "UP", C_ACCENT, C_DIM);
  } else if (nowMs - lastFrameMs > DATA_TIMEOUT_MS) {
    // Had data, then lost it (Pi down, USB unplugged). Mock wording: WAITING.
    screenCentered2("WAITING", "for data", C_ACCENT, C_DIM);
  } else if (gClosed && shipCount == 0) {
    // Mock's SEAWAY CLOSED state (only when closed AND nothing to show).
    centeredLine("SEAWAY", PANEL_H / 2 - 16, C_ACCENT);
    centeredLine("CLOSED", PANEL_H / 2 - 4,  C_NAME);
    centeredLine("reopens March", PANEL_H / 2 + 8, C_DIM);
  } else {
    // Mock cycle: boardMs + (#named * DETAIL_MS) + (Emmett slot if fresh fix).
    int namedCount = 0;
    for (int i = 0; i < shipCount; i++) if (isNamed(ships[i])) namedCount++;
    uint32_t boardMs  = boardCycleMs(shipCount);
    uint32_t emmettMs = haveEmmett ? EMMETT_MS : 0;
    uint32_t cycle    = boardMs + (uint32_t)namedCount * DETAIL_MS + emmettMs;
    if (cycle == 0) cycle = boardMs ? boardMs : 1;
    uint32_t p = nowMs % cycle;

    if (p < boardMs && (shipCount > 0 || !haveEmmett)) {
      drawBoard(nowMs);
    } else if (p < boardMs + (uint32_t)namedCount * DETAIL_MS) {
      if (namedCount == 0) {
        drawBoard(nowMs);
      } else {
        int slot = (p - boardMs) / DETAIL_MS;        // which named ship
        slot %= namedCount;
        // map slot -> index of the slot-th named ship
        int idx = -1, seen = 0;
        for (int i = 0; i < shipCount; i++) {
          if (isNamed(ships[i])) { if (seen == slot) { idx = i; break; } seen++; }
        }
        if (idx >= 0) drawDetail(idx); else drawBoard(nowMs);
      }
    } else if (haveEmmett) {
      drawEmmett();
    } else {
      drawBoard(nowMs);
    }
  }

  matrix.show();          // REQUIRED: copies the back buffer to the panel.

  // Liveness heartbeat: once per second, emit a line the Pi can read to confirm
  // the render loop is actually running. This is the ONLY signal that catches a
  // wedged firmware -- if loop() hangs, the panel keeps its last image and the
  // USB peripheral stays enumerated, so the Pi's [push] writes still "succeed"
  // and the push-side watchdog sees nothing wrong. A stalled heartbeat is the
  // tell. free_heap rides along so a slow heap leak (String churn over hours)
  // shows up as a falling number before it ever causes a hang. Starts with '#'
  // so it can never be mistaken for a frame (frames start with '{').
  if (nowMs - lastHeartbeatMs >= 1000) {
    lastHeartbeatMs = nowMs;
    Serial.print("#hb free_heap=");
    Serial.print((unsigned long)ESP.getFreeHeap());
    Serial.print(" uptime=");
    Serial.println((unsigned long)(nowMs / 1000));
  }

  // Keep draining the serial RX buffer during the inter-frame wait instead of
  // a blocking delay(33). The S3's USB CDC receive buffer is small (~256 B);
  // at 115200 it fills in ~22ms, so a single 33ms blocking delay could let an
  // in-flight frame overflow and drop bytes -> corrupted JSON -> parse fail ->
  // "PI OFFLINE" even with small frames. Pumping continuously prevents that.
  uint32_t until = millis() + 33;
  while ((int32_t)(until - millis()) > 0) {
    pumpSerial();
    delay(1);
  }
}
