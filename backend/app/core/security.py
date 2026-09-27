from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import uuid

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
import jwt

from app.core.config import get_settings

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def create_token(user_id: uuid.UUID, role: str, kind: str) -> str:
    s = get_settings()
    lifetime = timedelta(minutes=s.jwt_access_minutes) if kind == "access" else timedelta(days=s.jwt_refresh_days)
    now = datetime.now(UTC)
    payload = {"sub": str(user_id), "role": role, "typ": kind, "iat": now, "exp": now + lifetime}
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256")


def decode_token(token: str, kind: str) -> dict:
    """Raises jwt.InvalidTokenError on any problem, including a wrong token kind."""
    payload = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    if payload.get("typ") != kind:
        raise jwt.InvalidTokenError("wrong token type")
    return payload


# --- Terminals (ESP32 readers) ---------------------------------------------------------


def terminal_secret(terminal_id: uuid.UUID, version: int) -> str:
    """Per-terminal shared secret, derived from the master key so it never sits in the DB."""
    key = get_settings().terminal_master_key.encode()
    return hmac.new(key, f"terminal:{terminal_id}:{version}".encode(), hashlib.sha256).hexdigest()


def sign_terminal_request(secret: str, timestamp: str, nonce: str, body: bytes) -> str:
    message = timestamp.encode() + b"." + nonce.encode() + b"." + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_terminal_signature(secret: str, timestamp: str, nonce: str, body: bytes, signature: str) -> bool:
    expected = sign_terminal_request(secret, timestamp, nonce, body)
    return hmac.compare_digest(expected, signature.lower())
