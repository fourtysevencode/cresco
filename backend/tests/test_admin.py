import uuid

from app.core.db import get_sessionmaker
from app.core.security import terminal_secret
from app.models import School
from app.services import accounts
from tests.conftest import Reader, auth


async def test_register_card_by_tapping_it(client, world):
    """The dashboard flow: tap an unknown card on any reader, then create the student with that serial number."""
    admin = world.admin["headers"]
    assert (await world.canteen.scan(client, "04:5E:21:9A:6B:70:80")).json()["result"] == "unknown"
    unknown = (await client.get("/v1/admin/scans?unknown_only=true", headers=admin)).json()
    assert [(u["tag_uid"], u["terminal_name"]) for u in unknown] == [("045E219A6B7080", "C1")]
    assert (await client.get("/v1/admin/cards/lookup?tag_uid=045e219a6b7080", headers=admin)).json()["registered"] is False

    # the create-user endpoint: a student mapped to the card, no login needed to pay
    r = await client.post("/v1/admin/students", json={"name": "Meena K", "grade": 6, "tag_uid": unknown[0]["tag_uid"]}, headers=admin)
    assert r.status_code == 201
    meena = r.json()
    assert meena["tag_uid"] == "045E219A6B7080" and meena["card_status"] == "active" and meena["balance_paise"] == 0
    lookup = (await client.get("/v1/admin/cards/lookup?tag_uid=04:5E:21:9A:6B:70:80", headers=admin)).json()
    assert lookup["registered"] and lookup["student_name"] == "Meena K"
    tap = (await world.canteen.scan(client, "045E219A6B7080")).json()
    assert tap["result"] == "identified" and tap["name"] == "Meena"

    # a card can only belong to one student
    dup = await client.post("/v1/admin/students", json={"name": "Other", "grade": 6, "tag_uid": "045E219A6B7080"}, headers=admin)
    assert dup.status_code == 409 and dup.json()["detail"] == {"code": "card_in_use", "student_name": "Meena K"}
    assert (await client.post("/v1/admin/students", json={"name": "X", "grade": 6, "tag_uid": "xyz"}, headers=admin)).status_code == 422
    # a login-less student can't log in
    assert (await client.post("/v1/admin/students", json={"name": "X", "grade": 6, "email": "x@t.in"}, headers=admin)).status_code == 422

    listed = {s["name"]: s for s in (await client.get("/v1/admin/students", headers=admin)).json()}
    assert listed["Meena K"]["tag_uid"] == "045E219A6B7080" and listed["Student 0"]["balance_paise"] == 10_000


async def test_onboarding_end_to_end(client, world):
    admin = world.admin["headers"]
    r = await client.post(
        "/v1/admin/students",
        json={"name": "Meena K", "email": "meena@t.in", "password": "secret1", "grade": 6, "preferred_language": "te"},
        headers=admin,
    )
    assert r.status_code == 201 and r.json()["tag_uid"] is None
    student_id = r.json()["id"]
    dup = await client.post("/v1/admin/students", json={"name": "X", "email": "MEENA@t.in", "password": "secret1", "grade": 6}, headers=admin)
    assert dup.status_code == 409

    card = (await client.post(f"/v1/admin/students/{student_id}/cards", json={"tag_uid": "04:AA:BB:CC"}, headers=admin)).json()
    assert card["tag_uid"] == "04AABBCC" and card["status"] == "active"

    parent = (await client.post("/v1/admin/parents", json={"name": "Ravi K", "phone": "+919800000000", "password": "secret1"}, headers=admin)).json()
    assert (await client.post(f"/v1/admin/parents/{parent['id']}/students", json={"student_id": student_id}, headers=admin)).status_code == 204

    # parent logs in with phone, adds money with one click
    tokens = (await client.post("/v1/auth/login", json={"login": "+919800000000", "password": "secret1"})).json()
    parent_headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    wallet = (await client.post(f"/v1/wallets/{student_id}/topups", json={"amount_paise": 10_000}, headers=parent_headers)).json()
    assert wallet["balance_paise"] == 10_000

    # new reader for the canteen; the admin sets a charge on it and Meena taps
    creds = (await client.post("/v1/admin/terminals", json={"merchant_id": str(world.canteen_id), "name": "C2"}, headers=admin)).json()
    reader = Reader(creds["terminal_id"], creds["secret"])
    await client.post(f"/v1/merchant/terminals/{reader.id}/pending", json={"amount_paise": 3_000}, headers=admin)
    r = (await reader.scan(client, "04AABBCC")).json()
    assert r["result"] == "approved" and r["balance_paise"] == 7_000
    readers = (await client.get("/v1/admin/terminals", headers=admin)).json()
    assert [(t["merchant_name"], t["name"]) for t in readers] == [("Books", "B1"), ("Canteen", "C1"), ("Canteen", "C2")]

    # rotating the secret locks out the old one
    rotated = (await client.post(f"/v1/admin/terminals/{creds['terminal_id']}/rotate-secret", headers=admin)).json()
    assert (await reader.post(client, "heartbeat", {})).status_code == 401
    reader = Reader(creds["terminal_id"], rotated["secret"])
    assert (await reader.post(client, "heartbeat", {})).status_code == 200

    # assigning a new card retires the old one; the old serial number becomes unknown-but-blocked
    new_card = (await client.post(f"/v1/admin/students/{student_id}/cards", json={"tag_uid": "04DDEEFF"}, headers=admin)).json()
    assert new_card["status"] == "active"
    await client.post(f"/v1/merchant/terminals/{reader.id}/pending", json={"amount_paise": 100}, headers=admin)
    assert (await reader.scan(client, "04AABBCC")).json()["reason"] == "card_blocked"
    assert (await reader.scan(client, "04DDEEFF")).json()["result"] == "approved"


async def test_role_and_school_boundaries(client, world):
    st = world.students[0]
    assert (await client.post("/v1/admin/merchants", json={"name": "X", "kind": "canteen"}, headers=st["headers"])).status_code == 403
    assert (await client.get("/v1/merchant/transactions", headers=world.parent["headers"])).status_code == 403
    assert (await client.get("/v1/auth/me")).status_code == 401
    assert (await client.get("/v1/auth/me", headers={"Authorization": "Bearer junk"})).status_code == 401

    assert (await client.post("/v1/auth/login", json={"login": "admin@t.in", "password": "wrong"})).status_code == 401

    # an admin of another school can't see or issue cards to this school's students
    async with get_sessionmaker()() as s:
        other_school = School(name="Other")
        s.add(other_school)
        await s.flush()
        other = await accounts.create_user(
            s, role="admin", name="Other", email="other@t.in", phone=None, password="secret1", school_id=other_school.id
        )
        await s.commit()
    other_headers = auth(other.id, "admin")
    assert (await client.post(f"/v1/admin/students/{st['id']}/cards", json={"tag_uid": "0A0B0C0D"}, headers=other_headers)).status_code == 404
    assert (await client.get(f"/v1/wallets/{st['id']}", headers=other_headers)).status_code == 404
    assert (await client.get("/v1/admin/students", headers=other_headers)).json() == []


async def test_refresh_token(client, world):
    tokens = (await client.post("/v1/auth/login", json={"login": "PARENT@t.in", "password": "secret1"})).json()
    assert (await client.post("/v1/auth/refresh", json={"refresh_token": tokens["access_token"]})).status_code == 401
    fresh = await client.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert fresh.status_code == 200
    me = await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {fresh.json()['access_token']}"})
    assert me.json()["role"] == "parent"


def test_terminal_secret_is_deterministic_per_version():
    tid = uuid.uuid4()
    assert terminal_secret(tid, 1) == terminal_secret(tid, 1) != terminal_secret(tid, 2)


async def test_scholarship_credits_wallet_with_label(client, world):
    st = world.students[0]
    url = f"/v1/admin/students/{st['id']}/scholarships"
    body = {"kind": "books", "amount_paise": 20_000, "note": "Rank #1", "idempotency_key": "k1"}
    r = await client.post(url, json=body, headers=world.admin["headers"])
    assert r.status_code == 201, r.text
    assert r.json()["label"] == "📚 Book Scholarship · Rank #1"
    assert r.json()["balance_after_paise"] == 30_000
    assert (await client.post(url, json=body, headers=world.admin["headers"])).json()["id"] == r.json()["id"]  # double click
    r = await client.post(url, json={"kind": "icecream", "amount_paise": 5_000}, headers=world.admin["headers"])
    assert r.json()["label"] == "🍦 Ice-Cream Scholarship"

    wallet = (await client.get(f"/v1/wallets/{st['id']}", headers=st["headers"])).json()
    assert wallet["balance_paise"] == 35_000
    txns = (await client.get(f"/v1/wallets/{st['id']}/transactions", headers=st["headers"])).json()
    assert [(t["type"], t["amount_paise"]) for t in txns[:2]] == [("scholarship", 5_000), ("scholarship", 20_000)]
    # Spendable like any other money.
    assert (await world.pay(client, st, 34_000))["result"] == "approved"

    # Only admins, only for their own school's students, only positive amounts.
    assert (await client.post(url, json=body, headers=st["headers"])).status_code == 403
    assert (await client.post(url, json={"kind": "books", "amount_paise": 0}, headers=world.admin["headers"])).status_code == 422
    async with get_sessionmaker()() as s:
        other = School(name="Other")
        s.add(other)
        await s.flush()
        outsider = await accounts.create_student(s, other.id, name="X", email=None, phone=None, password=None, grade=7, preferred_language="ta")
        await s.commit()
    r = await client.post(f"/v1/admin/students/{outsider.id}/scholarships", json=body, headers=world.admin["headers"])
    assert r.status_code == 404
