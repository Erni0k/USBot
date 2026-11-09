"""Reguły aktualności i odbiorcy strony (usbot/core/scope.py)."""
from datetime import date, datetime, timezone

import pytest

from tests.fakes import chunk
from usbot.core.scope import (asks_about_past, asks_about_recruitment, for_audience,
                              without_outdated)

TODAY = date(2026, 11, 9)


def old_news(i, url="https://us.edu.pl/stary"):
    c = chunk(i, url)
    c.page_type, c.published_at = "news", datetime(2020, 3, 1, tzinfo=timezone.utc)
    return c


@pytest.mark.parametrize(("question", "past"), [
    ("Jakie były zasady w czasie pandemii?", True),
    ("Co się działo na uczelni w 2021 roku?", True),
    ("Jak wyglądała rekrutacja w zeszłym roku?", True),
    ("Kiedy jest sesja w roku 2026/2027?", False),
    ("Czy obecność na wykładach jest obowiązkowa?", False),
])
def test_pytanie_o_przeszlosc(question, past):
    assert asks_about_past(question, TODAY) is past


def test_covid_pomijany_przy_pytaniu_o_biezace_zasady():
    chunks = [chunk(1, "https://us.edu.pl/uczelnia/status-zabezpieczen-covid-19/poziom-2"),
              chunk(2, "https://us.edu.pl/a")]
    out = without_outdated(chunks, "Czy są zajęcia?", "covid", TODAY, 730)
    assert [c.id for c in out] == [2]


def test_tresc_o_pandemii_tez_jest_rozpoznawana():
    chunks = [chunk(1, "https://us.edu.pl/a", content="Zajęcia w czasie pandemii są zdalne.")]
    assert without_outdated(chunks, "Czy są zajęcia?", "covid", TODAY, 730) == []


def test_treści_historyczne_osiagalne_przy_pytaniu_o_przeszlosc():
    chunks = [chunk(1, "https://us.edu.pl/covid"), old_news(2)]
    assert len(without_outdated(chunks, "Jakie były zasady podczas COVID?", "all", TODAY, 730)) == 2


def test_stare_komunikaty_pomijane_tylko_w_trybie_all():
    chunks = [old_news(1), chunk(2, "https://us.edu.pl/a")]
    assert len(without_outdated(chunks, "Czy są zajęcia?", "covid", TODAY, 730)) == 2
    assert [c.id for c in without_outdated(chunks, "Czy są zajęcia?", "all", TODAY, 730)] == [2]


def test_tryb_off_nic_nie_zmienia():
    chunks = [chunk(1, "https://us.edu.pl/covid"), old_news(2)]
    assert without_outdated(chunks, "Czy są zajęcia?", "off", TODAY, 730) == chunks


@pytest.mark.parametrize("question", [
    "Jak wygląda rekrutacja na informatykę?", "Ile punktów trzeba mieć z matury?",
    "Jakie kierunki oferuje uczelnia?", "Czy cudzoziemcy płacą za studia?",
])
def test_pytania_o_rekrutacje(question):
    assert asks_about_recruitment(question)


CANDIDATE_PAGES = ["https://rekrutacja.us.edu.pl/wymagane-dokumenty",
                   "https://us.edu.pl/wydzial/wnst/studia/kandydat",
                   "https://us.edu.pl/3-czerwca-rusza-rekrutacja-na-studia-w-uniwersytecie"]


@pytest.mark.parametrize("url", CANDIDATE_PAGES)
def test_strony_dla_kandydatow_pomijane_w_pytaniach_studentow(url):
    assert for_audience([chunk(1, url)], "Nie mam oceny w USOS, co zrobić?") == []
    assert len(for_audience([chunk(1, url)], "Jak wygląda rekrutacja?")) == 1


@pytest.mark.parametrize("url", ["https://rekrutacja.us.edu.pl/akademiki",
                                 "https://us.edu.pl/rekrutacja-na-wyjazdy-na-studia-erasmus",
                                 "https://us.edu.pl/student/studia"])
def test_strony_przydatne_studentom_zostaja(url):
    assert len(for_audience([chunk(1, url)], "Jak się zapisać?")) == 1


def test_karta_kierunku_tylko_przy_pytaniu_o_kierunek():
    card = [chunk(1, "https://rekrutacja.us.edu.pl/karta-kierunku/administracja")]
    assert for_audience(card, "Jak skontaktować się z administracją uczelni?") == []
    assert len(for_audience(card, "Ile godzin praktyk jest na administracji?")) == 1
