"""Atrapy portów (usbot.ports) i fabryka fragmentów dla testów."""
from collections.abc import Iterator, Sequence
from dataclasses import replace

from usbot.core.models import Chunk, Message


def chunk(id: int, url: str = "https://us.edu.pl/strona/", *, dist: float = 0.3,
          score: float | None = None, content: str | None = None,
          body_hash: str | None = None, title: str | None = None,
          rerank: float | None = None, page_type: str | None = "static") -> Chunk:
    return Chunk(id=id, url=url, title=title, content=content or f"treść {id}",
                 page_type=page_type, published_at=None,
                 body_hash=body_hash if body_hash is not None else f"h{id}",
                 dist=dist, score=dist if score is None else score, rerank=rerank)


class FakeRetriever:
    """Zwraca zadane fragmenty (kopie, pierwsze k) i zapamiętuje wywołania."""

    def __init__(self, chunks: Sequence[Chunk] = ()) -> None:
        self.chunks = list(chunks)
        self.calls: list[tuple[list[str], int]] = []

    def search(self, queries: Sequence[str], k: int) -> list[Chunk]:
        self.calls.append((list(queries), k))
        return [replace(c) for c in self.chunks[:k]]


class FakeLLM:
    """Strumieniuje zadane kawałki tekstu i zapamiętuje otrzymane wiadomości."""

    def __init__(self, parts: Sequence[str] = ("Godziny: ", "pon 10–14. ", "Źródło: URL")) -> None:
        self.parts = list(parts)
        self.calls: list[list[Message]] = []

    def stream(self, messages: list[Message]) -> Iterator[str]:
        self.calls.append(list(messages))
        yield from self.parts


class FakeReranker:
    """Wynik pary (zapytanie, tekst) z słownika; brak pary = 0."""

    def __init__(self, scores: dict[tuple[str, str], float]) -> None:
        self.scores = scores
        self.calls: list[tuple[list[str], list[str]]] = []

    def score(self, queries: Sequence[str], texts: Sequence[str]) -> list[float]:
        self.calls.append((list(queries), list(texts)))
        return [max(self.scores.get((q, t), 0.0) for q in queries) for t in texts]


class FakeEmbedder:
    """Wektor zapytania ze słownika (testy integracyjne bez pobierania modelu)."""

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self.vectors = vectors

    def embed(self, text: str) -> list[float]:
        return self.vectors[text]

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.vectors[t] for t in texts]
