"""Modele żądań i odpowiedzi API (pydantic) — kontrakt opisany w docs/api.md.

Limity długości (API_MAX_MESSAGE_CHARS, API_MAX_HISTORY_MESSAGES, API_MAX_HISTORY_CHARS)
zależą od ustawień, więc sprawdza je `check_limits()` po walidacji kształtu żądania —
przekroczenie daje ten sam błąd 422 co niepoprawne pole.
"""
from typing import Literal
from urllib.parse import urlsplit

from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, field_validator

from usbot.config import Settings

# Źródła pochodzą wyłącznie z crawla tych serwisów (ALLOWED_DOMAINS w ingestion/scrape.py);
# adresy z ocen ogląda potem człowiek przy oznaczaniu, więc obcych linków nie przyjmujemy.
_SOURCE_DOMAIN = "us.edu.pl"
MAX_FEEDBACK_SOURCES = 50


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


def _clean_text(value: str) -> str:
    """Bez NUL i pojedynczych surogatów (`\\ud800` w JSON-ie) — PostgreSQL ich nie zapisze."""
    return value.encode("utf-8", "ignore").decode("utf-8").replace("\x00", "").strip()


class FeedbackSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2048)
    # Zapas przy końcach zakresu: odległość identycznych wektorów bywa rzędu -1e-16
    dist: float = Field(ge=-0.01, le=2.01, allow_inf_nan=False)
    rerank: float | None = Field(default=None, ge=-0.01, le=1.01, allow_inf_nan=False)

    @field_validator("url")
    @classmethod
    def _university_url(cls, v: str) -> str:
        parts = urlsplit(v)
        host = (parts.hostname or "").lower()
        if (parts.scheme != "https" or parts.username or parts.password or v != v.strip()
                or any(ord(c) < 32 for c in v)
                or not (host == _SOURCE_DOMAIN or host.endswith("." + _SOURCE_DOMAIN))):
            raise ValueError(f"źródło musi być adresem https w domenie {_SOURCE_DOMAIN}")
        return v


class FeedbackRequest(BaseModel):
    """Ocena jednej odpowiedzi. Konfigurację dopisuje serwer, więc klient jej nie przysyła."""
    model_config = ConfigDict(extra="forbid")

    question: str
    answer: str
    rating: Literal[1, -1]
    sources: list[FeedbackSource] = Field(default_factory=list, max_length=MAX_FEEDBACK_SOURCES)
    comment: str | None = None
    # Pytanie padło w trakcie rozmowy („a telefon?”), więc samo może nie mieć sensu
    followup: bool = False

    @field_validator("question", "answer")
    @classmethod
    def _required_text(cls, v: str) -> str:
        v = _clean_text(v)
        if not v:
            raise ValueError("pole nie może być puste")
        return v

    @field_validator("comment")
    @classmethod
    def _optional_text(cls, v: str | None) -> str | None:
        return (_clean_text(v) or None) if v is not None else None


def check_feedback_limits(body: FeedbackRequest, settings: Settings) -> None:
    """Rzuca RequestValidationError (→ 422), gdy któraś treść jest za długa."""
    errors = [
        {"type": "string_too_long", "loc": ("body", field),
         "msg": f"pole może mieć najwyżej {limit} znaków", "input": None,
         "ctx": {"max_length": limit}}
        for field, value, limit in (
            ("question", body.question, settings.api_max_message_chars),
            ("answer", body.answer, settings.api_max_feedback_answer_chars),
            ("comment", body.comment or "", settings.api_max_feedback_comment_chars),
        )
        if len(value) > limit
    ]
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
