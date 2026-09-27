"""Tests run against a real PostgreSQL (row locks and partial indexes matter for money), started
in-process with `pgserver`. Set TEST_DATABASE_URL to use an existing server instead."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import secrets
import tempfile
import time
import uuid

import pytest

_pg = None
if os.environ.get("TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
else:
    import pgserver

    _pg = pgserver.get_server(Path(tempfile.mkdtemp(prefix="cresco-pg-")), cleanup_mode="stop")
    socket_dir = _pg.get_uri().split("host=")[1]
    os.environ["DATABASE_URL"] = f"postgresql+asyncpg://postgres@/postgres?host={socket_dir}"

os.environ.update(
    AI_PROVIDER="fake",
    TTS_PROVIDER="fake",
    SCHEDULER_ENABLED="false",
    STORAGE_DIR=tempfile.mkdtemp(prefix="cresco-storage-"),
    JWT_SECRET="test-secret",
    TERMINAL_MASTER_KEY="test-master",
)

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.db import Base, get_engine, get_sessionmaker  # noqa: E402
from app.core.security import create_token, sign_terminal_request, terminal_secret  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Merchant, ParentStudent, School, Terminal  # noqa: E402
from app.services import accounts, ledger  # noqa: E402


def pytest_sessionfinish(session, exitstatus):
    if _pg is not None:
        _pg.cleanup()


@pytest.fixture(scope="session", autouse=True)
async def schema():
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


@pytest.fixture(autouse=True)
async def clean_tables(schema):
    yield
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with get_engine().begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} CASCADE"))


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


def auth(user_id: uuid.UUID, role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_token(user_id, role, 'access')}"}


@dataclass
class Reader:
    id: uuid.UUID
    secret: str

    def headers(self, body: bytes, *, ts: int | None = None, nonce: str | None = None, secret: str | None = None) -> dict:
        ts_s = str(ts if ts is not None else int(time.time()))
        nonce = nonce or secrets.token_hex(8)
        return {
            "Content-Type": "application/json",
            "X-Terminal-Id": str(self.id),
            "X-Timestamp": ts_s,
            "X-Nonce": nonce,
            "X-Signature": sign_terminal_request(secret or self.secret, ts_s, nonce, body),
        }

    async def post(self, client: httpx.AsyncClient, path: str, payload: dict, **kw) -> httpx.Response:
        body = json.dumps(payload).encode()
        return await client.post(f"/v1/terminal/{path}", content=body, headers=self.headers(body, **kw))


@dataclass
class World:
    school_id: uuid.UUID
    admin: dict
    parent: dict
    staff: dict
    students: list[dict]  # {"id", "headers", "card_token"}
    canteen: Reader
    bookstore: Reader
    canteen_id: uuid.UUID


@pytest.fixture
async def world() -> World:
    async with get_sessionmaker()() as session:
        school = School(name="Test School")
        session.add(school)
        await session.flush()
        admin = await accounts.create_user(
            session, role="admin", name="Admin", email="admin@t.in", phone=None, password="secret1", school_id=school.id
        )
        canteen = Merchant(school_id=school.id, name="Canteen", kind="canteen")
        bookstore = Merchant(school_id=school.id, name="Books", kind="bookstore")
        session.add_all([canteen, bookstore])
        await session.flush()
        staff = await accounts.create_user(
            session, role="merchant_staff", name="Staff", email="staff@t.in", phone=None, password="secret1", merchant_id=canteen.id
        )
        t1, t2 = Terminal(merchant_id=canteen.id, name="C1"), Terminal(merchant_id=bookstore.id, name="B1")
        session.add_all([t1, t2])
        parent = await accounts.create_user(session, role="parent", name="Parent", email="parent@t.in", phone=None, password="secret1")
        students = []
        for i in range(3):
            st = await accounts.create_student(
                session, school.id, name=f"Student {i}", email=f"s{i}@t.in", phone=None, password="secret1", grade=7, preferred_language="ta"
            )
            card = await accounts.issue_card(session, st, None, 50_000)
            await ledger.credit_topup(session, st.id, 10_000, f"seed:{st.id}")
            students.append({"id": st.id, "headers": auth(st.id, "student"), "card_token": card.card_token, "card_id": card.id})
        session.add(ParentStudent(parent_id=parent.id, student_id=students[0]["id"]))
        await session.commit()
        return World(
            school_id=school.id,
            admin={"id": admin.id, "headers": auth(admin.id, "admin")},
            parent={"id": parent.id, "headers": auth(parent.id, "parent")},
            staff={"id": staff.id, "headers": auth(staff.id, "merchant_staff")},
            students=students,
            canteen=Reader(t1.id, terminal_secret(t1.id, 1)),
            bookstore=Reader(t2.id, terminal_secret(t2.id, 1)),
            canteen_id=canteen.id,
        )
