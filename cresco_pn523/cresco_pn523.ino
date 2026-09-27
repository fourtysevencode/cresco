#include <Wire.h>
#include <Adafruit_PN532.h>

#define PN532_IRQ   4  // placeholder; leave unconnected
#define PN532_RESET 5  // placeholder; leave unconnected

Adafruit_PN532 nfc(PN532_IRQ, PN532_RESET, &Wire);

void setup() {
  Serial.begin(115200);

  Wire.begin(8, 9);  // SDA, SCL
  nfc.begin();

  if (!nfc.getFirmwareVersion()) {
    Serial.println("PN532 not found. Check wiring and I2C mode.");
    while (true) delay(10);
  }

  nfc.SAMConfig();
  Serial.println("Tap an NFC card...");
}

void loop() {
  uint8_t uid[7];
  uint8_t uidLength;

  if (nfc.readPassiveTargetID(
        PN532_MIFARE_ISO14443A, uid, &uidLength)) {
    Serial.print("UID: ");

    for (uint8_t i = 0; i < uidLength; i++) {
      if (uid[i] < 0x10) Serial.print("0");
      Serial.print(uid[i], HEX);
      if (i < uidLength - 1) Serial.print(":");
    }

    Serial.println();
    delay(1000);
  }
}