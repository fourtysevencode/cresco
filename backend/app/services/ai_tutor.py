"""The AI tutor: reads textbook photos, explains them in the student's language, answers follow-up
questions and writes quizzes.

The model reads the photos directly (vision), so there is no separate OCR step. Explanations and
quizzes use structured outputs, so the response is always valid, typed JSON.

Providers (AI_PROVIDER): "gemini" (Google Gemini, free tier), "anthropic" (Claude) or "fake"
(canned content for tests). Left unset, it picks Gemini if GEMINI_API_KEY is set, else Claude if
ANTHROPIC_API_KEY is set, else the fake tutor.
"""

import base64
from dataclasses import dataclass
from functools import lru_cache
import json
from typing import Protocol

import anthropic
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
import httpx
from pydantic import BaseModel, ValidationError

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


class GeminiTutor:
    """Google Gemini via the google-genai SDK. Uses the same prompts and output schemas as Claude."""

    def __init__(self, client: genai.Client, model: str):
        self.client = client
        self.model = model

    async def _generate(self, contents, system: str, schema: type[BaseModel] | None = None):
        config = genai_types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json" if schema else None,
            response_schema=schema,
            automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(disable=True),  # no tools used
        )
        try:
            response = await self.client.aio.models.generate_content(model=self.model, contents=contents, config=config)
        except genai_errors.ClientError as e:
            raise TutorError("ai_busy" if e.code == 429 else "ai_bad_request")
        except (genai_errors.APIError, httpx.HTTPError):
            raise TutorError("ai_unavailable")
        feedback = response.prompt_feedback
        if feedback is not None and feedback.block_reason:
            raise TutorError("ai_refused")
        candidate = response.candidates[0] if response.candidates else None
        finish = candidate.finish_reason if candidate else None
        if finish == genai_types.FinishReason.MAX_TOKENS:
            raise TutorError("ai_output_truncated")
        if finish in (genai_types.FinishReason.SAFETY, genai_types.FinishReason.PROHIBITED_CONTENT):
            raise TutorError("ai_refused")
        if schema is None:
            return (response.text or "").strip()
        if isinstance(response.parsed, schema):
            return response.parsed
        try:
            return schema.model_validate_json(response.text or "")
        except ValidationError:
            raise TutorError("ai_bad_output")

    async def explain(self, images, language, grade, subject):
        contents = [genai_types.Part.from_bytes(data=img.data, mime_type=img.media_type) for img in images]
        contents.append(
            genai_types.Part.from_text(
                text=prompts.EXPLAIN_USER.format(
                    language=language_name(language),
                    grade=grade or "unknown",
                    subject_line=f"Subject: {subject}" if subject else "",
                )
            )
        )
        return await self._generate(contents, prompts.EXPLAIN_SYSTEM, LessonContent)

    async def answer(self, lesson, history, question):
        system = prompts.CHAT_SYSTEM.format(
            language=language_name(lesson.language),
            grade=lesson.grade or "unknown",
            title=lesson.title,
            subject=lesson.subject,
            extracted_text=lesson.extracted_text,
            explanation=json.dumps(lesson.explanation, ensure_ascii=False),
        )
        contents = [
            genai_types.Content(role="model" if role == "assistant" else "user", parts=[genai_types.Part.from_text(text=text)])
            for role, text in history
        ]
        contents.append(genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=question)]))
        return await self._generate(contents, system)

    async def make_quiz(self, lesson, count):
        prompt = prompts.QUIZ_USER.format(
            language=language_name(lesson.language),
            grade=lesson.grade or "unknown",
            count=count,
            title=lesson.title,
            subject=lesson.subject,
            extracted_text=lesson.extracted_text,
            summary=lesson.explanation.get("summary", ""),
        )
        quiz: QuizContent = await self._generate(prompt, prompts.QUIZ_SYSTEM, QuizContent)
        valid = [q for q in quiz.questions if len(q.options) == 4 and 0 <= q.correct_index < 4]
        if not valid:
            raise TutorError("ai_bad_quiz")
        return QuizContent(questions=valid[:count])


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
    provider = s.ai_provider or ("gemini" if s.gemini_api_key else "anthropic" if s.anthropic_api_key else "fake")
    if provider == "gemini":
        return GeminiTutor(genai.Client(api_key=s.gemini_api_key), s.gemini_model)
    if provider == "anthropic":
        return ClaudeTutor(anthropic.AsyncAnthropic(api_key=s.anthropic_api_key or None), s.claude_model, s.claude_effort)
    return FakeTutor()
