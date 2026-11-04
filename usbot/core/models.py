"""Typy domenowe: fragment z wynikami wyszukiwania i wiadomość rozmowy."""
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypedDict


class Message(TypedDict):
    """Wiadomość w formacie API czatu (OpenAI): {"role": ..., "content": ...}."""
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass
class Chunk:
    """Fragment strony zwrócony przez wyszukiwanie.

    `dist` — odległość kosinusowa od zapytania (mniejsza = lepsza); ma ją każdy wynik,
    także znaleziony tylko pełnotekstowo, bo na niej działa próg MAX_DISTANCE.
    `score` — klucz sortowania (mniejszy = lepszy): `dist` (+ kara za stare
    aktualności) albo −RRF w trybie hybrid.
    `rerank` — wynik cross-encodera (0–1, większy = lepszy); None bez rerankingu.
    """
    id: int
    url: str
    title: str | None
    content: str
    page_type: str | None
    published_at: datetime | None
    body_hash: str | None
    dist: float
    score: float
    rerank: float | None = None
