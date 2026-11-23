"""ChatService: pytanie + historia → wyszukiwanie → próg → prompt → odpowiedź (strumień).

Odpowiedź to strumień zdarzeń (słowników gotowych do JSON-a, np. dla SSE/WebSocket):

    {"type": "sources", "sources": [...]}            fragmenty przeszły próg; zaraz LLM
    {"type": "token", "text": "..."}                 kolejny kawałek odpowiedzi LLM-a
    {"type": "refused", "message": NO_INFO}          nic nie przeszło progu, bez LLM-a
    {"type": "clarify", "message": ..., "options": [...]}  brak wydziału lub szczegółu, bez LLM-a
    {"type": "done", "answer": "..."}                zawsze na końcu: pełny tekst odpowiedzi

`done.answer` to tekst, który klient dopisuje do historii jako wiadomość asystenta
(przy `refused`/`clarify` — ich `message`). Źródła pochodzą z wyników wyszukiwania,
nie z tekstu LLM-a: model cytuje numery fragmentów, a ostatni token to lista cytowanych
stron (usbot/core/citations.py). ChatService nie trzyma stanu rozmowy — historię prowadzi klient.
"""
from collections.abc import Callable, Iterator, Sequence
from datetime import date
from typing import Literal, TypedDict

from usbot.core.citations import (CitationFilter, is_outdated, outdated_note, source_numbers,
                                  sources_footer, without_citations)
from usbot.core.clarify import needs_detail
from usbot.core.faculties import CLARIFY_MESSAGE, expand_faculties, faculty_options, needs_faculty
from usbot.core.models import Chunk, Message
from usbot.core.prompts import NO_INFO, build_system_prompt, recent_turns, search_queries
from usbot.core.scope import asks_about_past
from usbot.core.slang import expand_slang
from usbot.core.tokens import fits
from usbot.ports import ChatLLM, Retriever


class Source(TypedDict):
    url: str
    title: str | None
    page_type: str | None
    published_at: str | None   # ISO 8601
    dist: float
    rerank: float | None
    outdated: bool             # komunikat lub wydarzenie sprzed ponad roku


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


def sources(chunks: Sequence[Chunk], today: date) -> list[Source]:
    """Strony z wyników, każda raz (z najlepszego fragmentu), w kolejności wyników."""
    out: dict[str, Source] = {}
    for c in chunks:
        if c.url not in out:
            out[c.url] = {
                "url": c.url, "title": c.title, "page_type": c.page_type,
                "published_at": c.published_at.isoformat() if c.published_at else None,
                "dist": float(c.dist), "rerank": c.rerank, "outdated": is_outdated(c, today),
            }
    return list(out.values())


def drop_order(chunks: Sequence[Chunk], question: str, today: date) -> Chunk:
    """Fragment, który odpada pierwszy, gdy prompt jest za długi (pierwszy zostaje zawsze).

    Przy pytaniach o bieżące zasady najpierw najstarszy komunikat lub wydarzenie: starsze
    komunikaty z tej samej serii podają nieaktualne kwoty („Świadczenia… 2024/2025” obok
    „…2026/2027”). Bez takich fragmentów albo przy pytaniu o przeszłość — najdalszy.
    """
    rest = list(chunks[1:])
    dated = [c for c in rest if c.page_type in ("news", "event") and c.published_at is not None]
    if dated and not asks_about_past(question, today):
        return min(dated, key=lambda c: c.published_at)
    return rest[-1]


class ChatService:
    """Jedna tura rozmowy RAG. Zależy wyłącznie od portów Retriever i ChatLLM."""

    def __init__(self, retriever: Retriever, llm: ChatLLM, *, top_k: int,
                 max_distance: float | None, min_rerank_score: float | None,
                 max_history_turns: int | None = None, clarify_details: bool = False,
                 slang_expansion: bool = False, prompt_max_tokens: int | None = None,
                 today: Callable[[], date] = date.today) -> None:
        self.retriever = retriever
        self.llm = llm
        self.top_k = top_k
        self.max_distance = max_distance
        self.min_rerank_score = min_rerank_score
        # None = cała historia (cli.py); API przycina do API_HISTORY_TURNS tur
        self.max_history_turns = max_history_turns
        # Dopytywanie o przedmiot albo system (usbot/core/clarify.py)
        self.clarify_details = clarify_details
        # Urzędowe odpowiedniki slangu w zapytaniu do wyszukiwarki (usbot/core/slang.py)
        self.slang_expansion = slang_expansion
        # Limit tokenów promptu (fit_prompt()); None = bez limitu
        self.prompt_max_tokens = prompt_max_tokens
        # Dzisiejsza data do promptu (aktualność komunikatów); w testach — stała data
        self.today = today

    def retrieve(self, messages: Sequence[Message]) -> list[Chunk]:
        """Fragmenty dla ostatniego pytania w `messages`, po progu odcięcia.

        Szukamy zapytaniem z historii i samym ostatnim pytaniem (search_queries()),
        ze skrótami wydziałów rozwiniętymi do pełnych nazw (expand_faculties()), a ze
        `slang_expansion` także ze slangiem uzupełnionym o odpowiedniki (expand_slang());
        każdy fragment dostaje lepszy z wyników, a duplikaty, limit na stronę i
        ewentualny reranking działają na złączonej liście (Retriever.search).
        """
        queries = [expand_faculties(q) for q in search_queries(messages)]
        if self.slang_expansion:
            queries = [expand_slang(q) for q in queries]
        queries = list(dict.fromkeys(queries))
        chunks = self.retriever.search(queries, self.top_k)
        return cutoff(chunks, self.max_distance, self.min_rerank_score)

    def fit_prompt(self, chunks: list[Chunk], messages: list[Message]) -> tuple[list[Chunk], list[Message]]:
        """Fragmenty i prompt, który zmieści się w `prompt_max_tokens`.

        Za długi prompt zostawia za mało miejsca na odpowiedź w limicie kontekstu Ollamy i ją
        ucina (docs/testy.md, 6.13). Najpierw odpadają fragmenty (zostaje co najmniej
        jeden; drop_order()), potem najstarsze tury historii (zostaje ostatnie pytanie).
        """
        today = self.today()
        question = messages[-1]["content"] if messages else ""

        def build(cs: list[Chunk], ms: list[Message]) -> list[Message]:
            return [{"role": "system", "content": build_system_prompt(cs, today)}, *ms]

        prompt = build(chunks, messages)
        if self.prompt_max_tokens is None:
            return chunks, prompt
        while len(chunks) > 1 and not fits(prompt, self.prompt_max_tokens):
            drop = drop_order(chunks, question, today)
            chunks = [c for c in chunks if c is not drop]
            prompt = build(chunks, messages)
        while len(messages) > 1 and not fits(prompt, self.prompt_max_tokens):
            messages = messages[2:] if len(messages) > 2 else messages[-1:]
            prompt = build(chunks, messages)
        return chunks, prompt

    def stream(self, question: str, history: Sequence[Message] = ()) -> Iterator[Event]:
        """Odpowiedź na `question` w kontekście `history` (wcześniejsze tury) jako zdarzenia.

        Z `max_history_turns` brane jest tylko tyle ostatnich tur historii — do zapytania
        i do promptu LLM-a. Zamknięcie generatora (`close()`) w trakcie odpowiedzi zamyka
        też strumień LLM-a, więc model przestaje generować.
        """
        history = recent_turns(history, self.max_history_turns)
        messages: list[Message] = [*history, {"role": "user", "content": question}]
        # Dopytania przed wyszukiwaniem: bez wydziału pytanie o dziekanat często nie
        # przechodzi progu („do której jest dziś otwarty dziekanat?”), a z wydziałem trafia
        # we właściwą stronę (docs/testy.md, 6.11)
        if needs_faculty(messages):
            yield {"type": "clarify", "message": CLARIFY_MESSAGE,
                   "options": faculty_options()}
            yield {"type": "done", "answer": CLARIFY_MESSAGE}
            return
        if self.clarify_details and (detail := needs_detail(messages)):
            yield {"type": "clarify", "message": detail.message, "options": detail.options}
            yield {"type": "done", "answer": detail.message}
            return
        chunks = self.retrieve(messages)
        if not chunks:
            # Nic w bazie nie jest dość bliskie — LLM dostałby same śmieci i zmyślał
            yield {"type": "refused", "message": NO_INFO}
            yield {"type": "done", "answer": NO_INFO}
            return

        chunks, prompt = self.fit_prompt(chunks, without_citations(messages))
        yield {"type": "sources", "sources": sources(chunks, self.today())}
        numbers = source_numbers(chunks)
        citations = CitationFilter(len(numbers))
        parts: list[str] = []
        for raw in self.llm.stream(prompt):
            if text := citations.feed(raw):
                parts.append(text)
                yield {"type": "token", "text": text}
        note = ("" if asks_about_past(question, self.today())
                else outdated_note(citations.cited, numbers, chunks, self.today()))
        if text := citations.flush() + note + sources_footer(citations.cited, numbers):
            parts.append(text)
            yield {"type": "token", "text": text}
        yield {"type": "done", "answer": "".join(parts)}
