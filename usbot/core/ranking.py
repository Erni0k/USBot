"""Kolejność, deduplikacja, fuzja RRF i reranking fragmentów — czysta logika.

Adaptery wyszukiwarek (`usbot.adapters.pgvector`) dostarczają kandydatów, a tu
zapada, które z nich i w jakiej kolejności trafią do kontekstu. Porównania wariantów
i wybór parametrów: docs/testy.md (6.4).
"""
import re
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace
from datetime import date

from usbot.core.models import Chunk
from usbot.core.scope import is_old_news
from usbot.ports import Reranker, Retriever

RRF_K = 60  # stała w RRF: score = Σ 1/(RRF_K + pozycja)
# Z rerankingiem tylu najlepszych kandydatów ocenia cross-encoder; zostaje k z nich.
RERANK_CANDIDATES = 20


_POLISH_QUESTION = re.compile(r"[ąćęłńóśźż]|\b(?:jak|gdzie|czy|kiedy|ile|co|kto|jakie?|jaka|dlaczego)\b",
                              re.IGNORECASE)
_ENGLISH_PAGE = re.compile(r"/(?:en|english-[^/]*)(?:/|$)")


def for_question_language(chunks: list[Chunk], question: str) -> list[Chunk]:
    """Dla pytania po polsku pomija angielskie wersje stron (/en/, /english-…).

    Te wersje (często nieprzetłumaczone kopie albo strony dla obcokrajowców) zajmowały
    miejsca w kontekście i zmieniały odpowiedź. Gdy nie ma nic innego, zostają.
    """
    if not _POLISH_QUESTION.search(question):
        return chunks
    return [c for c in chunks if not _ENGLISH_PAGE.search(c.url)] or chunks


def rank_key(c: Chunk) -> tuple:
    """Kolejność wyników: `score`, przy remisie bliższy wektorowo, potem krótszy URL.

    Remisy odległości to kopie tej samej treści (ten sam embedding), np. strona portalu
    i jej nieprzetłumaczona kopia pod /student/en/ albo /student i /student/pl.
    select() zostawia pierwszą kopię — krótszy adres to zwykle oryginał, lepszy do
    cytowania. W trybie hybrid remis RRF jest częsty (ta sama pozycja na różnych listach).
    """
    return c.score, c.dist, len(c.url), c.url


def select(chunks: Iterable[Chunk], k: int, max_per_url: int) -> list[Chunk]:
    """Pierwsze k fragmentów z posortowanej listy, bez powtórzeń treści.

    Ta sama treść (`body_hash` — bez prefiksu z tytułem) z kilku stron zostaje raz,
    z najbliższego fragmentu. Z `max_per_url` jedna strona dostaje najwyżej tyle
    miejsc (0 = bez limitu), żeby długa strona nie zajęła całego kontekstu. Zwolnione
    miejsca zajmują kolejni kandydaci.
    """
    seen: set[str] = set()
    per_url: Counter[str] = Counter()
    out = []
    for c in chunks:
        key = c.body_hash or c.content
        if key in seen or (max_per_url and per_url[c.url] >= max_per_url):
            continue
        seen.add(key)
        per_url[c.url] += 1
        out.append(c)
        if len(out) == k:
            break
    return out


def rrf(*rankings: Sequence[Chunk]) -> list[Chunk]:
    """Reciprocal Rank Fusion: score fragmentu = −Σ 1/(RRF_K + pozycja) po listach.

    RRF, a nie ważona suma wyników, bo wyniki obu wyszukiwarek mają różne skale:
    odległość kosinusowa to 0–2 (w praktyce 0.2–0.6, mniejsza = lepsza), a ts_rank_cd
    jest nieograniczony (zależy od liczby wystąpień słów i długości fragmentu, większy =
    lepszy) i jego rozkład zmienia się z każdym pytaniem. Wagi sumy trzeba by stroić
    pod te rozkłady, a i tak nie byłyby porównywalne między pytaniami. RRF patrzy tylko
    na pozycje, więc nie wymaga normalizacji ani strojenia; RRF_K = 60 tłumi różnice
    między czołowymi pozycjami, przez co fragment wysoko na obu listach wygrywa z
    fragmentem pierwszym tylko na jednej (Cormack i in., 2009).

    `score` jest ujemny, bo rank_key() sortuje rosnąco. Pola fragmentu (m.in. `dist`)
    pochodzą z pierwszej listy, na której wystąpił.
    """
    fused: dict[int, Chunk] = {}
    for ranking in rankings:
        for pos, c in enumerate(ranking, 1):
            f = fused.get(c.id)
            if f is None:
                f = fused[c.id] = replace(c, score=0.0)
            f.score -= 1 / (RRF_K + pos)
    return sorted(fused.values(), key=rank_key)


def merge_best(candidate_lists: Iterable[Sequence[Chunk]]) -> list[Chunk]:
    """Łączy listy kandydatów kilku zapytań: każdy fragment z lepszym (mniejszym) `score`.

    Wynik posortowany według rank_key(). Dla jednej listy (już posortowanej) zwraca
    ją w tej samej kolejności.
    """
    best: dict[int, Chunk] = {}
    for candidates in candidate_lists:
        for c in candidates:
            if c.id not in best or c.score < best[c.id].score:
                best[c.id] = c
    return sorted(best.values(), key=rank_key)


def rerank(reranker: Reranker, queries: Sequence[str], chunks: list[Chunk]) -> list[Chunk]:
    """Sortuje fragmenty malejąco według wyniku rerankera (ustawia `rerank`, 0–1).

    Przy kilku zapytaniach fragment dostaje najlepszy z wyników — tak jak `score` z
    kilku wyszukiwań (merge_best). Przy remisie decyduje dotychczasowa kolejność.
    """
    if not chunks:
        return []
    for c, s in zip(chunks, reranker.score(queries, [c.content for c in chunks])):
        c.rerank = s
    return sorted(chunks, key=lambda c: (-c.rerank, *rank_key(c)))


def section(chunk: Chunk) -> str:
    """Prefiks fragmentu „<tytuł strony> > <nagłówek sekcji>” (pierwsza linia treści)."""
    return chunk.content.partition("\n")[0]


def estimated_tokens(text: str) -> float:
    """Przybliżona liczba tokenów `text` w qwen3:8b, bez wywoływania modelu.

    Współczynniki dopasowane do `prompt_eval_count` z Ollamy (docs/testy.md, 6.10). Liczba
    znaków nie wystarcza: cyfry kosztują kilka razy więcej niż litery, więc kontekst tej
    samej długości różni się o setki tokenów.
    """
    digits = sum(ch.isdigit() for ch in text)
    letters = sum(ch.isalpha() for ch in text)
    spaces = sum(ch.isspace() for ch in text)
    return 0.27 * letters + 1.43 * digits + 0.53 * spaces + 0.66 * (len(text) - digits - letters - spaces)


def with_neighbors(chunks: Sequence[Chunk], fetch: Callable[[list[int]], Sequence[Chunk]],
                   pages: int, per_side: int, max_tokens: float) -> list[Chunk]:
    """Dokłada sąsiednie fragmenty tej samej sekcji dla `pages` najlepszych stron.

    Fragmenty mają 800 znaków, a zasada i jej wyjątek bywają w dwóch kolejnych. Sąsiad
    dziedziczy wynik fragmentu, przy którym stoi, i trafia obok niego w kolejności z
    dokumentu. `max_tokens` ogranicza szacowaną długość fragmentów w kontekście: Ollama ma
    4096 tokenów na prompt i odpowiedź, a sąsiedzi nie mogą wypchnąć początku promptu.
    """
    best = list(dict.fromkeys(c.url for c in chunks))[:pages]
    have = {c.id for c in chunks}
    wanted = sorted({c.id + d for c in chunks if c.url in best
                     for d in range(-per_side, per_side + 1) if d} - have)
    found = {n.id: n for n in fetch(wanted)} if wanted else {}
    total = sum(estimated_tokens(c.content) for c in chunks)
    seen = {c.body_hash or c.content for c in chunks}
    out: list[Chunk] = []
    for c in chunks:
        before: list[Chunk] = []
        after: list[Chunk] = []
        if c.url in best:
            for distance in range(1, per_side + 1):
                for sign, side in ((-1, before), (1, after)):
                    n = found.get(c.id + sign * distance)
                    key = n and (n.body_hash or n.content)
                    if n is None or n.url != c.url or section(n) != section(c) or key in seen or n.id in have:
                        continue
                    cost = estimated_tokens(n.content)
                    if total + cost > max_tokens:
                        continue
                    seen.add(key)
                    have.add(n.id)
                    total += cost
                    side.append(replace(n, dist=c.dist, score=c.score, rerank=c.rerank))
        out += sorted(before, key=lambda n: n.id) + [c] + sorted(after, key=lambda n: n.id)
    return out


class NeighborRetriever:
    """Retriever dokładający sąsiednie fragmenty tej samej sekcji (with_neighbors())."""

    def __init__(self, inner: Retriever, fetch: Callable[[list[int]], Sequence[Chunk]], *,
                 pages: int, per_side: int, max_tokens: float) -> None:
        self.inner = inner
        self.fetch = fetch
        self.pages = pages
        self.per_side = per_side
        self.max_tokens = max_tokens

    def search(self, queries: Sequence[str], k: int) -> list[Chunk]:
        return with_neighbors(self.inner.search(queries, k), self.fetch, self.pages,
                              self.per_side, self.max_tokens)


class RerankingRetriever:
    """Retriever z rerankingiem: RERANK_CANDIDATES najlepszych z `inner` ocenia `reranker`.

    Duplikaty treści i limit na stronę stosuje `inner` (przed rerankingiem), więc
    wynik rerankera ich nie przywraca. Zostaje k fragmentów z najwyższym `rerank`.

    Reranker nie zna daty, więc stare komunikaty wygrywają z aktualnymi stronami:
    `old_news_factor` < 1 mnoży wynik aktualności i wydarzeń starszych niż `old_news_days`.
    """

    def __init__(self, inner: Retriever, reranker: Reranker,
                 candidates: int = RERANK_CANDIDATES, *, old_news_factor: float = 1.0,
                 old_news_days: int = 730, today: Callable[[], date] = date.today) -> None:
        self.inner = inner
        self.reranker = reranker
        self.candidates = candidates
        self.old_news_factor = old_news_factor
        self.old_news_days = old_news_days
        self.today = today

    def search(self, queries: Sequence[str], k: int) -> list[Chunk]:
        chunks = rerank(self.reranker, queries, self.inner.search(queries, max(k, self.candidates)))
        if self.old_news_factor != 1.0:
            for c in chunks:
                if is_old_news(c, self.today(), self.old_news_days):
                    c.rerank *= self.old_news_factor
            chunks.sort(key=lambda c: (-c.rerank, *rank_key(c)))
        return chunks[:k]
