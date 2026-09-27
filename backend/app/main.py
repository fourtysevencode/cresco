from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.responses import RedirectResponse

from app.api.v1 import admin, auth, leaderboard, lessons, merchant, quizzes, terminal, wallets
from app.core.config import get_settings
from app.jobs import build_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.env != "dev" and any(k.startswith("change-me") for k in (settings.jwt_secret, settings.terminal_master_key)):
        raise RuntimeError("Set JWT_SECRET and TERMINAL_MASTER_KEY before running outside ENV=dev")
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

v1 = APIRouter(prefix="/v1")
for module in (auth, admin, wallets, terminal, merchant, lessons, quizzes, leaderboard):
    v1.include_router(module.router)
app.include_router(v1)


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse("/docs")


@app.get("/health", tags=["meta"])
async def health():
    return {"ok": True}
