"""Blob storage for textbook photos and cached audio. Local disk for now; swap for S3/R2 in production
by implementing the same three methods."""

from functools import lru_cache
from pathlib import Path

import anyio

from app.core.config import get_settings


class LocalStorage:
    def __init__(self, base_dir: str):
        self.base = Path(base_dir).resolve()

    def _path(self, key: str) -> Path:
        path = (self.base / key).resolve()
        if not path.is_relative_to(self.base):
            raise ValueError("invalid storage key")
        return path

    async def save(self, key: str, data: bytes) -> str:
        path = self._path(key)
        await anyio.to_thread.run_sync(lambda: (path.parent.mkdir(parents=True, exist_ok=True), path.write_bytes(data)))
        return key

    async def load(self, key: str) -> bytes:
        return await anyio.to_thread.run_sync(self._path(key).read_bytes)

    async def exists(self, key: str) -> bool:
        return self._path(key).exists()


@lru_cache
def get_storage() -> LocalStorage:
    return LocalStorage(get_settings().storage_dir)
