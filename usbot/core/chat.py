"""ChatService: pytanie + historia → wyszukiwanie → próg → prompt → odpowiedź (strumień).

Odpowiedź to strumień zdarzeń (słowników gotowych do JSON-a, np. dla SSE/WebSocket):

    {"type": "sources", "sources": [...]}            fragmenty przeszły próg; zaraz LLM
    {"type": "token", "text": "..."}                 kolejny kawałek odpowiedzi LLM-a
    {"type": "refused", "message": NO_INFO}          nic nie przeszło progu, bez LLM-a
    {"type": "clarify", "message": ..., "options": [...]}  dziekanat bez wydziału, bez LLM-a
    {"type": "done", "answer": "..."}                zawsze na końcu: pełny tekst odpowiedzi

`done.answer` to tekst, który klient dopisuje do historii jako wiadomość asystenta
(przy `refused`/`clarify` — ich `message`). Źródła pochodzą z wyników wyszukiwania,
nie z tekstu LLM-a: model cytuje numery fragmentów, a ostatni token to lista cytowanych
stron (usbot/core/citations.py). ChatService nie trzyma stanu rozmowy — historię prowadzi klient.
"""
from collections.abc import Callable, Iterator, Sequence
from datetime import date
from typing import Literal, TypedDict

from usbot.core.citations import CitationFilter, source_numbers, sources_footer, without_citations
from usbot.core.faculties import CLARIFY_MESSAGE, expand_faculties, faculty_options, needs_faculty
from usbot.core.models import Chunk, Message
from usbot.core.prompts import NO_INFO, build_system_prompt, recent_turns, search_queries
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
                 max_distance: float | None, min_rerank_score: float | None,
                 max_history_turns: int | None = None,
                 today: Callable[[], date] = date.today) -> None:
        self.retriever = retriever
        self.llm = llm
        self.top_k = top_k
        self.max_distance = max_distance
        self.min_rerank_score = min_rerank_score
        # None = cała historia (cli.py); API przycina do API_HISTORY_TURNS tur
        self.max_history_turns = max_history_turns
        # Dzisiejsza data do promptu (aktualność komunikatów); w testach — stała data
        self.today = today

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
        """Odpowiedź na `question` w kontekście `history` (wcześniejsze tury) jako zdarzenia.

        Z `max_history_turns` brane jest tylko tyle ostatnich tur historii — do zapytania
        i do promptu LLM-a. Zamknięcie generatora (`close()`) w trakcie odpowiedzi zamyka
        też strumień LLM-a, więc model przestaje generować.
        """
        history = recent_turns(history, self.max_history_turns)
        messages: list[Message] = [*history, {"role": "user", "content": question}]
        chunks = self.retrieve(messages)
        if not chunks:
            # Nic w bazie nie jest dość bliskie — LLM dostałby same śmieci i zmyślał
            yield {"type": "refused", "message": NO_INFO}
            yield {"type": "done", "answer": NO_INFO}
            return
        if needs_faculty(messages):
            yield {"type": "clarify", "message": CLARIFY_MESSAGE,
                   "options": faculty_options()}
            yield {"type": "done", "answer": CLARIFY_MESSAGE}
            return

        yield {"type": "sources", "sources": sources(chunks)}
        prompt: list[Message] = [{"role": "system", "content": build_system_prompt(chunks, self.today())},
                                 *without_citations(messages)]
        numbers = source_numbers(chunks)
        citations = CitationFilter(len(numbers))
        parts: list[str] = []
        for raw in self.llm.stream(prompt):
            if text := citations.feed(raw):
                parts.append(text)
                yield {"type": "token", "text": text}
        if text := citations.flush() + sources_footer(citations.cited, numbers):
            parts.append(text)
            yield {"type": "token", "text": text}
        yield {"type": "done", "answer": "".join(parts)}
