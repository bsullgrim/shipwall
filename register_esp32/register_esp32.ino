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

// MatrixPortal ESP32-S3 HUB75 pin mapping (from Adafruit's Protomatter
// examples). addrPins has FIVE entries -- A,B,C,D,E -- because this is a
// 64-pixel-tall 1/32-scan panel using 5-address multiplexing. That fifth
// (E) pin is what the panel needs and what the DMA library wasn't driving.
static uint8_t rgbPins[]  = {42, 41, 40, 38, 39, 37};
static uint8_t addrPins[] = {45, 36, 48, 35, 21};   // A B C D E  (5 = 64 tall)
static uint8_t clockPin   = 2;
static uint8_t latchPin   = 47;
static uint8_t oePin      = 14;

// width, bit-depth(6), chain(1), rgbPins, 5 addr pins, clk, latch, oe, double-buffer
Adafruit_Protomatter matrix(
  PANEL_W, 6, 1, rgbPins, 5, addrPins, clockPin, latchPin, oePin, true);

// ---- Serial frame reader ----------------------------------------------------
// Worst case ~30 ships + emmett ~= 6-8 KB/line. Size generously; a truncated
// line is the #1 silent failure. Reject overlong lines rather than overflow.
static const size_t FRAME_BUF_MAX = 12288;     // 12 KB line ceiling
static char     lineBuf[FRAME_BUF_MAX];
static size_t   lineLen = 0;

// ArduinoJson 7: JsonDocument grows elastically on the heap as the frame is
// parsed -- no fixed capacity to size. (v6's DynamicJsonDocument(capacity) is
// deprecated in v7.) The frame buffer above still bounds the raw line length.

// ---- Timing -----------------------------------------------------------------
const uint32_t DETAIL_DWELL_MS  = 6000;   // each detail card
const uint32_t MODE_SWAP_MS     = 18000;  // board <-> detail cycle
const uint32_t DATA_TIMEOUT_MS  = 60000;  // no complete frame -> "waiting"

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

uint8_t  gBright   = 128;
bool     gClosed   = false;
float    gHours    = 18.0f;
float    gHome     = 0.5f;
uint32_t lastFrameMs = 0;
bool     gotFirstFrame = false;     // false until the first complete frame parses

// ---- Colors -----------------------------------------------------------------
uint16_t C_NAME, C_LABEL, C_VALUE, C_DIM, C_UP, C_DOWN, C_ACCENT;

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

// D = downbound (toward sea) filled down-triangle; U = up; M = square; ? = dot.
void drawDirGlyph(char dir, int x, int y) {
  uint16_t col = (dir == 'D') ? C_DOWN : (dir == 'U') ? C_UP : C_DIM;
  if (dir == 'D') {
    for (int r = 0; r < 4; r++)
      for (int c = r; c <= 4 - r; c++) matrix.drawPixel(x + c, y + r, col);
  } else if (dir == 'U') {
    for (int r = 0; r < 4; r++)
      for (int c = 3 - r; c <= 1 + r; c++) matrix.drawPixel(x + c, y + (3 - r), col);
  } else if (dir == 'M') {
    matrix.fillRect(x + 1, y + 1, 3, 3, col);
  } else {
    matrix.drawPixel(x + 2, y + 2, col);
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
  if (err) return false;                  // bad/truncated line: keep last frame

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
void pumpSerial() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n') {
      if (lineLen > 0 && lineLen < FRAME_BUF_MAX) applyFrame(lineBuf, lineLen);
      lineLen = 0;                          // also resets after an overlong drop
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

void drawBoard() {
  textAt("RECENT-SIGHTINGS REGISTER", 2, 0, C_LABEL);
  int rowH = 9, top = 10, maxRows = (PANEL_H - top) / rowH;
  for (int i = 0; i < shipCount && i < maxRows; i++) {
    Ship& s = ships[i];
    int y = top + i * rowH;
    char keybuf[16]; s.op.toCharArray(keybuf, sizeof(keybuf));
    drawMini(miniForKey(keybuf), 1, y);           // 8x8 funnel chip
    textAt(s.code.c_str(), 11, y, C_ACCENT);
    drawDirGlyph(s.dir, 32, y);
    String nm = s.name; if (nm.length() > 14) nm = nm.substring(0, 14);
    textAt(nm.c_str(), 40, y, C_NAME);
    int16_t x1, y1; uint16_t w, h;
    matrix.getTextBounds(s.age.c_str(), 0, 0, &x1, &y1, &w, &h);
    textAt(s.age.c_str(), PANEL_W - w - 1, y, C_DIM);
  }
}

void labelValue(const char* label, const String& val, int& y) {
  if (val.length() == 0) return;            // null/empty -> skip the line
  textAt(label, 66, y, C_LABEL);
  textAt(val.c_str(), 92, y, C_VALUE);
  y += 9;
}

void drawDetail(int idx) {
  if (idx < 0 || idx >= shipCount) { drawBoard(); return; }
  Ship& s = ships[idx];
  char keybuf[16]; s.op.toCharArray(keybuf, sizeof(keybuf));
  drawSpriteScaled(spriteForKey(keybuf), 12, 4, 40);   // big funnel left
  String nm = s.name; if (nm.length() > 10) nm = nm.substring(0, 10);
  textAt(nm.c_str(), 4, 50, C_NAME);
  drawDirGlyph(s.dir, 54, 52);

  int y = 2;
  textAt(s.code.c_str(), 66, y, C_ACCENT); y += 9;
  if (s.flag.length())     labelValue("FLAG", s.flag, y);
  if (s.length > 0) { char b[16]; snprintf(b, sizeof(b), "%dx%dm", s.length, s.beam > 0 ? s.beam : 0); labelValue("DIM", String(b), y); }
  if (s.hasDraught) { char b[12]; snprintf(b, sizeof(b), "%.1fm", s.draught); labelValue("DRFT", String(b), y); }
  if (s.navstat.length())  labelValue("NAV", s.navstat, y);
  if (s.dest.length())     labelValue("DEST", s.dest, y);
  if (s.eta.length())      labelValue("ETA", s.eta, y);
}

void drawEmmett() {
  textAt("WHERE'S EMMETT", 2, 0, C_ACCENT);
  String nm = emmett.name; if (nm.length() > 20) nm = nm.substring(0, 20);
  textAt(nm.c_str(), 2, 12, C_NAME);
  char buf[24];
  int y = 24;
  snprintf(buf, sizeof(buf), "%.3f,%.3f", emmett.lat, emmett.lon);
  textAt(buf, 2, y, C_VALUE); y += 10;
  if (emmett.hasSog) { snprintf(buf, sizeof(buf), "SOG %.1f", emmett.sog); textAt(buf, 2, y, C_VALUE); y += 10; }
  if (emmett.dest.length()) { textAt("DEST", 2, y, C_LABEL); textAt(emmett.dest.c_str(), 30, y, C_VALUE); y += 10; }
  snprintf(buf, sizeof(buf), "%dm ago", emmett.ageSecs / 60);
  textAt(buf, 2, y, C_DIM);
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

  C_NAME   = matrix.color565(255, 255, 255);
  C_LABEL  = matrix.color565(120, 140, 170);
  C_VALUE  = matrix.color565(210, 210, 210);
  C_DIM    = matrix.color565(90, 90, 90);
  C_UP     = matrix.color565(80, 200, 120);
  C_DOWN   = matrix.color565(230, 150, 60);
  C_ACCENT = matrix.color565(255, 210, 90);
}

void loop() {
  pumpSerial();
  uint32_t nowMs = millis();

  matrix.fillScreen(0);

  if (!gotFirstFrame) {
    // Cold boot: panel is up but the Pi hasn't sent a frame yet. The Pi takes
    // 30-60s to boot Linux, start the service, connect to AISStream and
    // warm-start, so on a shared-power reboot this screen is expected for up to
    // a minute. Showing it (not "REGISTER CLEAR") tells anyone looking at the
    // box it's starting up, not empty or broken.
    screenCentered("STARTING UP", C_DIM);
  } else if (nowMs - lastFrameMs > DATA_TIMEOUT_MS) {
    // We HAD data and then lost it (Pi crashed, USB unplugged, service down).
    screenCentered("PI OFFLINE", C_DOWN);
  } else if (gClosed) {
    screenCentered("SEAWAY CLOSED", C_DOWN);
  } else if (haveEmmett && ((nowMs / MODE_SWAP_MS) % 3 == 2)) {
    drawEmmett();                                  // Emmett gets one slot in the cycle
  } else if (shipCount == 0) {
    screenCentered("REGISTER CLEAR", C_DIM);
  } else if ((nowMs / MODE_SWAP_MS) % 2 == 0) {
    drawBoard();
  } else {
    int idx = (nowMs / DETAIL_DWELL_MS) % shipCount;
    drawDetail(idx);
  }

  matrix.show();          // REQUIRED: copies the back buffer to the panel.
  delay(33);              // ~30 fps; serial pumped each loop
}
