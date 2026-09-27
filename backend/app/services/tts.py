"""Text-to-speech for the tutor's explanations, returned as MP3 so any browser can play it.

Providers:
- "edge" (default): Microsoft Edge's neural read-aloud voices via the `edge-tts` package. Free, no API
  key, natural voices for every language we support. It is an unofficial use of Edge's public
  endpoint, so it may change without notice — fine for the demo; revisit before production.
- "google": Google Cloud Text-to-Speech (needs an API key on a billing-enabled project).
- "fake": deterministic bytes for tests.

Audio is cached by content hash, so each piece of text is synthesised once no matter how many
students replay it. To self-host a model instead (e.g. AI4Bharat Indic Parler-TTS on a GPU), add a
class with the same `synthesize` signature and select it in `get_tts()`.
"""

import base64
import hashlib
import re
from typing import Protocol

import edge_tts
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import TtsCache
from app.services.languages import locale
from app.services.storage import get_storage


class TTSError(Exception):
    pass


class TTSProvider(Protocol):
    name: str

    def voice(self, language: str) -> str: ...

    async def synthesize(self, text: str, language: str) -> bytes:
        """Returns MP3 bytes."""
        ...


_SENTENCE_END = re.compile(r"(?<=[.!?।॥\n])\s+")
_GOOGLE_MAX_BYTES = 4500  # API limit is 5000 bytes of input; Indic scripts are 3 bytes/char in UTF-8


def chunk_text(text: str, max_bytes: int = _GOOGLE_MAX_BYTES) -> list[str]:
    chunks: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(text.strip()):
        # Hard-split any single sentence that is itself too long.
        while len(sentence.encode()) > max_bytes:
            cut = len(sentence.encode()[:max_bytes].decode(errors="ignore"))
            chunks.append(sentence[:cut])
            sentence = sentence[cut:]
        candidate = f"{current} {sentence}".strip()
        if len(candidate.encode()) > max_bytes:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


class GoogleTTS:
    name = "google"

    def __init__(self, api_key: str, voices: dict[str, str]):
        self.api_key = api_key
        self.voices = voices

    def voice(self, language: str) -> str:
        return self.voices.get(language, locale(language))

    async def synthesize(self, text: str, language: str) -> bytes:
        voice: dict[str, str] = {"languageCode": locale(language)}
        if language in self.voices:
            voice["name"] = self.voices[language]
        audio = b""
        async with httpx.AsyncClient(timeout=30) as client:
            for chunk in chunk_text(text):
                resp = await client.post(
                    "https://texttospeech.googleapis.com/v1/text:synthesize",
                    headers={"X-Goog-Api-Key": self.api_key},
                    json={
                        "input": {"text": chunk},
                        "voice": voice,
                        "audioConfig": {"audioEncoding": "MP3", "speakingRate": 0.95},
                    },
                )
                if resp.status_code >= 400:
                    raise TTSError(f"google_tts_{resp.status_code}")
                # MP3 frames can be concatenated directly.
                audio += base64.b64decode(resp.json()["audioContent"])
        return audio


# Default voice per language (all Microsoft neural voices). Override with EDGE_TTS_VOICES.
EDGE_VOICES = {
    "ta": "ta-IN-PallaviNeural",
    "kn": "kn-IN-SapnaNeural",
    "te": "te-IN-ShrutiNeural",
    "bn": "bn-IN-TanishaaNeural",
    "mr": "mr-IN-AarohiNeural",
    "hi": "hi-IN-SwaraNeural",
    "en": "en-IN-NeerjaNeural",
}


class EdgeTTS:
    name = "edge"

    def __init__(self, voices: dict[str, str]):
        self.voices = {**EDGE_VOICES, **voices}

    def voice(self, language: str) -> str:
        return self.voices[language]

    async def synthesize(self, text: str, language: str) -> bytes:
        # Slightly slower than normal: easier for young listeners to follow.
        communicate = edge_tts.Communicate(text, self.voice(language), rate="-10%")
        audio = bytearray()
        try:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio += chunk["data"]
        except edge_tts.exceptions.EdgeTTSException as e:
            raise TTSError(f"edge_tts: {e}") from e
        if not audio:
            raise TTSError("edge_tts: no audio returned")
        return bytes(audio)


class FakeTTS:
    """Deterministic stand-in for tests and offline development."""

    name = "fake"

    def voice(self, language: str) -> str:
        return f"fake-{language}"

    async def synthesize(self, text: str, language: str) -> bytes:
        return b"ID3FAKE" + hashlib.sha256(f"{language}:{text}".encode()).digest()


def get_tts() -> TTSProvider | None:
    s = get_settings()
    if s.tts_provider == "edge":
        return EdgeTTS(s.edge_tts_voices)
    if s.tts_provider == "google" and s.google_tts_api_key:
        return GoogleTTS(s.google_tts_api_key, s.google_tts_voices)
    if s.tts_provider == "fake":
        return FakeTTS()
    return None


async def speak(session: AsyncSession, provider: TTSProvider, text: str, language: str) -> bytes:
    """Synthesise `text`, or return the cached audio if it has been synthesised before."""
    key = hashlib.sha256(f"{provider.name}|{provider.voice(language)}|{text}".encode()).hexdigest()
    storage = get_storage()
    cached = await session.get(TtsCache, key)
    if cached and await storage.exists(cached.storage_key):
        return await storage.load(cached.storage_key)
    audio = await provider.synthesize(text, language)
    storage_key = await storage.save(f"audio/{key}.mp3", audio)
    await session.merge(TtsCache(key=key, storage_key=storage_key))
    await session.commit()
    return audio
