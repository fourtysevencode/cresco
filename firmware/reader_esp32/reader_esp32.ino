// Cresco NFC reader — ESP32 + MFRC522 + 4x4 keypad + 16x2 I2C LCD + buzzer.
//
// Pay:     type the amount in rupees, press #, student taps card.
// Reward:  press A, student taps card to claim a leaderboard prize.
// Keys:    * = clear, D = backspace, C = check connection.
//
// The tag holds only an NDEF text record "CRESCO1|<card_token>|<first name>". No money is stored on
// the card: every tap is a signed request to the Cresco API, which owns the balance.
//
// Libraries (Arduino Library Manager): MFRC522 (GithubCommunity), Keypad (Mark Stanley),
// LiquidCrystal I2C (Frank de Brabander), ArduinoJson v7. Board: "ESP32 Dev Module".

#include <Arduino.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <Keypad.h>
#include <LiquidCrystal_I2C.h>
#include <MFRC522.h>
#include <Preferences.h>
#include <SPI.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <mbedtls/md.h>
#include <time.h>

#include "config.h"

// ---- Pins (see README.md for wiring) ----------------------------------------------------------
constexpr uint8_t PIN_RC522_SS = 5;   // SPI: SCK 18, MISO 19, MOSI 23
constexpr uint8_t PIN_RC522_RST = 4;
constexpr uint8_t PIN_BUZZER = 15;
byte KEYPAD_ROWS[4] = {13, 14, 26, 25};
byte KEYPAD_COLS[4] = {33, 32, 16, 17};
char KEYS[4][4] = {
    {'1', '2', '3', 'A'},
    {'4', '5', '6', 'B'},
    {'7', '8', '9', 'C'},
    {'*', '0', '#', 'D'},
};

constexpr uint32_t TAP_TIMEOUT_MS = 20000;
constexpr uint32_t HTTP_TIMEOUT_MS = 8000;
constexpr int HTTP_ATTEMPTS = 3;
constexpr uint32_t MAX_AMOUNT_RUPEES = 5000;

MFRC522 rfid(PIN_RC522_SS, PIN_RC522_RST);
LiquidCrystal_I2C lcd(0x27, 16, 2);
Keypad keypad(makeKeymap(KEYS), KEYPAD_ROWS, KEYPAD_COLS, 4, 4);
Preferences prefs;

String amountInput;

// ---- Small helpers ----------------------------------------------------------------------------

void show(const String &line1, const String &line2 = "") {
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print(line1.substring(0, 16));
  lcd.setCursor(0, 1);
  lcd.print(line2.substring(0, 16));
}

void beep(int times, int ms) {
  for (int i = 0; i < times; i++) {
    digitalWrite(PIN_BUZZER, HIGH);
    delay(ms);
    digitalWrite(PIN_BUZZER, LOW);
    delay(80);
  }
}

String rupees(long paise) {
  char buf[16];
  snprintf(buf, sizeof(buf), "Rs %ld.%02ld", paise / 100, labs(paise % 100));
  return String(buf);
}

String toHex(const uint8_t *data, size_t len) {
  static const char *digits = "0123456789abcdef";
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

// Idempotency key: a counter persisted in flash, incremented before each new transaction, so a
// retry of the same transaction reuses the key but a reboot never does.
String nextIdempotencyKey() {
  uint32_t seq = prefs.getUInt("seq", 0) + 1;
  prefs.putUInt("seq", seq);
  return String(seq);
}

// ---- Networking ----------------------------------------------------------------------------------

void connectWifi() {
  if (WiFi.status() == WL_CONNECTED) return;
  show("Connecting WiFi", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) delay(250);
}

void syncClock() {
  configTime(0, 0, "pool.ntp.org", "time.google.com");  // UTC epoch is all we need
  show("Syncing clock");
  for (int i = 0; i < 40 && time(nullptr) < 1700000000; i++) delay(250);
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
  return toHex(mac, sizeof(mac));
}

// POSTs a signed request. Retries network failures with the SAME body (and so the same
// idempotency key) but a fresh timestamp/nonce. Returns the HTTP status, or -1 if unreachable.
int signedPost(const String &path, const String &body, JsonDocument &response) {
  String url = String(API_BASE) + "/v1/terminal/" + path;
  bool https = url.startsWith("https://");
  for (int attempt = 1; attempt <= HTTP_ATTEMPTS; attempt++) {
    connectWifi();
    WiFiClientSecure secureClient;
    WiFiClient plainClient;
    if (https) {
#ifdef ROOT_CA
      secureClient.setCACert(ROOT_CA);
#else
      secureClient.setInsecure();  // DEV ONLY: define ROOT_CA in config.h for production
#endif
    }
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
      deserializeJson(response, http.getString());
      http.end();
      return code;
    }
    http.end();
    show("Retrying...", String(attempt) + "/" + String(HTTP_ATTEMPTS));
    delay(500 * attempt);
  }
  return -1;
}

// ---- NFC ---------------------------------------------------------------------------------------

struct Tap {
  String cardToken;
  String name;
  String uid;
};

// Reads NTAG21x pages 4.. and extracts the NDEF Text record "CRESCO1|<token>|<name>".
bool readTag(Tap &tap) {
  uint8_t mem[96];
  for (uint8_t page = 4, off = 0; off < sizeof(mem); page += 4, off += 16) {
    uint8_t buf[18];
    uint8_t size = sizeof(buf);
    if (rfid.MIFARE_Read(page, buf, &size) != MFRC522::STATUS_OK) return false;
    memcpy(mem + off, buf, 16);
  }
  // Walk the TLVs to the NDEF message (type 0x03).
  size_t i = 0;
  while (i < sizeof(mem) && mem[i] != 0x03) {
    if (mem[i] == 0x00) { i++; continue; }        // NULL TLV
    if (mem[i] == 0xFE || i + 1 >= sizeof(mem)) return false;  // terminator
    i += 2 + mem[i + 1];                            // skip lock/memory control TLVs
  }
  if (i + 2 >= sizeof(mem)) return false;
  size_t p = i + 2;                                  // start of the NDEF record (short TLV length)
  uint8_t header = mem[p];
  bool shortRecord = header & 0x10;
  bool hasId = header & 0x08;
  uint8_t typeLen = mem[p + 1];
  if (!shortRecord) return false;                   // our payload is always < 256 bytes
  uint8_t payloadLen = mem[p + 2];
  uint8_t idLen = hasId ? mem[p + 3] : 0;
  size_t typeStart = p + 3 + (hasId ? 1 : 0);
  if (typeLen != 1 || mem[typeStart] != 'T') return false;
  size_t payload = typeStart + typeLen + idLen;
  if (payload + payloadLen > sizeof(mem)) return false;
  uint8_t langLen = mem[payload] & 0x3f;
  String text;
  for (size_t k = payload + 1 + langLen; k < payload + payloadLen; k++) text += (char)mem[k];

  int bar1 = text.indexOf('|');
  int bar2 = text.indexOf('|', bar1 + 1);
  if (!text.startsWith("CRESCO1|") || bar2 < 0) return false;
  tap.cardToken = text.substring(bar1 + 1, bar2);
  tap.name = text.substring(bar2 + 1);
  tap.uid = "";
  for (byte k = 0; k < rfid.uid.size; k++) {
    char hex[3];
    snprintf(hex, sizeof(hex), "%02X", rfid.uid.uidByte[k]);
    tap.uid += hex;
  }
  return true;
}

bool waitForTap(Tap &tap) {
  uint32_t start = millis();
  while (millis() - start < TAP_TIMEOUT_MS) {
    if (keypad.getKey() == '*') return false;  // cancel
    if (rfid.PICC_IsNewCardPresent() && rfid.PICC_ReadCardSerial()) {
      bool ok = readTag(tap);
      rfid.PICC_HaltA();
      if (ok) return true;
      show("Card not Cresco", "Try again");
      beep(2, 80);
    }
    delay(50);
  }
  return false;
}

// ---- Transactions -------------------------------------------------------------------------------

String reasonText(const char *reason) {
  String r = reason ? reason : "";
  if (r == "insufficient_balance") return "Low balance";
  if (r == "daily_limit") return "Daily limit hit";
  if (r == "card_blocked") return "Card blocked";
  if (r == "unknown_card" || r == "tag_mismatch") return "Card not valid";
  if (r == "wrong_school") return "Wrong school";
  if (r == "no_reward") return "No reward";
  return r.length() ? r : "Error";
}

void pay(uint32_t amountRupees) {
  show("Pay " + rupees(amountRupees * 100L), "Tap card (*=no)");
  Tap tap;
  if (!waitForTap(tap)) {
    show("Cancelled");
    delay(1000);
    return;
  }
  show("Processing...", tap.name);

  JsonDocument req;
  req["card_token"] = tap.cardToken;
  req["tag_uid"] = tap.uid;
  req["amount_paise"] = amountRupees * 100L;
  req["idempotency_key"] = nextIdempotencyKey();
  String body;
  serializeJson(req, body);

  JsonDocument res;
  int code = signedPost("charge", body, res);
  if (code == 200 && res["status"] == "approved") {
    show("PAID " + String((const char *)(res["name"] | "")), "Bal " + rupees(res["balance_paise"] | 0L));
    beep(1, 150);
  } else if (code == 200) {
    String line2 = reasonText(res["reason"]);
    if (!res["balance_paise"].isNull()) line2 += " " + rupees(res["balance_paise"].as<long>());
    show("DECLINED", line2);
    beep(3, 120);
  } else {
    show("Server error", code < 0 ? "No network" : "HTTP " + String(code));
    beep(3, 120);
  }
  delay(3000);
}

void redeemReward() {
  show("Reward claim", "Tap card (*=no)");
  Tap tap;
  if (!waitForTap(tap)) return;
  show("Checking...", tap.name);
  JsonDocument req;
  req["card_token"] = tap.cardToken;
  req["idempotency_key"] = nextIdempotencyKey();
  String body;
  serializeJson(req, body);
  JsonDocument res;
  int code = signedPost("rewards/redeem", body, res);
  if (code == 200 && res["status"] == "approved") {
    show("GIVE: " + String((const char *)(res["reward"] | "")), String((const char *)(res["name"] | "")));
    beep(2, 150);
  } else {
    show("NO REWARD", code == 200 ? reasonText(res["reason"]) : "Server error");
    beep(3, 120);
  }
  delay(4000);
}

void checkConnection() {
  JsonDocument res;
  int code = signedPost("heartbeat", "{}", res);
  if (code == 200) show("Online", String((const char *)(res["merchant"] | "")));
  else if (code == 401) show("Auth failed", "Check secret/clk");
  else show("Offline", code < 0 ? "No network" : "HTTP " + String(code));
  delay(2500);
}

void showIdle() {
  show("Amount (Rs):", amountInput.length() ? amountInput : "_  #=pay A=gift");
}

// ---- Arduino entry points ------------------------------------------------------------------------

void setup() {
  pinMode(PIN_BUZZER, OUTPUT);
  lcd.init();
  lcd.backlight();
  SPI.begin();
  rfid.PCD_Init();
  prefs.begin("cresco", false);
  connectWifi();
  syncClock();
  checkConnection();
  showIdle();
}

void loop() {
  char key = keypad.getKey();
  if (!key) return;
  if (key >= '0' && key <= '9' && amountInput.length() < 4) {
    if (!(amountInput.isEmpty() && key == '0')) amountInput += key;
  } else if (key == 'D' && amountInput.length()) {
    amountInput.remove(amountInput.length() - 1);
  } else if (key == '*') {
    amountInput = "";
  } else if (key == '#' && amountInput.length()) {
    uint32_t amount = amountInput.toInt();
    amountInput = "";
    if (amount > 0 && amount <= MAX_AMOUNT_RUPEES) pay(amount);
  } else if (key == 'A') {
    amountInput = "";
    redeemReward();
  } else if (key == 'C') {
    checkConnection();
  }
  showIdle();
}
