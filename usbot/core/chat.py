"""ChatService: pytanie + historia → wyszukiwanie → próg → prompt → odpowiedź (strumień).

Odpowiedź to strumień zdarzeń (słowników gotowych do JSON-a, np. dla SSE/WebSocket):

    {"type": "sources", "sources": [...]}            fragmenty przeszły próg; zaraz LLM
    {"type": "token", "text": "..."}                 kolejny kawałek odpowiedzi LLM-a
    {"type": "refused", "message": NO_INFO}          nic nie przeszło progu, bez LLM-a
    {"type": "clarify", "message": ..., "options": [...]}  dziekanat bez wydziału, bez LLM-a
    {"type": "done", "answer": "..."}                zawsze na końcu: pełny tekst odpowiedzi

`done.answer` to tekst, który klient dopisuje do historii jako wiadomość asystenta
(przy `refused`/`clarify` — ich `message`). Źródła pochodzą z wyników wyszukiwania,
nie z tekstu LLM-a. ChatService nie trzyma stanu rozmowy — historię prowadzi klient.
"""
from collections.abc import Iterator, Sequence
from typing import Literal, TypedDict

from usbot.core.faculties import CLARIFY_MESSAGE, FACULTIES, expand_faculties, needs_faculty
from usbot.core.models import Chunk, Message
from usbot.core.prompts import NO_INFO, build_system_prompt, search_queries
from usbot.ports import ChatLLM, Retriever


class Source(TypedDict):
    url: str
    title: str | None
    page_type: str | None
    published_at: str | None   # ISO 8601
    dist: float
    rerank: float | None


class FacultyOption(TypedDict):
    code: str
    name: str


class SourcesEvent(TypedDict):
    type: Literal["sources"]
    sources: list[Source]


class TokenEvent(TypedDict):
    type: Literal["token"]
    text: str


class RefusedEvent(TypedDict):
    type: Literal["refused"]
    message: str


class ClarifyEvent(TypedDict):
    type: Literal["clarify"]
    message: str
    options: list[FacultyOption]


class DoneEvent(TypedDict):
    type: Literal["done"]
    answer: str


Event = SourcesEvent | TokenEvent | RefusedEvent | ClarifyEvent | DoneEvent


def cutoff(chunks: Sequence[Chunk], max_distance: float | None,
           min_rerank_score: float | None) -> list[Chunk]:
    """Próg na jednej skali: fragment po rerankingu — `min_rerank_score` na `rerank`,
    bez rerankingu — `max_distance` na `dist`. None = bez progu.

    Z rerankingiem próg odległości nie działa: odcinałby fragmenty dalekie wektorowo,
    które reranker ocenił wysoko (docs/testy.md, 6.4). Próg sprawdzamy tu, nie w SQL:
    przy indeksie HNSW warunek WHERE na odległość filtruje dopiero kandydatów z indeksu
    i może zwrócić za mało wyników.
    """
    def keep(c: Chunk) -> bool:
        if c.rerank is not None:
            return min_rerank_score is None or c.rerank >= min_rerank_score
        return max_distance is None or c.dist <= max_distance

    return [c for c in chunks if keep(c)]


def sources(chunks: Sequence[Chunk]) -> list[Source]:
    """Strony z wyników, każda raz (z najlepszego fragmentu), w kolejności wyników."""
    out: dict[str, Source] = {}
    for c in chunks:
        if c.url not in out:
            out[c.url] = {
                "url": c.url, "title": c.title, "page_type": c.page_type,
                "published_at": c.published_at.isoformat() if c.published_at else None,
                "dist": float(c.dist), "rerank": c.rerank,
            }
    return list(out.values())


class ChatService:
    """Jedna tura rozmowy RAG. Zależy wyłącznie od portów Retriever i ChatLLM."""

    def __init__(self, retriever: Retriever, llm: ChatLLM, *, top_k: int,
                 max_distance: float | None, min_rerank_score: float | None) -> None:
        self.retriever = retriever
        self.llm = llm
        self.top_k = top_k
        self.max_distance = max_distance
        self.min_rerank_score = min_rerank_score

    def retrieve(self, messages: Sequence[Message]) -> list[Chunk]:
        """Fragmenty dla ostatniego pytania w `messages`, po progu odcięcia.

        Szukamy zapytaniem z historii i samym ostatnim pytaniem (search_queries()),
        ze skrótami wydziałów rozwiniętymi do pełnych nazw (expand_faculties());
        każdy fragment dostaje lepszy z wyników, a duplikaty, limit na stronę i
        ewentualny reranking działają na złączonej liście (Retriever.search).
        """
        queries = list(dict.fromkeys(expand_faculties(q) for q in search_queries(messages)))
        chunks = self.retriever.search(queries, self.top_k)
        return cutoff(chunks, self.max_distance, self.min_rerank_score)

    def stream(self, question: str, history: Sequence[Message] = ()) -> Iterator[Event]:
        """Odpowiedź na `question` w kontekście `history` (wcześniejsze tury) jako zdarzenia."""
        messages: list[Message] = [*history, {"role": "user", "content": question}]
        chunks = self.retrieve(messages)
        if not chunks:
            # Nic w bazie nie jest dość bliskie — LLM dostałby same śmieci i zmyślał
            yield {"type": "refused", "message": NO_INFO}
            yield {"type": "done", "answer": NO_INFO}
            return
        if needs_faculty(messages):
            yield {"type": "clarify", "message": CLARIFY_MESSAGE,
                   "options": [{"code": f.code, "name": f.name} for f in FACULTIES]}
            yield {"type": "done", "answer": CLARIFY_MESSAGE}
            return

        yield {"type": "sources", "sources": sources(chunks)}
        prompt: list[Message] = [{"role": "system", "content": build_system_prompt(chunks)},
                                 *messages]
        parts: list[str] = []
        for text in self.llm.stream(prompt):
            parts.append(text)
            yield {"type": "token", "text": text}
        yield {"type": "done", "answer": "".join(parts)}
