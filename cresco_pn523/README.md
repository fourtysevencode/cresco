# Cresco NFC reader (ESP32 + PN532)

The reader does one thing: read an NFC card's **serial number (UID)** and send it to the Cresco API over WiFi. It doesn't read or write any data on the card, and no money is stored on the card. The server and dashboard decide what each tap means:

| On the dashboard | Student taps | Reader prints |
|---|---|---|
| Cashier set a charge (e.g. ₹40) on this reader | Wallet is debited | `PAID Rs 40.00 by Arun, balance Rs 115.00` |
| Cashier set "redeem prize" | Prize is handed out | `REWARD for Arun: Free ice cream…` |
| Nothing pending | Student is identified | `Hello Arun, balance Rs 155.00` |
| Any | Card isn't registered yet | `Unregistered card 04A1B2C3…`: the admin registers it from the dashboard |

## Wiring (I2C)

| PN532 | ESP32 |
|---|---|
| SDA | GPIO 8 |
| SCL | GPIO 9 |
| VCC / GND | 3V3 / GND |

Set the PN532's mode switches to **I2C**. LEDs and a buzzer are optional; set their pins in `config.h`.

## Display (1.8" 128x160 RGB TFT, ST7735, SPI)

| Display pin | ESP32 |
|---|---|
| VCC | 3V3 |
| GND | GND |
| SCK / SCL / CLK | GPIO 6 |
| SDA / MOSI / DIN | GPIO 7 |
| CS | GPIO 10 |
| DC / A0 / RS | GPIO 3 |
| RST / RES | GPIO 1 |
| LED / BL / BLK (backlight) | GPIO 21 |

The display shows **Waiting** while idle, **Sending** while a tap is being sent, then a green tick with the student's name (paid or identified) or a red cross with the reason (declined, unregistered card, no connection). After 2.5 s it goes back to **Waiting**.

- The pins are defaults in `cresco_pn523.ino`; to use other pins, `#define TFT_SCK`, `TFT_MOSI`, `TFT_CS`, `TFT_DC`, `TFT_RST` or `TFT_BL` in `config.h`. If RST or BL is wired to 3V3 instead, set `TFT_RST` / `TFT_BL` to `-1`.
- The backlight is driven from GPIO 21, so only one 3V3 pin is needed (for VCC). Share it with the PN532 through the breadboard's power rail.
- If the colours are swapped or there's a stripe of noise along one edge, add `#define TFT_TAB INITR_GREENTAB` (or `INITR_REDTAB`) to `config.h`.
- Leave the display's MISO/SDO pin (if it has one) and the SD-card pins unconnected.

## Setup

1. Arduino Library Manager: install **Adafruit PN532**, **ArduinoJson** (v7), **Adafruit ST7735 and ST7789 Library** and **Adafruit GFX Library**.
2. Register the reader: dashboard → *Admin → Readers → Add reader* (or `POST /v1/admin/terminals`). Copy the `terminal_id` and `secret`; the secret is shown only once.
3. Copy `config.h.example` to `config.h` and fill in WiFi, `API_BASE`, `TERMINAL_ID` and `TERMINAL_SECRET`. `config.h` is git-ignored.
4. Upload, then open the Serial Monitor at 115200 baud. You should see `Online: School Canteen`.
   - `Redirected` / `HTTP 308`: `API_BASE` must start with `https://` and have no trailing slash.
   - `Rejected (401)`: wrong secret (issue a new one from the dashboard) or the clock didn't sync.

## How it works

- Each tap is sent as `POST /v1/terminal/scan` with body `{"tag_uid": "04A1B2C3D4E5F6", "scan_id": "<counter>"}`.
- Every request is signed:
  - `X-Signature = hex(HMAC-SHA256(secret, "<timestamp>.<nonce>.<body>"))`
  - The server rejects bad signatures, clocks more than 60 s off (the reader syncs via NTP), and reused nonces.
- If WiFi drops, the reader retries with the same `scan_id`. The server processes each tap once, so nobody is charged twice.
- A card left resting on the reader counts as one tap. Keep it away for 2 s to tap again.
- A heartbeat every 60 s lets the dashboard show the reader as online.

## Security note

The reader identifies students by serial number only. Serial numbers can be copied onto special "magic" cards, so treat the card like a canteen token, not a bank card:
- Keep the daily spend limit low.
- Parents can block a lost card instantly.
- Every tap is logged, with the student's name shown to the cashier.

For stronger protection later, NTAG 424 DNA cards produce a fresh cryptographic code on every tap.

`scripts/sim_terminal.py` speaks the same protocol, so you can test without hardware.
