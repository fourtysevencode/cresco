from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Empty variables (e.g. copied from .env.example) fall back to the defaults below.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    env: str = "dev"
    # Set automatically by Vercel (VERCEL=1). Switches to serverless-friendly behaviour: no in-process
    # scheduler (use the /v1/cron/daily endpoint), no DB connection pool, and /tmp for files.
    vercel: bool = False
    # Vercel Cron sends "Authorization: Bearer <CRON_SECRET>" to /v1/cron/daily.
    cron_secret: str = ""
    # Web app origins allowed to call the API from a browser, e.g. ["https://cresco.vercel.app"].
    cors_origins: list[str] = ["*"]
    database_url: str = "postgresql+asyncpg://cresco:cresco@localhost:5432/cresco"

    # Auth
    jwt_secret: str = "change-me"
    jwt_access_minutes: int = 30
    jwt_refresh_days: int = 30
    # Terminal secrets are derived from this key (HMAC(master, terminal_id:version)),
    # so no terminal secret is ever stored in the database.
    terminal_master_key: str = "change-me-too"
    terminal_clock_skew_seconds: int = 60

    # Wallet
    default_daily_limit_paise: int = 50_000  # ₹500
    # How long a charge/redeem set on the dashboard waits for a tap before it lapses.
    pending_action_seconds: int = 120
    # Top-ups are mocked (instant credit, no payment gateway).
    min_topup_paise: int = 100  # ₹1
    max_topup_paise: int = 500_000  # ₹5,000

    # AI tutor: "anthropic" or "fake" (deterministic, no network — for tests/dev)
    ai_provider: str = "anthropic"
    claude_model: str = "claude-opus-5"
    claude_effort: str = "medium"
    max_lesson_images: int = 5
    max_image_bytes: int = 5 * 1024 * 1024
    quiz_questions: int = 6

    # Text-to-speech: "edge" (free, no key), "google", "fake" or "none"
    tts_provider: str = "edge"
    # Optional JSON map of language code -> Edge voice, e.g. {"ta": "ta-IN-ValluvarNeural"} for a male voice
    edge_tts_voices: dict[str, str] = {}
    google_tts_api_key: str = ""
    # Optional JSON map of language code -> Google voice name, e.g. {"ta": "ta-IN-Wavenet-A"}
    google_tts_voices: dict[str, str] = {}

    storage_dir: str = "./data"

    # Points & rewards
    points_per_correct: int = 10
    full_marks_bonus: int = 20
    max_scoring_quizzes_per_day: int = 5
    reward_expiry_days: int = 14
    leaderboard_size: int = 50

    timezone: str = "Asia/Kolkata"
    scheduler_enabled: bool = True

    @model_validator(mode="after")
    def _serverless_defaults(self):
        if self.vercel:
            self.scheduler_enabled = False
            if self.storage_dir == "./data":
                self.storage_dir = "/tmp/cresco-data"  # the only writable path; not persistent
        return self

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@lru_cache
def get_settings() -> Settings:
    return Settings()
