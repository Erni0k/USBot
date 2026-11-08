"""Numery źródeł w odpowiedzi: filtr strumienia, stopka, historia dla LLM-a."""
import pytest

from usbot.core.citations import CitationFilter, source_numbers, sources_footer, without_citations
from usbot.core.models import Chunk


def feed_all(parts, n_sources=3):
    f = CitationFilter(n_sources)
    text = "".join(f.feed(p) for p in parts) + f.flush()
    return text, f.cited


def chunk(i, url):
    return Chunk(id=i, url=url, title=None, content="t", page_type=None, published_at=None, body_hash=None, score=0.3, dist=0.3)


def test_numery_stron_w_kolejnosci_wynikow():
    numbers = source_numbers([chunk(1, "a"), chunk(2, "b"), chunk(3, "a")])
    assert numbers == {"a": 1, "b": 2}


@pytest.mark.parametrize("parts", [["Tak [2]."], ["Tak [", "2", "]."], ["Tak [2", "]", "."]])
def test_numer_przeciety_miedzy_tokenami(parts):
    assert feed_all(parts) == ("Tak [2].", [2])


def test_numer_spoza_kontekstu_jest_usuwany():
    assert feed_all(["Tak [7]."]) == ("Tak .", [])


def test_lista_numerow_zostawia_poprawne():
    assert feed_all(["Tak [1, 9, 3]."]) == ("Tak [1, 3].", [1, 3])


def test_zwykly_nawias_kwadratowy_zostaje():
    assert feed_all(["Sala [B1] i [ok]"])[0] == "Sala [B1] i [ok]"


def test_niedomkniety_nawias_na_koncu_odpowiedzi_nie_ginie():
    assert feed_all(["Koniec [1"])[0] == "Koniec [1"


def test_stopka_tylko_cytowanych_stron_rosnaco():
    assert sources_footer([2, 1], {"https://a": 1, "https://b": 2, "https://c": 3}) == (
        "\n\nŹródła:\n[1] https://a\n[2] https://b")


def test_bez_cytatow_nie_ma_stopki():
    assert sources_footer([], {"https://a": 1}) == ""


def test_historia_dla_llm_bez_numerow_i_stopki():
    history = [{"role": "user", "content": "Pytanie [1]?"},
               {"role": "assistant", "content": "Odpowiedź [1, 2].\n\nŹródła:\n[1] https://a\n[2] https://b"}]
    assert without_citations(history) == [{"role": "user", "content": "Pytanie [1]?"},
                                          {"role": "assistant", "content": "Odpowiedź."}]


@pytest.mark.parametrize(("question", "kept"), [
    ("Jak działa Erasmus?", ["https://us.edu.pl/a"]),
    ("How do I connect to the university Wi-Fi?", ["https://us.edu.pl/a", "https://us.edu.pl/en/a"]),
])
def test_angielskie_strony_tylko_dla_pytan_nie_po_polsku(question, kept):
    from usbot.core.ranking import for_question_language
    chunks = [chunk(1, "https://us.edu.pl/a"), chunk(2, "https://us.edu.pl/en/a")]
    assert [c.url for c in for_question_language(chunks, question)] == kept


def test_gdy_sa_tylko_angielskie_strony_zostaja():
    from usbot.core.ranking import for_question_language
    chunks = [chunk(1, "https://us.edu.pl/en/a")]
    assert for_question_language(chunks, "Jak?") == chunks
