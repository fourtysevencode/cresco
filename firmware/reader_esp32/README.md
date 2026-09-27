# Cresco NFC reader (ESP32)

A canteen/bookstore payment terminal. The cashier types the amount, the student taps their card and
the reader sends an HMAC-signed request to the Cresco API. **No money is stored on the card.**

## Parts

| Part | Notes |
|---|---|
| ESP32 dev board (WROOM) | On WROVER boards GPIO16/17 are used by PSRAM, so move the keypad columns |
| MFRC522 RFID module (13.56 MHz) | 3.3 V only |
| NTAG213 / NTAG215 stickers or cards | MIFARE Classic cards are **not** supported (no NDEF) |
| 4x4 membrane keypad | |
| 16x2 LCD with I2C backpack | Address 0x27 (change in the sketch if yours is 0x3F) |
| Active buzzer | |

## Wiring

| MFRC522 | ESP32 |
|---|---|
| SDA/SS | GPIO 5 |
| SCK | GPIO 18 |
| MOSI | GPIO 23 |
| MISO | GPIO 19 |
| RST | GPIO 4 |
| 3.3V / GND | 3V3 / GND |

| Other | ESP32 |
|---|---|
| LCD SDA / SCL | GPIO 21 / GPIO 22 (5 V to VCC) |
| Keypad rows R1–R4 | GPIO 13, 14, 26, 25 |
| Keypad cols C1–C4 | GPIO 33, 32, 16, 17 |
| Buzzer + | GPIO 15 |

## Setup

1. Arduino IDE → Boards Manager: install **esp32** by Espressif. Board: *ESP32 Dev Module*.
2. Library Manager: install **MFRC522**, **Keypad** (Mark Stanley), **LiquidCrystal I2C** (Frank de Brabander) and **ArduinoJson** (v7).
3. Register the reader: `POST /v1/admin/terminals` (or run `python -m app.cli seed-demo`). You get a `terminal_id` and a `secret`.
4. Copy `config.h.example` to `config.h`, fill in WiFi, `API_BASE`, `TERMINAL_ID` and `TERMINAL_SECRET`, then upload.
5. On boot the LCD shows **Online** + the shop name if the reader can reach the API and its secret is right.

## Using it

| Key | Action |
|---|---|
| `0`–`9` then `#` | Charge that many rupees; then the student taps |
| `A` | Claim a leaderboard prize (e.g. free ice cream); then the student taps |
| `*` | Clear / cancel |
| `D` | Backspace |
| `C` | Check connection |

## How a tap is secured

- The tag holds `CRESCO1|<card_token>|<first name>`. The token is random (128-bit), and the server also checks the tag's hardware UID, which is bound on the first tap.
- Every request carries `X-Terminal-Id`, `X-Timestamp`, `X-Nonce` and `X-Signature = hex(HMAC-SHA256(secret, "<timestamp>.<nonce>.<body>"))`. The server rejects:
  - bad signatures
  - clocks more than 60 s off (the reader syncs over NTP)
  - reused nonces
- Every transaction has an `idempotency_key`: a counter kept in flash. If WiFi drops mid-request, the reader retries with the same key and the student is charged once.
- Use `https://` with `ROOT_CA` set in production. Without `ROOT_CA`, HTTPS certificates are not checked, which is fine only for bench testing.

`scripts/sim_terminal.py` implements the same protocol in Python, so you can test the backend without hardware.
