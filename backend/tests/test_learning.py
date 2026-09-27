from datetime import timedelta

from sqlalchemy import update

from app.core.db import get_sessionmaker
from app.core.timeutil import iso_week, now, previous_iso_week
from app.models import PointsEntry, Reward

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


async def make_lesson(client, student, data=PNG, language="kn"):
    return await client.post(
        "/v1/lessons", files=[("images", ("page1.png", data, "image/png"))], data={"language": language}, headers=student["headers"]
    )


async def make_quiz(client, student):
    lesson = (await make_lesson(client, student)).json()
    return (await client.post(f"/v1/lessons/{lesson['id']}/quiz", headers=student["headers"])).json()


def all_correct(quiz):
    return [i % 4 for i in range(len(quiz["questions"]))]  # FakeTutor puts the answer at i % 4


async def test_lesson_flow(client, world):
    st = world.students[0]
    r = await make_lesson(client, st)
    assert r.status_code == 201
    lesson = r.json()
    assert lesson["language"] == "kn" and lesson["explanation"]["sections"]

    again = await make_lesson(client, st)
    assert again.status_code == 200 and again.json()["deduplicated"] and again.json()["id"] == lesson["id"]

    audio = await client.get(f"/v1/lessons/{lesson['id']}/audio?section=1", headers=st["headers"])
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/mpeg" and audio.content.startswith(b"ID3")
    assert (await client.get(f"/v1/lessons/{lesson['id']}/audio?section=9", headers=st["headers"])).status_code == 404

    msg = await client.post(f"/v1/lessons/{lesson['id']}/messages", json={"question": "Why are leaves green?"}, headers=st["headers"])
    assert msg.status_code == 201 and msg.json()["content"].startswith("[kn]")
    detail = (await client.get(f"/v1/lessons/{lesson['id']}", headers=st["headers"])).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert (await client.get(f"/v1/lessons/{lesson['id']}/messages/1/audio", headers=st["headers"])).status_code == 200

    # other students can't see it; the linked parent can
    assert (await client.get(f"/v1/lessons/{lesson['id']}", headers=world.students[1]["headers"])).status_code == 404
    assert (await client.get(f"/v1/lessons?student_id={st['id']}", headers=world.parent["headers"])).json()[0]["id"] == lesson["id"]


async def test_rejects_non_images(client, world):
    r = await make_lesson(client, world.students[0], data=b"%PDF-1.4 not an image")
    assert r.status_code == 415


async def test_quiz_hides_answers_and_scores_first_attempt_only(client, world):
    st = world.students[0]
    quiz = await make_quiz(client, st)
    assert len(quiz["questions"]) == 6
    assert "correct_index" not in str(quiz) and "explanation" not in str(quiz)
    assert "correct_index" not in (await client.get(f"/v1/quizzes/{quiz['id']}", headers=st["headers"])).text

    r = await client.post(f"/v1/quizzes/{quiz['id']}/attempts", json={"answers": all_correct(quiz)}, headers=st["headers"])
    body = r.json()
    assert body["correct_count"] == 6 and body["points_awarded"] == 6 * 10 + 20
    assert all(q["correct"] for q in body["results"])

    retry = (await client.post(f"/v1/quizzes/{quiz['id']}/attempts", json={"answers": all_correct(quiz)}, headers=st["headers"])).json()
    assert retry["points_awarded"] == 0 and retry["no_points_reason"] == "retry_not_scored"

    bad = await client.post(f"/v1/quizzes/{quiz['id']}/attempts", json={"answers": [0]}, headers=st["headers"])
    assert bad.status_code == 422
    # another student can't answer someone else's quiz
    assert (await client.post(f"/v1/quizzes/{quiz['id']}/attempts", json={"answers": all_correct(quiz)}, headers=world.students[1]["headers"])).status_code == 404

    points = (await client.get("/v1/students/me/points", headers=st["headers"])).json()
    assert points == {"week": iso_week(), "this_week": 80, "all_time": 80, "rank_this_week": 1}


async def test_daily_scoring_cap(client, world):
    st = world.students[0]
    awarded = []
    lesson = (await make_lesson(client, st)).json()
    for _ in range(6):
        quiz = (await client.post(f"/v1/lessons/{lesson['id']}/quiz", headers=st["headers"])).json()
        r = await client.post(f"/v1/quizzes/{quiz['id']}/attempts", json={"answers": [0] * 6}, headers=st["headers"])
        awarded.append((r.json()["points_awarded"], r.json()["no_points_reason"]))
    assert awarded[:5] == [(20, None)] * 5  # 2 of 6 correct
    assert awarded[5] == (0, "daily_quiz_limit")


async def test_leaderboard_week_close_and_redeem(client, world):
    s0, s1, s2 = world.students
    # s1 scores most, s0 and s2 tie on points but s0 got there first
    for st in (s0, s2, s1):
        quiz = await make_quiz(client, st)
        await client.post(f"/v1/quizzes/{quiz['id']}/attempts", json={"answers": all_correct(quiz)}, headers=st["headers"])
    quiz = await make_quiz(client, s1)
    await client.post(f"/v1/quizzes/{quiz['id']}/attempts", json={"answers": [0] * 6}, headers=s1["headers"])

    board = (await client.get("/v1/leaderboard/weekly", headers=s2["headers"])).json()
    assert [(r["student_id"], r["points"]) for r in board["rows"]] == [(str(s1["id"]), 100), (str(s0["id"]), 80), (str(s2["id"]), 80)]
    assert board["me"]["rank"] == 3
    assert (await client.get("/v1/leaderboard/weekly", headers=world.parent["headers"])).status_code == 200

    # Pretend all of this happened last week, then close it.
    last_week = previous_iso_week(iso_week())
    async with get_sessionmaker()() as s:
        await s.execute(update(PointsEntry).values(iso_week=last_week))
        await s.commit()
    r = await client.post("/v1/admin/leaderboard/close-week", headers=world.admin["headers"])
    assert r.json() == {"week": last_week, "rewards_issued": 3}
    assert (await client.post("/v1/admin/leaderboard/close-week", headers=world.admin["headers"])).json()["rewards_issued"] == 0

    winners = (await client.get("/v1/leaderboard/weekly/winners", headers=s0["headers"])).json()["winners"]
    assert [(w["rank"], w["student_id"]) for w in winners] == [(1, str(s1["id"])), (2, str(s0["id"])), (3, str(s2["id"]))]

    # 3rd place wins a free ice cream: redeemable at the canteen, not the bookstore, and only once.
    await world.set_pending(client, world.bookstore, kind="redeem")
    assert (await world.bookstore.scan(client, s2["uid"])).json()["reason"] == "no_reward"
    await world.set_pending(client, world.canteen, kind="redeem")
    r = (await world.canteen.scan(client, s2["uid"], "ice-1")).json()
    assert r["result"] == "approved" and "ice cream" in r["reward"]
    assert (await world.canteen.scan(client, s2["uid"], "ice-1")).json()["result"] == "approved"  # retried tap
    await world.set_pending(client, world.canteen, kind="redeem")
    assert (await world.canteen.scan(client, s2["uid"])).json()["reason"] == "no_reward"
    rewards = (await client.get("/v1/students/me/rewards", headers=s2["headers"])).json()
    assert [x["status"] for x in rewards] == ["redeemed"]


async def test_all_time_leaderboard(client, world):
    s0, s1, _ = world.students
    for st in (s0, s1):
        quiz = await make_quiz(client, st)
        await client.post(f"/v1/quizzes/{quiz['id']}/attempts", json={"answers": all_correct(quiz)}, headers=st["headers"])
    # s0's points were earned last week: gone from this week's board, still on the all-time one.
    async with get_sessionmaker()() as s:
        await s.execute(update(PointsEntry).where(PointsEntry.student_id == s0["id"]).values(iso_week=previous_iso_week(iso_week())))
        await s.commit()
    quiz = await make_quiz(client, s0)
    await client.post(f"/v1/quizzes/{quiz['id']}/attempts", json={"answers": [0] * 6}, headers=s0["headers"])

    weekly = (await client.get("/v1/leaderboard/weekly", headers=world.admin["headers"])).json()
    assert [(r["student_id"], r["points"]) for r in weekly["rows"]] == [(str(s1["id"]), 80), (str(s0["id"]), 20)]
    board = (await client.get("/v1/leaderboard/all-time", headers=s0["headers"])).json()
    assert board["week"] == "all"
    assert [(r["student_id"], r["points"]) for r in board["rows"]] == [(str(s0["id"]), 100), (str(s1["id"]), 80)]
    assert board["me"]["rank"] == 1


async def test_custom_reward_config(client, world):
    config = [{"rank": 1, "type": "free_icecream", "title": "Ice cream", "value_paise": None, "merchant_kind": "canteen"}]
    r = await client.put("/v1/admin/reward-config", json=config, headers=world.admin["headers"])
    assert r.status_code == 200 and len(r.json()) == 1
    for st in world.students[:2]:
        quiz = await make_quiz(client, st)
        await client.post(f"/v1/quizzes/{quiz['id']}/attempts", json={"answers": all_correct(quiz)}, headers=st["headers"])
    week = iso_week()
    r = await client.post(f"/v1/admin/leaderboard/close-week?week={week}", headers=world.admin["headers"])
    assert r.json()["rewards_issued"] == 1


async def test_expired_reward_not_redeemable(client, world):
    st = world.students[0]
    async with get_sessionmaker()() as s:
        s.add(
            Reward(
                student_id=st["id"], school_id=world.school_id, week="2026-W01", rank=3, points=10, type="free_icecream",
                title="Ice cream", merchant_kind="canteen", expires_at=now() - timedelta(days=1),
            )
        )
        await s.commit()
    await world.set_pending(client, world.canteen, kind="redeem")
    assert (await world.canteen.scan(client, st["uid"])).json()["reason"] == "no_reward"
    assert (await client.get("/v1/students/me/rewards", headers=st["headers"])).json()[0]["status"] == "expired"


PAGE_TEXT = (
    "Chapter 4: Force and Motion\n"
    "A force is a push or a pull on an object. Forces can make a still object move, stop a moving object, "
    "or change its direction or speed. Friction slows moving objects down."
)


async def test_lesson_from_ocr_text(client, world):
    st = world.students[0]
    r = await client.post("/v1/lessons/text", json={"text": PAGE_TEXT, "language": "bn", "subject": "Physics"}, headers=st["headers"])
    assert r.status_code == 201
    lesson = r.json()
    assert lesson["title"] == "Chapter 4: Force and Motion" and lesson["language"] == "bn"
    assert lesson["extracted_text"] == PAGE_TEXT  # kept for follow-up questions and quizzes
    again = await client.post("/v1/lessons/text", json={"text": PAGE_TEXT, "language": "bn"}, headers=st["headers"])
    assert again.status_code == 200 and again.json()["deduplicated"]
    quiz = await client.post(f"/v1/lessons/{lesson['id']}/quiz", headers=st["headers"])
    assert quiz.status_code == 201

    garbled = await client.post("/v1/lessons/text", json={"text": "@@ ## ~~ %% ^^ && ** !!"}, headers=st["headers"])
    assert garbled.status_code == 422 and garbled.json()["detail"]["code"] == "unreadable_text"
    assert (await client.post("/v1/lessons/text", json={"text": "too short"}, headers=st["headers"])).status_code == 422
    assert (await client.post("/v1/lessons/text", json={"text": PAGE_TEXT}, headers=world.parent["headers"])).status_code == 403


async def test_tutor_failure_details_are_returned(client, world, monkeypatch):
    from app.services import ai_tutor

    async def busy(*a, **k):
        raise ai_tutor.TutorError("ai_busy", ["gemini-3.8-flash: 429 RESOURCE_EXHAUSTED"])

    monkeypatch.setattr(ai_tutor.FakeTutor, "explain_text", busy)
    r = await client.post("/v1/lessons/text", json={"text": PAGE_TEXT}, headers=world.students[0]["headers"])
    assert r.status_code == 503
    assert r.json()["detail"] == {"code": "ai_busy", "details": ["gemini-3.8-flash: 429 RESOURCE_EXHAUSTED"]}
