"""PgvectorRetriever na prawdziwym Postgresie z pgvector (testcontainers).

Wymaga działającego Dockera (bez niego testy są pomijane). Model embeddingów nie
jest pobierany — FakeEmbedder zwraca wektory przygotowane w conftest.py.
"""
import pytest

from tests.fakes import FakeEmbedder, FakeLLM
from tests.integration.conftest import (DZIEKANAT, EDUROAM, EDUROAM_URL, ELS_URL, GODZINY,
                                        LOKALIZACJA, NEWS_URL, POZA, REKRUTACJA, REKRUTACJA_URL,
                                        STYPENDIA, STYPENDIA_URL, WNP, cos_dist, vec)
from usbot.adapters.pgvector import PgvectorRetriever, connect, fulltext_query
from usbot.core.chat import ChatService
from usbot.core.prompts import NO_INFO
from usbot.core.ranking import with_neighbors

pytestmark = pytest.mark.integration

Q_DZIEKANAT = "Godziny dziekanatu WNP"
Q_GODZINY = "Godziny otwarcia"
Q_EDUROAM = "Jak skonfigurować eduroam?"
Q_ELS = "Gdzie wyrobić ELS?"
Q_REKRUTACJA = "Kiedy rekrutacja?"
Q_STYPENDIA = "Stypendium"
Q_POGODA = "Jaka jest pogoda?"

EMBEDDER = FakeEmbedder({
    Q_DZIEKANAT: vec({DZIEKANAT: 1, GODZINY: 0.2}),
    Q_GODZINY: vec({GODZINY: 1}),
    Q_EDUROAM: vec({EDUROAM: 1}),
    Q_ELS: vec({EDUROAM: 1}),  # embedding „myli” skrót z eduroamem — ratuje go FTS
    Q_REKRUTACJA: vec({REKRUTACJA: 1}),
    Q_STYPENDIA: vec({STYPENDIA: 1}),
    Q_POGODA: vec({POZA: 1}),
})


@pytest.fixture
def retriever(database_url):
    def make(**kw):
        return PgvectorRetriever(database_url, EMBEDDER, **kw)
    return make


def test_wektorowo_najblizsze_bez_duplikatow_i_limit_na_strone(retriever):
    # T05: do k wyników; kopia pod /student/en/ odpada, z WNP najwyżej 2 fragmenty
    out = retriever().search([Q_DZIEKANAT], 3)
    assert [c.url for c in out] == [WNP, WNP, "https://us.edu.pl/biblioteka/"]
    assert out[0].content.endswith("Godziny pracy: pon–pt 10–14")
    assert out[0].dist == pytest.approx(0, abs=1e-5)
    assert all(a.score <= b.score for a, b in zip(out, out[1:]))


def test_dist_to_odleglosc_kosinusowa(retriever):
    out = retriever().search([Q_DZIEKANAT], 3)
    expected = cos_dist(vec({DZIEKANAT: 1, GODZINY: 0.2}), vec({DZIEKANAT: 1, LOKALIZACJA: 0.5}))
    assert out[1].dist == pytest.approx(expected, abs=1e-5)


def test_bez_limitu_na_strone(retriever):
    out = retriever(max_per_url=0).search([Q_DZIEKANAT], 3)
    assert [c.url for c in out] == [WNP, WNP, WNP]


def test_hybrid_znajduje_skrot_dalekie_wektorowo(retriever):
    # T17: słowo tylko w fragmencie dalekim wektorowo; wynik ma dist, więc próg działa
    vector = retriever(mode="vector").search([Q_ELS], 2)
    hybrid = retriever(mode="hybrid").search([Q_ELS], 2)
    assert ELS_URL not in [c.url for c in vector]
    assert [c.url for c in hybrid] == [ELS_URL, EDUROAM_URL]
    assert hybrid[0].dist == pytest.approx(1.0, abs=1e-5)
    assert hybrid[0].score < 0  # −RRF


def test_nieznany_tryb(database_url):
    with pytest.raises(ValueError):
        PgvectorRetriever(database_url, EMBEDDER, mode="foo")


def test_kara_za_stare_aktualnosci_zmienia_kolejnosc_nie_dist(retriever):
    plain = retriever().search([Q_REKRUTACJA], 3)
    penalized = retriever(old_news_penalty=0.2).search([Q_REKRUTACJA], 3)
    assert plain[0].url == NEWS_URL
    assert [c.url for c in penalized] == [REKRUTACJA_URL, REKRUTACJA_URL, NEWS_URL]
    news = penalized[2]
    assert news.dist == pytest.approx(plain[0].dist)
    assert news.score == pytest.approx(news.dist + 0.2)


def test_kilka_zapytan_lepszy_wynik_fragmentu(retriever):
    # T12: fragment dostaje lepszy z dwóch wyników, każdy fragment raz
    out = retriever().search([Q_DZIEKANAT, Q_GODZINY], 4)
    by_url = {c.url: c for c in out}
    assert "https://us.edu.pl/biblioteka/" in by_url
    assert by_url["https://us.edu.pl/biblioteka/"].dist == pytest.approx(
        cos_dist(vec({GODZINY: 1}), vec({GODZINY: 1, LOKALIZACJA: 0.2})), abs=1e-5)
    assert len({c.id for c in out}) == len(out) == 4


def test_duze_k_wiecej_niz_ef_search(retriever):
    # k=10 → 50 kandydatów > domyślne hnsw.ef_search = 40
    out = retriever().search([Q_STYPENDIA], 10)
    assert len(out) == 10 and out[0].url == STYPENDIA_URL


def test_fulltext_query_bez_stop_slow_i_polskich_znakow(database_url):
    with connect(database_url) as conn:
        assert fulltext_query(conn, "Gdzie jest dziekanat WNŚiT?") == "'dziekanat' | 'wnsit'"
        assert fulltext_query(conn, "Gdzie to jest?") is None


def test_chat_service_na_prawdziwej_bazie(retriever):
    llm = FakeLLM(["Wybierz sieć eduroam."])
    svc = ChatService(retriever(), llm, top_k=3, max_distance=0.43, min_rerank_score=0.01)
    evs = list(svc.stream(Q_EDUROAM))
    assert evs[0] == {"type": "sources", "sources": [
        {"url": EDUROAM_URL, "title": "Eduroam", "page_type": "static", "published_at": None,
         "dist": pytest.approx(0, abs=1e-5), "rerank": None}]}
    assert "Krok 1: wybierz sieć eduroam" in llm.calls[0][0]["content"]

    assert list(svc.stream(Q_POGODA)) == [{"type": "refused", "message": NO_INFO},
                                          {"type": "done", "answer": NO_INFO}]


# --- pula połączeń (API) ---

@pytest.fixture
def pool(database_url):
    from usbot.adapters.pgvector import create_pool
    p = create_pool(database_url, min_size=1, max_size=1, timeout=5)
    p.open(wait=True)
    yield p
    p.close()


@pytest.mark.parametrize("mode", ["vector", "hybrid"])
def test_z_pula_te_same_wyniki_co_z_wlasnym_polaczeniem(database_url, pool, mode):
    plain = PgvectorRetriever(database_url, EMBEDDER, mode=mode)
    pooled = PgvectorRetriever(database_url, EMBEDDER, mode=mode, pool=pool)
    for q in (Q_DZIEKANAT, Q_ELS, Q_EDUROAM):
        for k in (3, 10):
            a = [(c.id, c.dist, c.score) for c in plain.search([q], k)]
            assert [(c.id, c.dist, c.score) for c in pooled.search([q], k)] == a


def test_ef_search_nie_zostaje_na_polaczeniu_z_puli(database_url, pool):
    PgvectorRetriever(database_url, EMBEDDER, pool=pool).search([Q_DZIEKANAT], 10)
    with pool.connection() as conn:  # max_size=1 — to samo połączenie
        assert conn.execute("SHOW hnsw.ef_search").fetchone()[0] == "40"


def test_ping_i_statement_timeout(database_url):
    import psycopg

    from usbot.adapters.pgvector import create_pool
    with create_pool(database_url, max_size=1, timeout=1) as pool:
        assert PgvectorRetriever(database_url, EMBEDDER, pool=pool).ping(timeout=5) == {
            "chunks": True}
        with pool.connection() as conn, pytest.raises(psycopg.errors.QueryCanceled):
            conn.execute("SELECT pg_sleep(5)")  # statement_timeout = 1 s z create_pool()



def test_sasiednie_fragmenty_z_bazy(retriever):
    # T53: chunks_by_ids zwraca fragmenty z liczbowym dist, a with_neighbors dokłada
    # następny fragment tej samej strony i sekcji z wynikiem znalezionego
    r = retriever(max_per_url=1)
    found = r.search([Q_DZIEKANAT], 1)
    assert [c.url for c in found] == [WNP]
    fetched = r.chunks_by_ids([found[0].id + 1])
    assert [c.url for c in fetched] == [WNP] and isinstance(fetched[0].score, float)
    out = with_neighbors(found, r.chunks_by_ids, pages=1, per_side=1, max_tokens=5000)
    assert [c.id for c in out] == [found[0].id, found[0].id + 1]
    assert out[1].dist == found[0].dist
