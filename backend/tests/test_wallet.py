import asyncio
import time

from sqlalchemy import func, select

from app.core.db import get_sessionmaker
from app.models import LedgerEntry, Wallet


async def balance(student_id) -> int:
    async with get_sessionmaker()() as s:
        return await s.scalar(select(Wallet.balance_paise).where(Wallet.student_id == student_id))


def charge_payload(student, amount, key, uid=None):
    return {"card_token": student["card_token"], "tag_uid": uid, "amount_paise": amount, "idempotency_key": key}


async def test_charge_approved_and_debits(client, world):
    st = world.students[0]
    r = await world.canteen.post(client, "charge", charge_payload(st, 2_500, "k1"))
    assert r.status_code == 200
    assert r.json() == {"status": "approved", "reason": None, "name": "Student", "balance_paise": 7_500}
    assert await balance(st["id"]) == 7_500


async def test_same_idempotency_key_charges_once(client, world):
    st = world.students[0]
    first = await world.canteen.post(client, "charge", charge_payload(st, 1_000, "retry-me"))
    again = await world.canteen.post(client, "charge", charge_payload(st, 1_000, "retry-me"))
    assert first.json() == again.json()
    assert await balance(st["id"]) == 9_000
    conflict = await world.canteen.post(client, "charge", charge_payload(st, 2_000, "retry-me"))
    assert conflict.json()["reason"] == "idempotency_conflict"


async def test_concurrent_taps_never_overdraw(client, world):
    st = world.students[0]  # balance 10_000
    results = await asyncio.gather(
        *[world.canteen.post(client, "charge", charge_payload(st, 3_000, f"c{i}")) for i in range(8)]
    )
    approved = [r for r in results if r.json()["status"] == "approved"]
    assert len(approved) == 3
    assert all(r.json()["reason"] == "insufficient_balance" for r in results if r not in approved)
    assert await balance(st["id"]) == 1_000


async def test_concurrent_retries_same_key_charge_once(client, world):
    st = world.students[0]
    results = await asyncio.gather(*[world.canteen.post(client, "charge", charge_payload(st, 500, "dup")) for _ in range(5)])
    assert {r.json()["status"] for r in results} == {"approved"}
    assert await balance(st["id"]) == 9_500


async def test_declines(client, world):
    st = world.students[0]
    r = await world.canteen.post(client, "charge", charge_payload(st, 20_000, "big"))
    assert r.json()["reason"] == "insufficient_balance"
    r = await world.canteen.post(client, "charge", {**charge_payload(st, 100, "x"), "card_token": "nope"})
    assert r.json() == {"status": "declined", "reason": "unknown_card", "name": None, "balance_paise": None}


async def test_daily_limit(client, world):
    st = world.students[0]
    await client.patch(f"/v1/students/{st['id']}/spend-limit", json={"daily_limit_paise": 3_000}, headers=world.parent["headers"])
    assert (await world.canteen.post(client, "charge", charge_payload(st, 2_000, "a"))).json()["status"] == "approved"
    assert (await world.canteen.post(client, "charge", charge_payload(st, 1_500, "b"))).json()["reason"] == "daily_limit"
    assert (await world.canteen.post(client, "charge", charge_payload(st, 1_000, "c"))).json()["status"] == "approved"


async def test_lost_card_is_declined(client, world):
    st = world.students[0]
    r = await client.post(f"/v1/students/{st['id']}/card/block", headers=world.parent["headers"])
    assert r.json()["status"] == "lost"
    assert (await world.canteen.post(client, "charge", charge_payload(st, 100, "z"))).json()["reason"] == "card_blocked"


async def test_tag_uid_bound_on_first_tap(client, world):
    st = world.students[1]
    assert (await world.canteen.post(client, "charge", charge_payload(st, 100, "u1", uid="04:a1:b2:c3"))).json()["status"] == "approved"
    assert (await world.canteen.post(client, "charge", charge_payload(st, 100, "u2", uid="04A1B2C3"))).json()["status"] == "approved"
    assert (await world.canteen.post(client, "charge", charge_payload(st, 100, "u3", uid="DEADBEEF"))).json()["reason"] == "tag_mismatch"


async def test_terminal_auth_rejections(client, world):
    st = world.students[0]
    payload = charge_payload(st, 100, "sig")
    assert (await world.canteen.post(client, "charge", payload, secret="wrong")).status_code == 401
    stale = await world.canteen.post(client, "charge", payload, ts=int(time.time()) - 600)
    assert stale.status_code == 401 and stale.json()["detail"] == "stale_timestamp"
    assert (await world.canteen.post(client, "charge", payload, nonce="fixednonce1")).status_code == 200
    replay = await world.canteen.post(client, "charge", payload, nonce="fixednonce1")
    assert replay.status_code == 401 and replay.json()["detail"] == "replayed_nonce"
    assert (await client.post("/v1/terminal/charge", json=payload)).status_code == 422  # missing headers
    assert await balance(st["id"]) == 9_900


async def test_heartbeat(client, world):
    r = await world.canteen.post(client, "heartbeat", {})
    assert r.status_code == 200 and r.json()["merchant"] == "Canteen"


async def test_mock_topup_and_parent_scope(client, world):
    st0, st1 = world.students[0], world.students[1]
    r = await client.post(f"/v1/wallets/{st0['id']}/topups", json={"amount_paise": 5_000, "idempotency_key": "btn-1"}, headers=world.parent["headers"])
    assert r.status_code == 201 and r.json()["balance_paise"] == 15_000
    # double-clicked button with the same key credits once
    await client.post(f"/v1/wallets/{st0['id']}/topups", json={"amount_paise": 5_000, "idempotency_key": "btn-1"}, headers=world.parent["headers"])
    assert await balance(st0["id"]) == 15_000
    # not this parent's child
    r = await client.post(f"/v1/wallets/{st1['id']}/topups", json={"amount_paise": 5_000}, headers=world.parent["headers"])
    assert r.status_code == 404
    # students can view but not top up
    assert (await client.get(f"/v1/wallets/{st0['id']}", headers=st0["headers"])).status_code == 200
    assert (await client.get(f"/v1/wallets/{st1['id']}", headers=st0["headers"])).status_code == 404
    assert (await client.post(f"/v1/wallets/{st0['id']}/topups", json={"amount_paise": 100}, headers=st0["headers"])).status_code == 403

    children = (await client.get("/v1/parents/me/children", headers=world.parent["headers"])).json()
    assert [c["balance_paise"] for c in children] == [15_000]
    txns = (await client.get(f"/v1/wallets/{st0['id']}/transactions", headers=world.parent["headers"])).json()
    assert [t["type"] for t in txns] == ["topup", "topup"]


async def test_refund_once(client, world):
    st = world.students[0]
    await world.canteen.post(client, "charge", charge_payload(st, 4_000, "r1"))
    sales = (await client.get("/v1/merchant/transactions", headers=world.staff["headers"])).json()
    assert len(sales) == 1 and sales[0]["student_name"] == "Student 0"
    results = await asyncio.gather(
        *[client.post(f"/v1/merchant/transactions/{sales[0]['id']}/refund", headers=world.staff["headers"]) for _ in range(3)]
    )
    assert sorted(r.status_code for r in results) == [201, 409, 409]
    assert await balance(st["id"]) == 10_000


async def test_ledger_matches_balance(client, world):
    st = world.students[0]
    for i in range(5):
        await world.canteen.post(client, "charge", charge_payload(st, 700, f"l{i}"))
    async with get_sessionmaker()() as s:
        total = await s.scalar(
            select(func.sum(LedgerEntry.amount_paise)).join(Wallet, Wallet.id == LedgerEntry.wallet_id).where(Wallet.student_id == st["id"])
        )
    assert total == await balance(st["id"]) == 6_500
