"""Endpoints called by the ESP32 + PN532 readers. The reader only reads a card's serial number;
the server decides what the tap means (see app.services.scans). Every request must be HMAC-signed
(see app.core.deps.get_terminal)."""

from fastapi import APIRouter, HTTPException, status

from app.core.deps import SessionDep, TerminalDep
from app.core.timeutil import now
from app.schemas.wallet import HeartbeatOut, ScanIn, ScanOut
from app.services import scans
from app.services.accounts import normalize_uid

router = APIRouter(prefix="/terminal", tags=["terminal (ESP32)"])


@router.post("/heartbeat", response_model=HeartbeatOut)
async def heartbeat(ctx: TerminalDep):
    return HeartbeatOut(terminal=ctx.terminal.name, merchant=ctx.merchant.name, server_time=int(now().timestamp()))


@router.post("/scan", response_model=ScanOut)
async def scan(body: ScanIn, ctx: TerminalDep, session: SessionDep):
    """A card was tapped. Returns 200 with `result`:

    - `approved` / `declined`: the cashier had set a charge (or prize claim) on this reader
    - `identified`: nothing pending; here's who it is
    - `unknown`: this serial number isn't registered yet

    Retry a timed-out request with the same `scan_id`; it's processed once."""
    try:
        uid = normalize_uid(body.tag_uid)
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_tag_uid")
    return await scans.handle_scan(session, ctx.terminal, ctx.merchant, uid, body.scan_id)
