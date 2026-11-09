"""Dopytywanie o przedmiot i system (usbot/core/clarify.py) oraz wpisy FAQ w cytowaniach."""
import pytest

from ingestion.ingest_faq import faq_chunks, load_entries
from tests.fakes import FakeLLM, FakeRetriever, chunk
from usbot.core.chat import ChatService
from usbot.core.citations import source_label, sources_footer, without_citations
from usbot.core.clarify import needs_detail
from usbot.core.prompts import build_context


def ask(*questions):
    return needs_detail([{"role": "user", "content": q} for q in questions])


@pytest.mark.parametrize("question", [
    "Ile nieobecności mogę mieć?", "Nie mam oceny", "Nie działa mi konto, pomożesz?",
    "nie moge sie zalogowac",
])
def test_pytania_bez_szczegolu(question):
    assert ask(question) is not None


@pytest.mark.parametrize("question", [
    "Ile nieobecności mogę mieć na laboratoriach?",
    "Czy na pewno mogę mieć trzy nieobecności na każdych zajęciach?",
    "Co zrobić, jeśli prowadzący nie wpisał mi oceny do USOSweb?",
    "Nie mam oceny z analizy", "Zapomniałem hasła do konta uczelnianego. Co zrobić?",
    "Nie działa mi poczta uczelniana, konto nie działa", "Jak zalogować się do USOSweb?",
    "Jak aktywować konto?", "Co zrobić, gdy nie działa mi konto?",
])
def test_pytania_z_pelna_odpowiedzia_nie_sa_zatrzymywane(question):
    assert ask(question) is None


def test_odpowiedz_na_dopytanie_nie_dopytuje_drugi_raz():
    assert ask("Nie działa mi konto", "USOSweb") is None


def test_opcje_systemu():
    assert {"code": "USOSweb", "name": "USOSweb"} in ask("Nie działa mi konto").options


def test_etykieta_wpisu_faq():
    assert source_label("https://us.edu.pl/a") == "https://us.edu.pl/a"
    assert source_label("faq://f05") == "FAQ (wpis sprawdzony ręcznie)"
    assert source_label("faq://f10#https://us.edu.pl/a") == "FAQ (wpis sprawdzony ręcznie): https://us.edu.pl/a"


def test_stopka_z_faq_jest_usuwana_z_historii():
    footer = sources_footer([1], {"faq://f05": 1})
    assert "[1] FAQ" in footer
    history = [{"role": "assistant", "content": f"Odpowiedź [1].{footer}"}]
    assert without_citations(history)[0]["content"] == "Odpowiedź."


def test_naglowek_wpisu_faq_w_kontekscie():
    assert "[Źródło 1 | wpis FAQ sprawdzony ręcznie]" in build_context([chunk(1, "faq://f05", page_type="faq")])


def test_dopytanie_przed_wyszukiwaniem_i_bez_llm():
    retriever, llm = FakeRetriever([chunk(1)]), FakeLLM()
    svc = ChatService(retriever, llm, top_k=5, max_distance=0.43, min_rerank_score=None,
                      clarify_details=True)
    events = list(svc.stream("Ile nieobecności mogę mieć?"))
    assert [e["type"] for e in events] == ["clarify", "done"]
    assert retriever.calls == [] and llm.calls == []


def test_bez_ustawienia_nie_dopytuje():
    svc = ChatService(FakeRetriever([chunk(1)]), FakeLLM(), top_k=5, max_distance=0.43,
                      min_rerank_score=None)
    assert "clarify" not in [e["type"] for e in svc.stream("Ile nieobecności mogę mieć?")]


def test_fragmenty_wpisu_faq():
    entry = {"id": "f10", "questions": ["Pierwsze?", "Drugie?"], "answer": "Odpowiedź.",
             "source": "https://us.edu.pl/a"}
    chunks = faq_chunks(entry)
    assert [c["url"] for c in chunks] == ["faq://f10#https://us.edu.pl/a"] * 2
    assert chunks[0]["body_hash"] == chunks[1]["body_hash"]
    assert chunks[0]["content_hash"] != chunks[1]["content_hash"]
    assert chunks[1]["content"] == "Drugie?\nOdpowiedź.\nStrona: https://us.edu.pl/a"
    assert faq_chunks({**entry, "source": None})[0]["url"] == "faq://f10"


def test_plik_faq_jest_poprawny():
    entries = load_entries()
    assert len({e["id"] for e in entries}) == len(entries)
    assert all(e["questions"] and e["answer"].strip() and e["origin"] for e in entries)
