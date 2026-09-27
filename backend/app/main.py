from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.api.v1 import admin, auth, cron, leaderboard, lessons, merchant, quizzes, terminal, wallets
from app.core.config import get_settings
from app.jobs import build_scheduler

settings = get_settings()
# Checked at import (not in lifespan) so it also applies on serverless hosts.
if settings.env != "dev" and any(k.startswith("change-me") for k in (settings.jwt_secret, settings.terminal_master_key)):
    raise RuntimeError("Set JWT_SECRET and TERMINAL_MASTER_KEY before running outside ENV=dev")


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = build_scheduler() if settings.scheduler_enabled else None
    if scheduler:
        scheduler.start()
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


app = FastAPI(
    title="Cresco API",
    version="0.1.0",
    description="NFC student wallet (canteen & bookstore) + multilingual AI tutor, quizzes and weekly leaderboard.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

v1 = APIRouter(prefix="/v1")
for module in (auth, admin, wallets, terminal, merchant, lessons, quizzes, leaderboard, cron):
    v1.include_router(module.router)
app.include_router(v1)


STATIC = Path(__file__).parent / "static"
HOMEPAGE = STATIC / "index.html"
DASHBOARD = STATIC / "dashboard.html"


@app.get("/", include_in_schema=False)
async def root():
    return FileResponse(HOMEPAGE, headers={"Cache-Control": "no-cache"})


@app.get("/dashboard", include_in_schema=False)
async def dashboard():
    return FileResponse(DASHBOARD, headers={"Cache-Control": "no-cache"})


@app.get("/health", tags=["meta"])
async def health():
    return {"ok": True}
