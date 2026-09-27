"""Bootstrap commands.

    uv run python -m app.cli create-admin --school "Govt High School" --name "Admin" --email admin@school.in --password ...
    uv run python -m app.cli seed-demo        # a demo school with shops, readers, a parent and students
"""

import argparse
import asyncio
import json

from app.core.db import get_engine, get_sessionmaker
from app.core.security import terminal_secret
from app.models import Merchant, ParentStudent, School, Terminal
from app.services import accounts, ledger


async def create_admin(school: str, name: str, email: str, password: str) -> None:
    async with get_sessionmaker()() as session:
        s = School(name=school)
        session.add(s)
        await session.flush()
        admin = await accounts.create_user(session, role="admin", name=name, email=email, phone=None, password=password, school_id=s.id)
        await session.commit()
        print(json.dumps({"school_id": str(s.id), "admin_id": str(admin.id)}, indent=2))


async def seed_demo() -> None:
    password = "cresco123"
    async with get_sessionmaker()() as session:
        school = School(name="Cresco Demo School")
        session.add(school)
        await session.flush()
        await accounts.create_user(
            session, role="admin", name="Demo Admin", email="admin@demo.cresco", phone=None, password=password, school_id=school.id
        )
        canteen = Merchant(school_id=school.id, name="School Canteen", kind="canteen")
        bookstore = Merchant(school_id=school.id, name="Book Store", kind="bookstore")
        session.add_all([canteen, bookstore])
        await session.flush()
        await accounts.create_user(
            session, role="merchant_staff", name="Canteen Staff", email="canteen@demo.cresco", phone=None, password=password, merchant_id=canteen.id
        )
        terminals = [Terminal(merchant_id=canteen.id, name="Canteen Reader 1"), Terminal(merchant_id=bookstore.id, name="Bookstore Reader 1")]
        session.add_all(terminals)
        parent = await accounts.create_user(session, role="parent", name="Lakshmi R", email="parent@demo.cresco", phone=None, password=password)

        students = []
        for i, (name, lang, grade) in enumerate(
            [("Arun Kumar", "ta", 7), ("Divya Rao", "kn", 8), ("Sai Teja", "te", 7), ("Ananya Das", "bn", 6), ("Omkar Patil", "mr", 8)]
        ):
            student = await accounts.create_student(
                session, school.id, name=name, email=f"student{i + 1}@demo.cresco", phone=None, password=password, preferred_language=lang, grade=grade
            )
            # Demo serial numbers; register real cards from the dashboard (tap an unknown card).
            card = await accounts.issue_card(session, student, f"C0DE{i + 1:04X}", None)
            if i < 2:
                session.add(ParentStudent(parent_id=parent.id, student_id=student.id))
            await ledger.credit_topup(session, student.id, 20_000, f"seed:{student.id}", description="Demo balance")
            students.append({"name": name, "login": student.email, "student_id": str(student.id), "tag_uid": card.tag_uid})
        await session.commit()

        print(
            json.dumps(
                {
                    "password_for_all_accounts": password,
                    "admin": "admin@demo.cresco",
                    "parent": "parent@demo.cresco (linked to the first two students)",
                    "canteen_staff": "canteen@demo.cresco",
                    "terminals_note": "Reader secrets are derived from TERMINAL_MASTER_KEY. If the server uses a different key "
                    "than this machine (e.g. on Vercel), issue new ones from the dashboard: Readers -> New secret.",
                    "terminals": [
                        {"name": t.name, "terminal_id": str(t.id), "secret": terminal_secret(t.id, t.secret_version)} for t in terminals
                    ],
                    "students": students,
                },
                indent=2,
                ensure_ascii=False,
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("create-admin", help="Create a school and its first admin")
    p.add_argument("--school", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--email", required=True)
    p.add_argument("--password", required=True)
    sub.add_parser("seed-demo", help="Create a demo school with shops, readers, a parent and five students")
    args = parser.parse_args()

    async def run():
        try:
            if args.command == "create-admin":
                await create_admin(args.school, args.name, args.email, args.password)
            else:
                await seed_demo()
        finally:
            await get_engine().dispose()

    asyncio.run(run())


if __name__ == "__main__":
    main()
