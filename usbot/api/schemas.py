"""Modele żądań i odpowiedzi API (pydantic) — kontrakt opisany w docs/api.md.

Limity długości (API_MAX_MESSAGE_CHARS, API_MAX_HISTORY_MESSAGES, API_MAX_HISTORY_CHARS)
zależą od ustawień, więc sprawdza je `check_limits()` po walidacji kształtu żądania —
przekroczenie daje ten sam błąd 422 co niepoprawne pole.
"""
from typing import Literal

from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, field_validator

from usbot.config import Settings


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    history: list[HistoryMessage] = Field(default_factory=list)

    @field_validator("message")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("pytanie nie może być puste")
        return v.strip()


def check_limits(body: ChatRequest, settings: Settings) -> None:
    """Rzuca RequestValidationError (→ 422), gdy pytanie lub historia są za długie."""
    errors = []
    if len(body.message) > settings.api_max_message_chars:
        errors.append({"type": "string_too_long", "loc": ("body", "message"),
                       "msg": f"pytanie może mieć najwyżej {settings.api_max_message_chars} znaków",
                       "input": None, "ctx": {"max_length": settings.api_max_message_chars}})
    if len(body.history) > settings.api_max_history_messages:
        errors.append({"type": "too_long", "loc": ("body", "history"),
                       "msg": f"historia może mieć najwyżej {settings.api_max_history_messages} "
                              "wiadomości — wysyłaj tylko ostatnie tury",
                       "input": None, "ctx": {"max_length": settings.api_max_history_messages}})
    total = sum(len(m.content) for m in body.history)
    if total > settings.api_max_history_chars:
        errors.append({"type": "too_long", "loc": ("body", "history"),
                       "msg": f"historia może mieć najwyżej {settings.api_max_history_chars} "
                              "znaków łącznie — wysyłaj tylko ostatnie tury",
                       "input": None, "ctx": {"max_length": settings.api_max_history_chars}})
    if errors:
        raise RequestValidationError(errors)


class Faculty(BaseModel):
    code: str
    name: str


class Check(BaseModel):
    ok: bool
    ms: float
    error: str | None = None
    detail: dict = Field(default_factory=dict)


class Health(BaseModel):
    status: Literal["ok", "error"]
    checks: dict[str, Check]
