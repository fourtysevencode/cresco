"""Pretend to be an ESP32 reader: send signed requests to the Cresco API without any hardware.

    uv run --project backend python scripts/sim_terminal.py --terminal-id ID --secret SECRET heartbeat
    uv run --project backend python scripts/sim_terminal.py --terminal-id ID --secret SECRET charge --tag "CRESCO1|<token>|Arun" --amount 25
    uv run --project backend python scripts/sim_terminal.py --terminal-id ID --secret SECRET redeem --tag "CRESCO1|<token>|Arun"

`--tag` is exactly the NDEF text written on the card (as printed by `python -m app.cli seed-demo`).
The signing here is the reference for the firmware: see firmware/reader_esp32/reader_esp32.ino.
"""

import argparse
import hashlib
import hmac
import json
import os
import secrets
import time

import httpx


def sign(secret: str, timestamp: str, nonce: str, body: bytes) -> str:
    message = timestamp.encode() + b"." + nonce.encode() + b"." + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def send(base_url: str, terminal_id: str, secret: str, path: str, payload: dict) -> dict:
    body = json.dumps(payload, separators=(",", ":")).encode()
    ts, nonce = str(int(time.time())), secrets.token_hex(8)
    headers = {
        "Content-Type": "application/json",
        "X-Terminal-Id": terminal_id,
        "X-Timestamp": ts,
        "X-Nonce": nonce,
        "X-Signature": sign(secret, ts, nonce, body),
    }
    resp = httpx.post(f"{base_url}/v1/terminal/{path}", content=body, headers=headers, timeout=10)
    return {"http_status": resp.status_code, **resp.json()}


def card_token(tag_text: str) -> str:
    parts = tag_text.split("|")
    if len(parts) < 2 or parts[0] != "CRESCO1":
        raise SystemExit("--tag must look like CRESCO1|<card_token>|<name>")
    return parts[1]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", default=os.environ.get("CRESCO_URL", "http://localhost:8000"))
    p.add_argument("--terminal-id", required=True)
    p.add_argument("--secret", required=True)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("heartbeat")
    c = sub.add_parser("charge")
    c.add_argument("--tag", required=True, help="NDEF text on the card")
    c.add_argument("--uid", help="Tag hardware UID, e.g. 04A1B2C3")
    c.add_argument("--amount", type=float, required=True, help="Rupees, e.g. 25 or 12.50")
    c.add_argument("--key", help="Idempotency key (reuse it to simulate a retry)")
    r = sub.add_parser("redeem")
    r.add_argument("--tag", required=True)
    r.add_argument("--key")
    args = p.parse_args()

    key = getattr(args, "key", None) or secrets.token_hex(6)
    if args.command == "heartbeat":
        result = send(args.base_url, args.terminal_id, args.secret, "heartbeat", {})
    elif args.command == "charge":
        payload = {"card_token": card_token(args.tag), "tag_uid": args.uid, "amount_paise": round(args.amount * 100), "idempotency_key": key}
        result = send(args.base_url, args.terminal_id, args.secret, "charge", payload)
    else:
        payload = {"card_token": card_token(args.tag), "idempotency_key": key}
        result = send(args.base_url, args.terminal_id, args.secret, "rewards/redeem", payload)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
