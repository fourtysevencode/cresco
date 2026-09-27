"""Pretend to be an ESP32 + PN532 reader: send signed scans to the Cresco API without hardware.

    uv run --project backend python scripts/sim_terminal.py --terminal-id ID --secret SECRET heartbeat
    uv run --project backend python scripts/sim_terminal.py --terminal-id ID --secret SECRET scan 04:A1:B2:C3
    uv run --project backend python scripts/sim_terminal.py --terminal-id ID --secret SECRET scan 04A1B2C3 --scan-id 7   # retry tap 7

What a scan does depends on the dashboard: with a charge set on this reader it pays, otherwise it
identifies the student (or reports an unregistered card). The signing here is the reference for the
firmware in cresco_pn523/cresco_pn523.ino.
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
    resp = httpx.post(f"{base_url}/v1/terminal/{path}", content=body, headers=headers, timeout=30)
    return {"http_status": resp.status_code, **resp.json()}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", default=os.environ.get("CRESCO_URL", "http://localhost:8000"))
    p.add_argument("--terminal-id", required=True)
    p.add_argument("--secret", required=True)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("heartbeat")
    s = sub.add_parser("scan")
    s.add_argument("uid", help="Card serial number, e.g. 04:A1:B2:C3 or 04A1B2C3")
    s.add_argument("--scan-id", help="Reuse to simulate a retried request")
    args = p.parse_args()

    if args.command == "heartbeat":
        result = send(args.base_url, args.terminal_id, args.secret, "heartbeat", {})
    else:
        payload = {"tag_uid": args.uid, "scan_id": args.scan_id or secrets.token_hex(6)}
        result = send(args.base_url, args.terminal_id, args.secret, "scan", payload)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
