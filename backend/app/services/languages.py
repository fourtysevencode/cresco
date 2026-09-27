from typing import Literal

# Code -> (English name, BCP-47 locale used for speech)
LANGUAGES: dict[str, tuple[str, str]] = {
    "ta": ("Tamil", "ta-IN"),
    "kn": ("Kannada", "kn-IN"),
    "te": ("Telugu", "te-IN"),
    "bn": ("Bengali", "bn-IN"),
    "mr": ("Marathi", "mr-IN"),
    "hi": ("Hindi", "hi-IN"),
    "en": ("English", "en-IN"),
}

LanguageCode = Literal["ta", "kn", "te", "bn", "mr", "hi", "en"]


def language_name(code: str) -> str:
    return LANGUAGES[code][0]


def locale(code: str) -> str:
    return LANGUAGES[code][1]
