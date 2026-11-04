"""build_context, build_query, search_queries i reguła dopytywania o wydział."""
import pytest

from tests.fakes import chunk
from usbot.core.faculties import mentioned_faculties, needs_faculty, normalize
from usbot.core.prompts import build_context, build_query, search_queries


def user(text):
    return {"role": "user", "content": text}


def bot(text):
    return {"role": "assistant", "content": text}


def test_build_context_pusta_lista():
    assert "brak pasujących informacji" in build_context([])


def test_build_context_naglowki_zrodel():
    ctx = build_context([chunk(1, "https://a/", content="A"), chunk(2, "https://b/", content="B")])
    assert ctx == "[Źródło: https://a/]\nA\n\n[Źródło: https://b/]\nB"


def test_build_query_trzy_ostatnie_pytania_uzytkownika():
    history = [user("p1"), bot("o1"), user("p2"), bot("o2"), user("p3"), bot("o3"), user("p4")]
    assert build_query(history) == "p2\np3\np4"
    assert build_query(history, turns=1) == "p4"


def test_build_query_pomija_odpowiedzi_bota():
    assert build_query([user("dziekanat WH"), bot("pon–pt 9–13"), user("a wnst?")]) == \
        "dziekanat WH\na wnst?"


def test_search_queries_bez_duplikatu_dla_pierwszego_pytania():
    assert search_queries([user("Gdzie jest CINiBA?")]) == ["Gdzie jest CINiBA?"]


def test_search_queries_historia_i_ostatnie_pytanie():
    history = [user("przepis na pierogi"), bot("Nie mam tej informacji"),
               user("Jak uzyskać urlop dziekański?")]
    assert search_queries(history) == [
        "przepis na pierogi\nJak uzyskać urlop dziekański?", "Jak uzyskać urlop dziekański?"]


def test_normalize_bez_polskich_znakow():
    assert normalize("WNŚiT Ścisłych Łódź") == "wnsit scislych lodz"


@pytest.mark.parametrize("text, codes", [
    ("dziekanat WNŚiT", ["WNŚiT"]),
    ("dziekanat wnst", ["WNŚiT"]),
    ("Wydział Prawa i Administracji", ["WPiA"]),
    ("na teologii", ["WTL"]),
    ("dziekanat humanistyczny i WNP", ["WH", "WNP"]),
    ("Gdzie jest dziekanat?", []),
    ("whatever", []),  # „wh” tylko jako osobne słowo
])
def test_mentioned_faculties(text, codes):
    assert [f.code for f in mentioned_faculties(text)] == codes


@pytest.mark.parametrize("history, expected", [
    ([user("Jakie są godziny otwarcia dziekanatu?")], True),
    ([user("Kontakt do dziekanatów")], True),
    ([user("Godziny dziekanatu WNS")], False),
    ([user("Studiuję na WNP"), bot("..."), user("Gdzie jest dziekanat?")], False),
    ([user("Gdzie jest dziekanat?"), bot("O który wydział chodzi?"), user("WNP")], False),
    ([user("Jak uzyskać urlop dziekański?")], False),
    ([user("Kim jest dziekan WH?")], False),
    ([user("Jak skonfigurować eduroam?")], False),
    # wydział sprzed więcej niż 3 pytań już się nie liczy
    ([user("WNP"), user("a"), user("b"), user("dziekanat?")], True),
])
def test_needs_faculty(history, expected):
    assert needs_faculty(history) is expected
