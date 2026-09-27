"""Run the API locally with zero setup: embedded PostgreSQL, migrations, demo data, then uvicorn.

    uv run python dev_server.py            # from backend/

Data lives in backend/data/ (git-ignored). Demo logins and reader secrets are written to
backend/data/demo-credentials.json on first run. Without ANTHROPIC_API_KEY / GOOGLE_TTS_API_KEY the
tutor and read-aloud use deterministic fakes.
"""

import os
from pathlib import Path
import subprocess
import sys

import pgserver
import uvicorn

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
PORT = int(os.environ.get("PORT", "8000"))


def main() -> None:
    DATA.mkdir(exist_ok=True)
    pg = pgserver.get_server(DATA / "pg", cleanup_mode="stop")
    socket_dir = pg.get_uri().split("host=")[1]

    env = os.environ
    env.setdefault("DATABASE_URL", f"postgresql+asyncpg://postgres@/postgres?host={socket_dir}")
    env.setdefault("STORAGE_DIR", str(DATA / "storage"))
    env.setdefault("AI_PROVIDER", "anthropic" if env.get("ANTHROPIC_API_KEY") else "fake")
    env.setdefault("TTS_PROVIDER", "google" if env.get("GOOGLE_TTS_API_KEY") else "fake")

    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=HERE, check=True)

    creds = DATA / "demo-credentials.json"
    has_data = pg.psql("SELECT count(*) FROM schools;").split("\n")[2].strip() != "0"
    if not has_data:
        seeded = subprocess.run([sys.executable, "-m", "app.cli", "seed-demo"], cwd=HERE, check=True, capture_output=True, text=True)
        creds.write_text(seeded.stdout)
    print(f"\nCresco API: http://localhost:{PORT}/docs")
    print(f"AI tutor: {env['AI_PROVIDER']} | speech: {env['TTS_PROVIDER']} | demo logins: {creds}\n", flush=True)

    uvicorn.run("app.main:app", host="0.0.0.0", port=PORT, reload=False)


if __name__ == "__main__":
    main()
