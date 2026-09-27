# Writing a student's NFC tag

1. Issue a card: `POST /v1/admin/students/{student_id}/cards` with body `{}`. The response includes `ndef_text`, for example:

   ```
   CRESCO1|2ed7843cd83657727ec233313088343e|Arun
   ```

   `python -m app.cli seed-demo` prints this for each demo student.

2. On an Android phone, install **NXP TagWriter** or **NFC Tools**.
3. Choose *Write* → *Add a record* → **Text** (not URL). Paste `ndef_text` exactly, then tap an **NTAG213/215** tag to write it.
4. Optional: lock the tag (*NFC Tools → Other → Lock tag*) so it can't be rewritten. Locking is permanent.

## Binding the tag's hardware UID

The card works as soon as it's written. The first successful tap binds the tag's hardware UID to the card. After that, a copy of the text written on a different tag is declined (`tag_mismatch`).

To bind the UID up front instead, read it with NFC Tools (e.g. `04:A1:B2:C3:D4:E5:F6`). Pass it when issuing the card: `{"tag_uid": "04A1B2C3D4E5F6"}`.

## Lost cards

- A parent can block the card straight away: `POST /v1/students/{id}/card/block`.
- The school can issue a new one. The old card stops working as soon as a new one is issued.
