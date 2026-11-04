"""Modele żądań i odpowiedzi API (pydantic) — kontrakt opisany w docs/api.md."""
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# Granice wielkości żądania — chronią serwer przed bardzo długim wejściem. Historia i tak
# jest przycinana do API_HISTORY_TURNS tur, ale klient może przysłać więcej.
MAX_MESSAGE_CHARS = 2000
MAX_HISTORY_MESSAGES = 100
MAX_HISTORY_CONTENT_CHARS = 20_000


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=MAX_HISTORY_CONTENT_CHARS)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    history: list[HistoryMessage] = Field(default_factory=list, max_length=MAX_HISTORY_MESSAGES)

    @field_validator("message")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("pytanie nie może być puste")
        return v.strip()


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
