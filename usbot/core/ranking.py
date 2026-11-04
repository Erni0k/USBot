"""Kolejność, deduplikacja, fuzja RRF i reranking fragmentów — czysta logika.

Adaptery wyszukiwarek (`usbot.adapters.pgvector`) dostarczają kandydatów, a tu
zapada, które z nich i w jakiej kolejności trafią do kontekstu. Porównania wariantów
i wybór parametrów: docs/testy.md (6.4).
"""
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import replace

from usbot.core.models import Chunk
from usbot.ports import Reranker, Retriever

RRF_K = 60  # stała w RRF: score = Σ 1/(RRF_K + pozycja)
# Z rerankingiem tylu najlepszych kandydatów ocenia cross-encoder; zostaje k z nich.
RERANK_CANDIDATES = 20


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


class RerankingRetriever:
    """Retriever z rerankingiem: RERANK_CANDIDATES najlepszych z `inner` ocenia `reranker`.

    Duplikaty treści i limit na stronę stosuje `inner` (przed rerankingiem), więc
    wynik rerankera ich nie przywraca. Zostaje k fragmentów z najwyższym `rerank`.
    """

    def __init__(self, inner: Retriever, reranker: Reranker,
                 candidates: int = RERANK_CANDIDATES) -> None:
        self.inner = inner
        self.reranker = reranker
        self.candidates = candidates

    def search(self, queries: Sequence[str], k: int) -> list[Chunk]:
        chunks = self.inner.search(queries, max(k, self.candidates))
        return rerank(self.reranker, queries, chunks)[:k]
