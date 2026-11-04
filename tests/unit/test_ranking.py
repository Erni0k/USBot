"""select, rrf, merge_best, rerank i RerankingRetriever — czysta logika rankingu."""
import pytest

from tests.fakes import FakeReranker, FakeRetriever, chunk
from usbot.core.ranking import (RERANK_CANDIDATES, RRF_K, RerankingRetriever, merge_best,
                                rank_key, rerank, rrf, select)


def test_select_duplikaty_tresci_i_limit_na_strone():
    # T13: ta sama treść raz (najbliższa; przy remisie krótszy URL), ≤ max_per_url na stronę
    long = "https://us.edu.pl/student/en/dziekanat/"
    short = "https://us.edu.pl/student/dziekanat/"
    page = "https://us.edu.pl/dlugastrona/"
    cands = sorted([
        chunk(1, long, dist=0.20, body_hash="same"),
        chunk(2, short, dist=0.20, body_hash="same"),
        chunk(3, page, dist=0.21), chunk(4, page, dist=0.22),
        chunk(5, page, dist=0.23), chunk(6, page, dist=0.24),
        chunk(7, "https://us.edu.pl/x/", dist=0.30),
        chunk(8, "https://us.edu.pl/y/", dist=0.31),
    ], key=rank_key)
    out = select(cands, 4, max_per_url=2)
    assert [c.id for c in out] == [2, 3, 4, 7]


def test_select_bez_limitu_na_strone():
    cands = [chunk(i, "https://us.edu.pl/a/", dist=0.1 * i) for i in range(1, 5)]
    assert len(select(cands, 4, max_per_url=0)) == 4


def test_select_bez_body_hash_porownuje_tresc():
    cands = [chunk(1, "https://a/", body_hash="", content="X"),
             chunk(2, "https://b/", body_hash="", content="X")]
    assert [c.id for c in select(cands, 5, 2)] == [1]


def test_rrf_wysoko_na_obu_listach_wygrywa():
    # T16: 3. miejsce na obu listach (2/63) > 1. miejsce tylko na jednej (1/61)
    both = chunk(1, "https://both/", dist=0.4)
    a, b, c, d = (chunk(i, f"https://{i}/", dist=0.3) for i in (2, 3, 4, 5))
    fused = rrf([a, b, both], [c, d, both])
    assert fused[0].id == 1
    assert fused[0].score == pytest.approx(-2 / (RRF_K + 3))
    assert sorted(f.id for f in fused) == [1, 2, 3, 4, 5]
    assert fused[0].dist == 0.4  # pola z listy, nie wynik fuzji


def test_merge_best_bierze_lepszy_wynik():
    q1 = [chunk(1, dist=0.30), chunk(2, "https://b/", dist=0.40)]
    q2 = [chunk(2, "https://b/", dist=0.20), chunk(3, "https://c/", dist=0.35)]
    merged = merge_best([q1, q2])
    assert [(c.id, c.dist) for c in merged] == [(2, 0.20), (1, 0.30), (3, 0.35)]


def test_rerank_malejaco_i_lepszy_z_dwoch_zapytan():
    # T18
    chunks = [chunk(1, content="A"), chunk(2, content="B"), chunk(3, content="C")]
    reranker = FakeReranker({("q1", "A"): 0.2, ("q2", "A"): 0.7, ("q1", "B"): 0.9,
                             ("q2", "C"): 0.1})
    out = rerank(reranker, ["q1", "q2"], chunks)
    assert [(c.id, c.rerank) for c in out] == [(2, 0.9), (1, 0.7), (3, 0.1)]


def test_rerank_pusta_lista_bez_wywolania_modelu():
    reranker = FakeReranker({})
    assert rerank(reranker, ["q"], []) == [] and reranker.calls == []


def test_reranking_retriever_kandydaci_i_top_k():
    chunks = [chunk(i, f"https://{i}/", content=f"t{i}", dist=0.1 + i / 100) for i in range(30)]
    inner = FakeRetriever(chunks)
    reranker = FakeReranker({("q", "t19"): 0.99, ("q", "t5"): 0.5})
    out = RerankingRetriever(inner, reranker).search(["q"], 3)
    assert inner.calls == [(["q"], RERANK_CANDIDATES)]
    assert len(reranker.calls[0][1]) == RERANK_CANDIDATES
    assert [c.content for c in out] == ["t19", "t5", "t0"]  # remis 0 → dotychczasowa kolejność
