// Cresco NFC reader — ESP32 + PN532 (I2C).
//
// The reader only reads each card's serial number (UID) and sends it to the Cresco API. It never
// reads or writes card data and holds no money. The server decides what a tap means: if the cashier
// has set a charge on this reader in the dashboard, the tap pays; otherwise it identifies the
// student, or reports an unregistered card (which the admin can then register from the dashboard).
//
// A 1.8" 128x160 RGB TFT (ST7735, SPI) shows "Waiting" when idle, "Sending" while a tap is being
// sent, then a green tick (or a red cross) with the result.
//
// Libraries (Arduino Library Manager): "Adafruit PN532", "ArduinoJson" (v7),
// "Adafruit ST7735 and ST7789 Library", "Adafruit GFX Library".
// Board: your ESP32 board (I2C on SDA 8 / SCL 9, as wired).

#include <Arduino.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <Wire.h>
#include <Adafruit_PN532.h>
#include <Adafruit_GFX.h>
#include <Adafruit_ST7735.h>
#include <SPI.h>
#include <mbedtls/md.h>
#include <time.h>

#include "config.h"

#define PN532_IRQ 4    // placeholder; leave unconnected
#define PN532_RESET 5  // placeholder; leave unconnected
#define I2C_SDA 8
#define I2C_SCL 9

// ST7735 display (SPI). Override any of these in config.h. Set TFT_RST to -1 if RST is tied to 3V3.
#ifndef TFT_SCK
#define TFT_SCK 6    // display SCK / SCL / CLK
#endif
#ifndef TFT_MOSI
#define TFT_MOSI 7   // display SDA / MOSI / DIN
#endif
#ifndef TFT_CS
#define TFT_CS 10
#endif
#ifndef TFT_DC
#define TFT_DC 3     // display DC / A0 / RS
#endif
#ifndef TFT_RST
#define TFT_RST 1
#endif
#ifndef TFT_TAB
#define TFT_TAB INITR_BLACKTAB  // try INITR_GREENTAB or INITR_REDTAB if colours or edges look wrong
#endif

constexpr uint32_t SAME_CARD_IGNORE_MS = 2000;  // a card resting on the reader counts as one tap
constexpr uint32_t HEARTBEAT_MS = 60000;        // lets the dashboard show the reader as online
constexpr uint32_t HTTP_TIMEOUT_MS = 10000;
constexpr int HTTP_ATTEMPTS = 3;
constexpr uint32_t RESULT_SHOW_MS = 2500;       // how long the tick / cross stays before "Waiting"

Adafruit_PN532 nfc(PN532_IRQ, PN532_RESET, &Wire);
Adafruit_ST7735 tft(&SPI, TFT_CS, TFT_DC, TFT_RST);
Preferences prefs;

String lastUid;
uint32_t lastUidAt = 0;
uint32_t lastHeartbeatAt = 0;
uint32_t resultShownAt = 0;  // 0 while the display isn't showing a result

// ---- Display ------------------------------------------------------------------------------------

void centerText(const String &text, int16_t y, uint8_t size, uint16_t color) {
  int16_t x1, y1;
  uint16_t w, h;
  tft.setTextSize(size);
  tft.setTextColor(color);
  tft.setTextWrap(false);
  tft.getTextBounds(text, 0, 0, &x1, &y1, &w, &h);
  tft.setCursor((tft.width() - (int16_t)w) / 2 - x1, y);
  tft.print(text);
}

// A line drawn `thickness` pixels tall, for the tick and cross.
void thickLine(int16_t x0, int16_t y0, int16_t x1, int16_t y1, int thickness, uint16_t color) {
  for (int d = -thickness / 2; d <= thickness / 2; d++) {
    tft.drawLine(x0, y0 + d, x1, y1 + d, color);
  }
}

void showMessage(const String &title, const String &subtitle) {
  tft.fillScreen(ST77XX_BLACK);
  centerText(title, 64, 2, ST77XX_WHITE);
  centerText(subtitle, 92, 1, 0x8410);  // grey
  resultShownAt = 0;
}

void showWaiting() { showMessage("Waiting", "Tap a card"); }

void showResult(bool ok, const String &caption) {
  const int16_t cx = 64, cy = 64, r = 42;
  tft.fillScreen(ST77XX_BLACK);
  tft.fillCircle(cx, cy, r, ok ? ST77XX_GREEN : ST77XX_RED);
  if (ok) {
    thickLine(cx - 22, cy + 2, cx - 7, cy + 17, 7, ST77XX_WHITE);
    thickLine(cx - 7, cy + 17, cx + 23, cy - 15, 7, ST77XX_WHITE);
  } else {
    thickLine(cx - 18, cy - 18, cx + 18, cy + 18, 7, ST77XX_WHITE);
    thickLine(cx - 18, cy + 18, cx + 18, cy - 18, 7, ST77XX_WHITE);
  }
  centerText(caption.substring(0, 21), 124, 1, ST77XX_WHITE);  // 21 chars fit across at size 1
  resultShownAt = millis() | 1;  // never 0, which means "no result showing"
}

// ---- Feedback -----------------------------------------------------------------------------------

void setPin(int pin, bool on) {
  if (pin >= 0) digitalWrite(pin, on ? HIGH : LOW);
}

void signal(bool ok, const String &caption) {
  showResult(ok, caption);
  int led = ok ? PIN_LED_OK : PIN_LED_ERROR;
  int beeps = ok ? 1 : 3;
  for (int i = 0; i < beeps; i++) {
    setPin(led, true);
    setPin(PIN_BUZZER, true);
    delay(ok ? 150 : 90);
    setPin(PIN_BUZZER, false);
    delay(90);
  }
  delay(ok ? 600 : 300);
  setPin(led, false);
}

// ---- Helpers ------------------------------------------------------------------------------------

String toHex(const uint8_t *data, size_t len) {
  static const char *digits = "0123456789ABCDEF";
  String out;
  out.reserve(len * 2);
  for (size_t i = 0; i < len; i++) {
    out += digits[data[i] >> 4];
    out += digits[data[i] & 0x0f];
  }
  return out;
}

String randomNonce() {
  uint8_t bytes[8];
  for (int i = 0; i < 8; i += 4) {
    uint32_t r = esp_random();
    memcpy(bytes + i, &r, 4);
  }
  return toHex(bytes, 8);
}

// A counter kept in flash, so scan ids never repeat, even across reboots. A retry of the same tap
// reuses its scan id, which the server uses to process each tap exactly once.
String nextScanId() {
  uint32_t seq = prefs.getUInt("seq", 0) + 1;
  prefs.putUInt("seq", seq);
  return String(seq);
}

// ---- Networking ----------------------------------------------------------------------------------

void connectWifi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.printf("Connecting to WiFi %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) {
    delay(250);
    Serial.print(".");
  }
  Serial.println(WiFi.status() == WL_CONNECTED ? " connected" : " FAILED");
}

void syncClock() {
  configTime(0, 0, "pool.ntp.org", "time.google.com");  // requests are signed with UTC epoch seconds
  Serial.print("Syncing clock");
  for (int i = 0; i < 40 && time(nullptr) < 1700000000; i++) {
    delay(250);
    Serial.print(".");
  }
  Serial.println(time(nullptr) >= 1700000000 ? " ok" : " FAILED (requests will be rejected)");
}

// X-Signature = hex(HMAC-SHA256(secret, "<timestamp>.<nonce>.<body>"))
String sign(const String &timestamp, const String &nonce, const String &body) {
  String message = timestamp + "." + nonce + "." + body;
  uint8_t mac[32];
  mbedtls_md_context_t ctx;
  mbedtls_md_init(&ctx);
  mbedtls_md_setup(&ctx, mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), 1);
  mbedtls_md_hmac_starts(&ctx, (const unsigned char *)TERMINAL_SECRET, strlen(TERMINAL_SECRET));
  mbedtls_md_hmac_update(&ctx, (const unsigned char *)message.c_str(), message.length());
  mbedtls_md_hmac_finish(&ctx, mac);
  mbedtls_md_free(&ctx);
  String hex = toHex(mac, sizeof(mac));
  hex.toLowerCase();
  return hex;
}

// POSTs a signed request, retrying network failures with the same body (same scan id) but a fresh
// timestamp and nonce. Returns the HTTP status, or -1 if the server couldn't be reached.
int signedPost(const String &path, const String &body, JsonDocument &response) {
  String url = String(API_BASE) + "/v1/terminal/" + path;
  bool https = url.startsWith("https://");
  for (int attempt = 1; attempt <= HTTP_ATTEMPTS; attempt++) {
    connectWifi();
    WiFiClientSecure secureClient;
    WiFiClient plainClient;
    if (https) secureClient.setInsecure();  // demo: no certificate pinning (requests are still HMAC-signed)
    HTTPClient http;
    http.begin(https ? static_cast<WiFiClient &>(secureClient) : plainClient, url);
    http.setTimeout(HTTP_TIMEOUT_MS);
    String ts = String((long)time(nullptr));
    String nonce = randomNonce();
    http.addHeader("Content-Type", "application/json");
    http.addHeader("X-Terminal-Id", TERMINAL_ID);
    http.addHeader("X-Timestamp", ts);
    http.addHeader("X-Nonce", nonce);
    http.addHeader("X-Signature", sign(ts, nonce, body));
    int code = http.POST(body);
    if (code > 0) {
      String text = http.getString();
      http.end();
      deserializeJson(response, text);
      if (code != 200) Serial.printf("HTTP %d: %s\n", code, text.c_str());
      return code;
    }
    http.end();
    Serial.printf("Network error (attempt %d/%d)\n", attempt, HTTP_ATTEMPTS);
    delay(400 * attempt);
  }
  return -1;
}

void heartbeat() {
  JsonDocument res;
  int code = signedPost("heartbeat", "{}", res);
  if (code == 200) Serial.printf("Online: %s\n", (const char *)(res["merchant"] | ""));
  else if (code == 401) Serial.println("Rejected (401): check TERMINAL_ID / TERMINAL_SECRET and the clock");
  else if (code >= 300 && code < 400) Serial.println("Redirected: set API_BASE to the https:// address with no trailing slash");
  else if (code > 0) Serial.printf("Server error (HTTP %d)\n", code);
  else Serial.println("Server unreachable: check the WiFi has internet access");
  lastHeartbeatAt = millis();
}

// ---- Taps ----------------------------------------------------------------------------------------

String rupees(long paise) {
  char buf[20];
  snprintf(buf, sizeof(buf), "Rs %ld.%02ld", paise / 100, labs(paise % 100));
  return String(buf);
}

void sendScan(const String &uid) {
  JsonDocument req;
  req["tag_uid"] = uid;
  req["scan_id"] = nextScanId();
  String body;
  serializeJson(req, body);

  JsonDocument res;
  int code = signedPost("scan", body, res);
  if (code != 200) {
    signal(false, code > 0 ? "Server error" : "No connection");
    return;
  }
  String result = res["result"] | "";
  String name = res["name"] | "";
  if (result == "approved") {
    if (!res["reward"].isNull()) Serial.printf("REWARD for %s: %s\n", name.c_str(), (const char *)res["reward"]);
    else Serial.printf("PAID %s by %s, balance %s\n", rupees(res["amount_paise"] | 0L).c_str(), name.c_str(), rupees(res["balance_paise"] | 0L).c_str());
    signal(true, name);
  } else if (result == "identified") {
    Serial.printf("Hello %s, balance %s\n", name.c_str(), rupees(res["balance_paise"] | 0L).c_str());
    signal(true, name);
  } else if (result == "declined") {
    Serial.printf("DECLINED %s: %s\n", name.c_str(), (const char *)(res["reason"] | ""));
    signal(false, "Declined");
  } else {
    Serial.printf("Unregistered card %s: register it in the dashboard\n", uid.c_str());
    signal(false, "Unregistered card");
  }
}

// ---- Arduino entry points ------------------------------------------------------------------------

void setup() {
  Serial.begin(115200);
  for (int pin : {PIN_LED_OK, PIN_LED_ERROR, PIN_BUZZER}) {
    if (pin >= 0) pinMode(pin, OUTPUT);
  }

  SPI.begin(TFT_SCK, -1, TFT_MOSI, TFT_CS);
  tft.initR(TFT_TAB);
  tft.setRotation(0);  // portrait, 128 wide x 160 tall
  showMessage("Starting", "Connecting...");

  Wire.begin(I2C_SDA, I2C_SCL);
  nfc.begin();
  if (!nfc.getFirmwareVersion()) {
    Serial.println("PN532 not found. Check wiring and I2C mode.");
    showMessage("Error", "NFC reader not found");
    while (true) delay(10);
  }
  nfc.SAMConfig();

  prefs.begin("cresco", false);
  connectWifi();
  syncClock();
  heartbeat();
  Serial.println("Tap an NFC card...");
  showWaiting();
}

void loop() {
  if (resultShownAt && millis() - resultShownAt > RESULT_SHOW_MS) showWaiting();
  if (millis() - lastHeartbeatAt > HEARTBEAT_MS) heartbeat();

  uint8_t uid[10];
  uint8_t uidLength = 0;
  // Short timeout so the loop keeps running (heartbeats) while no card is present.
  if (!nfc.readPassiveTargetID(PN532_MIFARE_ISO14443A, uid, &uidLength, 100)) return;

  String hex = toHex(uid, uidLength);
  if (hex == lastUid && millis() - lastUidAt < SAME_CARD_IGNORE_MS) {
    lastUidAt = millis();  // card still resting on the reader
    return;
  }
  lastUid = hex;
  lastUidAt = millis();
  Serial.printf("UID: %s\n", hex.c_str());
  showMessage("Sending", "Please wait...");
  sendScan(hex);
}
