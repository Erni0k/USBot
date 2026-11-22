"""Slang w zapytaniu do wyszukiwarki (usbot/core/slang.py) i jego użycie w ChatService."""
import pytest

from tests.fakes import FakeLLM, FakeRetriever, chunk
from usbot.core.chat import ChatService
from usbot.core.slang import expand_slang


@pytest.mark.parametrize("text, expected", [
    ("Dostałem pałę, mogę iść na komisa?",
     "Dostałem pałę (ocena niedostateczna), mogę iść na komisa (egzamin komisyjny)?"),
    ("czy na dziekance mogę pisać kolosa", "czy na dziekance (urlop dziekański) mogę pisać kolosa (kolokwium)"),
    ("Mam warun z analizy", "Mam warun (warunkowe zaliczenie semestru) z analizy"),
    ("ile nieobek na wfie", "ile nieobek (nieobecność) na wfie (wychowanie fizyczne)"),
    ("zgubiłem legitkę", "zgubiłem legitkę (legitymacja studencka)"),
])
def test_slang_dostaje_urzedowy_odpowiednik(text, expected):
    assert expand_slang(text) == expected


@pytest.mark.parametrize("text", [
    "Jaki jest warunek otrzymania stypendium?", "Kto zasiada w komisji egzaminacyjnej?",
    "Gdzie jest dziekanat?", "Jak złożyć wniosek o urlop dziekański?", "Ile kosztuje akademik?",
])
def test_slowa_urzedowe_bez_zmian(text):
    assert expand_slang(text) == text


def test_odpowiednik_najwyzej_raz():
    assert expand_slang("pała i jeszcze jedna pała") == "pała (ocena niedostateczna) i jeszcze jedna pała"


def test_chat_service_rozwija_slang_tylko_w_zapytaniu_i_tylko_z_ustawieniem():
    for flag, query in ((True, "mam pałę (ocena niedostateczna)"), (False, "mam pałę")):
        retriever, llm = FakeRetriever([chunk(1, dist=0.3)]), FakeLLM()
        svc = ChatService(retriever, llm, top_k=5, max_distance=0.43, min_rerank_score=None,
                          slang_expansion=flag)
        list(svc.stream("mam pałę"))
        assert retriever.calls[0][0] == [query]
        assert llm.calls[0][-1]["content"] == "mam pałę"
