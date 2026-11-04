"""Porty — interfejsy, przez które rdzeń (`usbot.core`) korzysta z otoczenia.

Rdzeń zna tylko te protokoły; implementacje (bge-m3, pgvector, cross-encoder, API
zgodne z OpenAI) są w `usbot.adapters`, a w testach zastępują je atrapy.
"""
from collections.abc import Iterator, Sequence
from typing import Protocol

from usbot.core.models import Chunk, Message


class Embedder(Protocol):
    """Model embeddingów: tekst → znormalizowany wektor."""

    def embed(self, text: str) -> list[float]:
        """Embedding jednego tekstu (zapytania)."""
        ...

    def embed_batch(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """Embeddingi wielu tekstów naraz (ingestia)."""
        ...


class Retriever(Protocol):
    """Wyszukiwanie fragmentów w bazie wiedzy."""

    def search(self, queries: Sequence[str], k: int) -> list[Chunk]:
        """Do `k` najlepszych fragmentów, od najlepszego, BEZ progu odcięcia.

        Przy kilku zapytaniach fragment dostaje lepszy z wyników. Duplikaty treści
        i limit fragmentów z jednej strony są już zastosowane. Próg (MAX_DISTANCE /
        MIN_RERANK_SCORE) nakłada rdzeń — `usbot.core.chat.cutoff()`.
        """
        ...


class Reranker(Protocol):
    """Model oceniający pary (zapytanie, tekst), np. cross-encoder."""

    def score(self, queries: Sequence[str], texts: Sequence[str]) -> list[float]:
        """Wynik każdego tekstu (0–1, większy = lepszy): najlepszy po zapytaniach."""
        ...


class ChatLLM(Protocol):
    """Model językowy generujący odpowiedź strumieniowo."""

    def stream(self, messages: list[Message]) -> Iterator[str]:
        """Kolejne kawałki tekstu odpowiedzi (bez pustych)."""
        ...
