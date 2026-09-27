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

## Setup

1. Arduino Library Manager: install **Adafruit PN532** and **ArduinoJson** (v7).
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
