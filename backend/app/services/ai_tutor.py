"""The AI tutor: reads textbook photos, explains them in the student's language, answers follow-up
questions and writes quizzes.

Claude reads the photos directly (vision), so there is no separate OCR step. Explanations and quizzes
use structured outputs, so the response is always valid, typed JSON.
"""

import base64
from dataclasses import dataclass
from functools import lru_cache
import json
from typing import Protocol

import anthropic
from pydantic import BaseModel

from app.core.config import get_settings
from app.prompts import tutor as prompts
from app.services.languages import language_name

# Opt in to server-side refusal fallbacks: if the model's safety classifiers decline a request,
# the API retries it on Anthropic's recommended fallback model inside the same call.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class TutorError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class Section(BaseModel):
    heading: str
    body: str


class KeyTerm(BaseModel):
    english: str
    native: str
    meaning: str


class LessonContent(BaseModel):
    readable: bool
    title: str
    subject: str
    extracted_text: str
    summary: str
    sections: list[Section]
    key_terms: list[KeyTerm]


class QuizQuestion(BaseModel):
    question: str
    options: list[str]
    correct_index: int
    explanation: str


class QuizContent(BaseModel):
    questions: list[QuizQuestion]


@dataclass
class PageImage:
    data: bytes
    media_type: str


@dataclass
class LessonContext:
    language: str
    grade: int | None
    title: str
    subject: str
    extracted_text: str
    explanation: dict


class Tutor(Protocol):
    async def explain(
        self, images: list[PageImage], language: str, grade: int | None, subject: str | None
    ) -> LessonContent: ...

    async def answer(self, lesson: LessonContext, history: list[tuple[str, str]], question: str) -> str: ...

    async def make_quiz(self, lesson: LessonContext, count: int) -> QuizContent: ...


class ClaudeTutor:
    def __init__(self, client: anthropic.AsyncAnthropic, model: str, effort: str):
        self.client = client
        self.model = model
        self.effort = effort

    def _common(self) -> dict:
        return {
            "model": self.model,
            "max_tokens": 16000,
            "betas": [_FALLBACK_BETA],
            "fallbacks": "default",
            "thinking": {"type": "adaptive"},
        }

    @staticmethod
    def _check(response) -> None:
        if response.stop_reason == "refusal":
            raise TutorError("ai_refused")
        if response.stop_reason == "max_tokens":
            raise TutorError("ai_output_truncated")

    async def _call(self, fn, **kwargs):
        try:
            return await fn(**self._common(), **kwargs)
        except anthropic.BadRequestError:
            raise TutorError("ai_bad_request")
        except anthropic.RateLimitError:
            raise TutorError("ai_busy")
        except (anthropic.APIStatusError, anthropic.APIConnectionError):
            raise TutorError("ai_unavailable")

    async def explain(self, images, language, grade, subject):
        content: list[dict] = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": img.media_type, "data": _b64(img.data)},
            }
            for img in images
        ]
        content.append(
            {
                "type": "text",
                "text": prompts.EXPLAIN_USER.format(
                    language=language_name(language),
                    grade=grade or "unknown",
                    subject_line=f"Subject: {subject}" if subject else "",
                ),
            }
        )
        response = await self._call(
            self.client.beta.messages.parse,
            system=prompts.EXPLAIN_SYSTEM,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": self.effort},
            output_format=LessonContent,
        )
        self._check(response)
        return response.parsed_output

    async def answer(self, lesson, history, question):
        system = prompts.CHAT_SYSTEM.format(
            language=language_name(lesson.language),
            grade=lesson.grade or "unknown",
            title=lesson.title,
            subject=lesson.subject,
            extracted_text=lesson.extracted_text,
            explanation=json.dumps(lesson.explanation, ensure_ascii=False),
        )
        messages = [{"role": role, "content": text} for role, text in history]
        messages.append({"role": "user", "content": question})
        response = await self._call(
            self.client.beta.messages.create,
            system=system,
            messages=messages,
            output_config={"effort": "low"},
        )
        self._check(response)
        return "".join(block.text for block in response.content if block.type == "text").strip()

    async def make_quiz(self, lesson, count):
        response = await self._call(
            self.client.beta.messages.parse,
            system=prompts.QUIZ_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": prompts.QUIZ_USER.format(
                        language=language_name(lesson.language),
                        grade=lesson.grade or "unknown",
                        count=count,
                        title=lesson.title,
                        subject=lesson.subject,
                        extracted_text=lesson.extracted_text,
                        summary=lesson.explanation.get("summary", ""),
                    ),
                }
            ],
            output_config={"effort": self.effort},
            output_format=QuizContent,
        )
        self._check(response)
        quiz: QuizContent = response.parsed_output
        valid = [q for q in quiz.questions if len(q.options) == 4 and 0 <= q.correct_index < 4]
        if not valid:
            raise TutorError("ai_bad_quiz")
        return QuizContent(questions=valid[:count])


def _b64(data: bytes) -> str:
    return base64.standard_b64encode(data).decode()


class FakeTutor:
    """Deterministic tutor for tests and for running the API without an Anthropic key."""

    async def explain(self, images, language, grade, subject):
        return LessonContent(
            readable=True,
            title="Photosynthesis",
            subject=subject or "Science",
            extracted_text="Plants make their own food using sunlight, water and carbon dioxide.",
            summary=f"[{language}] Plants make food from sunlight.",
            sections=[
                Section(heading="What is photosynthesis?", body=f"[{language}] Leaves use sunlight to make food."),
                Section(heading="What do plants need?", body=f"[{language}] Water, carbon dioxide and sunlight."),
            ],
            key_terms=[KeyTerm(english="photosynthesis", native=f"[{language}] photosynthesis", meaning="making food")],
        )

    async def answer(self, lesson, history, question):
        return f"[{lesson.language}] Answer to: {question}"

    async def make_quiz(self, lesson, count):
        return QuizContent(
            questions=[
                QuizQuestion(
                    question=f"[{lesson.language}] Question {i + 1}?",
                    options=["A", "B", "C", "D"],
                    correct_index=i % 4,
                    explanation="Because.",
                )
                for i in range(count)
            ]
        )


@lru_cache
def get_tutor() -> Tutor:
    s = get_settings()
    if s.ai_provider == "fake":
        return FakeTutor()
    return ClaudeTutor(anthropic.AsyncAnthropic(), s.claude_model, s.claude_effort)
