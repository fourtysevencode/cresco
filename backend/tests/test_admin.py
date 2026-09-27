import uuid

from app.core.db import get_sessionmaker
from app.core.security import terminal_secret
from app.models import School
from app.services import accounts
from tests.conftest import Reader, auth


async def test_onboarding_end_to_end(client, world):
    admin = world.admin["headers"]
    r = await client.post(
        "/v1/admin/students",
        json={"name": "Meena K", "email": "meena@t.in", "password": "secret1", "grade": 6, "preferred_language": "te"},
        headers=admin,
    )
    assert r.status_code == 201
    student_id = r.json()["id"]
    dup = await client.post("/v1/admin/students", json={"name": "X", "email": "MEENA@t.in", "password": "secret1", "grade": 6}, headers=admin)
    assert dup.status_code == 409

    card = (await client.post(f"/v1/admin/students/{student_id}/cards", json={"tag_uid": "04:AA:BB:CC"}, headers=admin)).json()
    assert card["ndef_text"] == f"CRESCO1|{card['card_token']}|Meena" and card["tag_uid"] == "04AABBCC"

    parent = (await client.post("/v1/admin/parents", json={"name": "Ravi K", "phone": "+919800000000", "password": "secret1"}, headers=admin)).json()
    assert (await client.post(f"/v1/admin/parents/{parent['id']}/students", json={"student_id": student_id}, headers=admin)).status_code == 204

    # parent logs in with phone, adds money with one click
    tokens = (await client.post("/v1/auth/login", json={"login": "+919800000000", "password": "secret1"})).json()
    parent_headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    wallet = (await client.post(f"/v1/wallets/{student_id}/topups", json={"amount_paise": 10_000}, headers=parent_headers)).json()
    assert wallet["balance_paise"] == 10_000

    # new reader for the canteen
    creds = (await client.post("/v1/admin/terminals", json={"merchant_id": str(world.canteen_id), "name": "C2"}, headers=admin)).json()
    reader = Reader(creds["terminal_id"], creds["secret"])
    r = await reader.post(client, "charge", {"card_token": card["card_token"], "tag_uid": "04AABBCC", "amount_paise": 3_000, "idempotency_key": "1"})
    assert r.json()["status"] == "approved" and r.json()["balance_paise"] == 7_000

    # rotating the secret locks out the old one
    rotated = (await client.post(f"/v1/admin/terminals/{creds['terminal_id']}/rotate-secret", headers=admin)).json()
    assert (await reader.post(client, "heartbeat", {})).status_code == 401
    assert (await Reader(creds["terminal_id"], rotated["secret"]).post(client, "heartbeat", {})).status_code == 200

    # reissuing a card retires the old one
    new_card = (await client.post(f"/v1/admin/students/{student_id}/cards", json={}, headers=admin)).json()
    old = await reader.post(client, "charge", {"card_token": card["card_token"], "amount_paise": 100, "idempotency_key": "2"}, secret=rotated["secret"])
    assert old.json()["reason"] == "card_blocked"
    assert new_card["status"] == "active"


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
    assert (await client.post(f"/v1/admin/students/{st['id']}/cards", json={}, headers=other_headers)).status_code == 404
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
