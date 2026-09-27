"""Serverless-hosting support: hosted-Postgres URLs, the daily cron endpoint and CORS."""

from sqlalchemy import update

from app.core.config import get_settings
from app.core.db import database_url, get_sessionmaker
from app.core.timeutil import iso_week, previous_iso_week
from app.models import PointsEntry
from tests.test_learning import all_correct, make_quiz


def test_database_url_accepts_neon_style_urls():
    url = database_url("postgres://u:p@ep-x.aws.neon.tech/neondb?sslmode=require&channel_binding=require")
    assert url.drivername == "postgresql+asyncpg"
    assert dict(url.query) == {"ssl": "require"}
    assert url.password == "p" and url.host == "ep-x.aws.neon.tech"
    local = database_url("postgresql+asyncpg://cresco:cresco@localhost:5432/cresco")
    assert local.drivername == "postgresql+asyncpg" and dict(local.query) == {}


async def test_daily_cron(client, world, monkeypatch):
    monkeypatch.setattr(get_settings(), "cron_secret", "cron-test-secret")
    assert (await client.get("/v1/cron/daily")).status_code == 401
    assert (await client.get("/v1/cron/daily", headers={"Authorization": "Bearer wrong"})).status_code == 401

    st = world.students[0]
    quiz = await make_quiz(client, st)
    await client.post(f"/v1/quizzes/{quiz['id']}/attempts", json={"answers": all_correct(quiz)}, headers=st["headers"])
    async with get_sessionmaker()() as s:
        await s.execute(update(PointsEntry).values(iso_week=previous_iso_week(iso_week())))
        await s.commit()

    auth = {"Authorization": "Bearer cron-test-secret"}
    r = await client.get("/v1/cron/daily", headers=auth)
    assert r.json() == {"closed_week": previous_iso_week(iso_week()), "rewards_issued": 1}
    assert (await client.get("/v1/cron/daily", headers=auth)).json()["rewards_issued"] == 0


async def test_cron_disabled_without_secret(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "cron_secret", "")
    assert (await client.get("/v1/cron/daily", headers={"Authorization": "Bearer "})).status_code == 401


async def test_cors_preflight(client):
    r = await client.options(
        "/v1/auth/login",
        headers={"Origin": "https://cresco-web.vercel.app", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"},
    )
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "*"
