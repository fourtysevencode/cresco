import asyncio
import time

from sqlalchemy import func, select

from app.core.db import get_sessionmaker
from app.models import LedgerEntry, Merchant, NfcCard, Terminal, User, Wallet
from app.services import ledger


async def balance(student_id) -> int:
    async with get_sessionmaker()() as s:
        return await s.scalar(select(Wallet.balance_paise).where(Wallet.student_id == student_id))


async def test_tap_with_pending_charge_pays(client, world):
    st = world.students[0]
    r = await world.pay(client, st, 2_500)
    assert r == {"result": "approved", "reason": None, "name": "Student", "balance_paise": 7_500, "amount_paise": 2_500, "reward": None}
    assert await balance(st["id"]) == 7_500
    # the charge is used up: the next tap only identifies
    again = (await world.canteen.scan(client, st["uid"])).json()
    assert again["result"] == "identified" and again["balance_paise"] == 7_500
    assert await balance(st["id"]) == 7_500


async def test_tap_without_pending_identifies(client, world):
    st = world.students[1]
    r = (await world.canteen.scan(client, "04:aa:00:01")).json()  # separators and case don't matter
    assert r["result"] == "identified" and r["name"] == "Student" and r["balance_paise"] == 10_000
    unknown = (await world.canteen.scan(client, "DEADBEEF")).json()
    assert unknown["result"] == "unknown" and unknown["reason"] == "unregistered_card"
    assert (await world.canteen.scan(client, "not-hex")).status_code == 422
    assert st["uid"] == "04AA0001"


async def test_retried_scan_is_processed_once(client, world):
    st = world.students[0]
    first = await world.pay(client, st, 1_000, scan_id="tap-1")
    await world.set_pending(client, world.canteen, 1_000)  # a new charge is waiting…
    again = (await world.canteen.scan(client, st["uid"], "tap-1")).json()  # …but this is a retry of the old tap
    assert first == again
    assert await balance(st["id"]) == 9_000


async def test_concurrent_taps_complete_one_pending_charge(client, world):
    st = world.students[0]
    await world.set_pending(client, world.canteen, 3_000)
    results = await asyncio.gather(*[world.canteen.scan(client, st["uid"]) for _ in range(6)])
    kinds = sorted(r.json()["result"] for r in results)
    assert kinds == ["approved"] + ["identified"] * 5
    assert await balance(st["id"]) == 7_000


async def test_concurrent_retries_same_scan_charge_once(client, world):
    st = world.students[0]
    await world.set_pending(client, world.canteen, 500)
    results = await asyncio.gather(*[world.canteen.scan(client, st["uid"], "dup") for _ in range(5)])
    assert {r.json()["result"] for r in results} == {"approved"}
    assert await balance(st["id"]) == 9_500


async def test_ledger_never_overdraws_under_concurrency(world):
    """Several readers charging the same wallet at once (the ledger's row lock)."""
    st = world.students[0]  # balance 10_000
    async with get_sessionmaker()() as s:
        card = await s.get(NfcCard, st["card_id"])
        terminal = await s.get(Terminal, world.canteen.id)
        merchant = await s.get(Merchant, terminal.merchant_id)

    async def one(i):
        async with get_sessionmaker()() as s:
            student = await s.get(User, st["id"])
            c = await s.get(NfcCard, card.id)
            return await ledger.charge(s, terminal, merchant, c, student, 3_000, f"c{i}")

    results = await asyncio.gather(*[one(i) for i in range(8)])
    assert sorted(r.status for r in results).count("approved") == 3
    assert all(r.reason == "insufficient_balance" for r in results if r.status == "declined")
    assert await balance(st["id"]) == 1_000


async def test_declined_tap_keeps_charge_open(client, world):
    poor, rich = world.students[0], world.students[1]
    await world.set_pending(client, world.canteen, 20_000)
    r = (await world.canteen.scan(client, poor["uid"])).json()
    assert r["result"] == "declined" and r["reason"] == "insufficient_balance" and r["balance_paise"] == 10_000
    await client.post(f"/v1/wallets/{poor['id']}/topups", json={"amount_paise": 10_000}, headers=world.parent["headers"])
    assert (await world.canteen.scan(client, poor["uid"])).json()["result"] == "approved"
    assert await balance(poor["id"]) == 0
    assert await balance(rich["id"]) == 10_000


async def test_daily_limit(client, world):
    st = world.students[0]
    await client.patch(f"/v1/students/{st['id']}/spend-limit", json={"daily_limit_paise": 3_000}, headers=world.parent["headers"])
    assert (await world.pay(client, st, 2_000))["result"] == "approved"
    assert (await world.pay(client, st, 1_500))["reason"] == "daily_limit"
    await world.set_pending(client, world.canteen, 1_000)  # replaces the declined ₹15 charge
    assert (await world.canteen.scan(client, st["uid"])).json()["result"] == "approved"


async def test_lost_card_is_declined(client, world):
    st = world.students[0]
    r = await client.post(f"/v1/students/{st['id']}/card/block", headers=world.parent["headers"])
    assert r.json()["status"] == "lost"
    assert (await world.pay(client, st, 100))["reason"] == "card_blocked"
    assert (await world.canteen.scan(client, st["uid"])).json()["reason"] == "card_blocked"


async def test_pending_actions(client, world):
    reader = world.canteen
    assert (await world.set_pending(client, reader, None)).status_code == 422  # a charge needs an amount
    await world.set_pending(client, reader, 1_000)
    await world.set_pending(client, reader, 2_000)  # replaces the first
    status = (await client.get(f"/v1/merchant/terminals/{reader.id}/status", headers=world.staff["headers"])).json()
    assert status["pending"]["amount_paise"] == 2_000 and status["last_scan"] is None
    await client.delete(f"/v1/merchant/terminals/{reader.id}/pending", headers=world.staff["headers"])
    status = (await client.get(f"/v1/merchant/terminals/{reader.id}/status", headers=world.staff["headers"])).json()
    assert status["pending"] is None
    # canteen staff can't drive the bookstore reader; the admin can
    assert (await client.post(f"/v1/merchant/terminals/{world.bookstore.id}/pending", json={"amount_paise": 100}, headers=world.staff["headers"])).status_code == 404
    assert (await world.set_pending(client, world.bookstore, 100)).status_code == 201
    staff_readers = (await client.get("/v1/merchant/terminals", headers=world.staff["headers"])).json()
    assert [t["name"] for t in staff_readers] == ["C1"]


async def test_status_shows_last_tap(client, world):
    st = world.students[0]
    await world.pay(client, st, 700)
    status = (await client.get(f"/v1/merchant/terminals/{world.canteen.id}/status", headers=world.staff["headers"])).json()
    assert status["pending"] is None
    assert status["last_scan"]["result"] == "approved" and status["last_scan"]["amount_paise"] == 700
    assert status["last_scan_uid"] == st["uid"]


async def test_terminal_auth_rejections(client, world):
    st = world.students[0]
    payload = {"tag_uid": st["uid"], "scan_id": "sig"}
    assert (await world.canteen.post(client, "scan", payload, secret="wrong")).status_code == 401
    stale = await world.canteen.post(client, "scan", payload, ts=int(time.time()) - 600)
    assert stale.status_code == 401 and stale.json()["detail"] == "stale_timestamp"
    assert (await world.canteen.post(client, "scan", payload, nonce="fixednonce1")).status_code == 200
    replay = await world.canteen.post(client, "scan", payload, nonce="fixednonce1")
    assert replay.status_code == 401 and replay.json()["detail"] == "replayed_nonce"
    assert (await client.post("/v1/terminal/scan", json=payload)).status_code == 422  # missing headers


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
    await world.pay(client, st, 4_000)
    sales = (await client.get("/v1/merchant/transactions", headers=world.staff["headers"])).json()
    assert len(sales) == 1 and sales[0]["student_name"] == "Student 0"
    results = await asyncio.gather(
        *[client.post(f"/v1/merchant/transactions/{sales[0]['id']}/refund", headers=world.staff["headers"]) for _ in range(3)]
    )
    assert sorted(r.status_code for r in results) == [201, 409, 409]
    assert await balance(st["id"]) == 10_000


async def test_ledger_matches_balance(client, world):
    st = world.students[0]
    for _ in range(5):
        await world.pay(client, st, 700)
    async with get_sessionmaker()() as s:
        total = await s.scalar(
            select(func.sum(LedgerEntry.amount_paise)).join(Wallet, Wallet.id == LedgerEntry.wallet_id).where(Wallet.student_id == st["id"])
        )
    assert total == await balance(st["id"]) == 6_500
