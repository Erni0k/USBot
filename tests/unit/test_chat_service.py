"""ChatService na atrapach portów: próg, dopytywanie o wydział, kontekst, zdarzenia."""
import pytest

from tests.fakes import FakeLLM, FakeRetriever, chunk
from usbot.core.chat import ChatService, cutoff
from usbot.core.faculties import CLARIFY_MESSAGE
from usbot.core.prompts import NO_INFO

EDUROAM = "https://eduroam.us.edu.pl/konfiguracja/"
WNP = "https://us.edu.pl/wydzial/wnp/dziekanat/"


def service(chunks=(), llm=None, *, top_k=5, max_distance=0.43, min_rerank_score=0.01):
    retriever = FakeRetriever(chunks)
    llm = llm or FakeLLM()
    return ChatService(retriever, llm, top_k=top_k, max_distance=max_distance,
                       min_rerank_score=min_rerank_score), retriever, llm


def events(svc, question, history=()):
    return list(svc.stream(question, history))


# --- próg odcięcia ---------------------------------------------------------------

def test_brak_wynikow_odmowa_bez_llm():
    svc, _, llm = service([])
    evs = events(svc, "Jaka jest pogoda w Katowicach?")
    assert evs == [{"type": "refused", "message": NO_INFO},
                   {"type": "done", "answer": NO_INFO}]
    assert llm.calls == []


def test_wszystkie_fragmenty_za_progiem_odleglosci_odmowa():
    svc, _, llm = service([chunk(1, dist=0.44), chunk(2, dist=0.50)])
    assert [e["type"] for e in events(svc, "Przepis na pierogi")] == ["refused", "done"]
    assert llm.calls == []


def test_prog_odcina_tylko_dalekie_fragmenty():
    svc, _, llm = service([chunk(1, EDUROAM, dist=0.30), chunk(2, dist=0.43),
                           chunk(3, "https://us.edu.pl/inna/", dist=0.4301)])
    evs = events(svc, "Jak połączyć się z eduroam?")
    system = llm.calls[0][0]["content"]
    assert "treść 1" in system and "treść 2" in system  # 0.43 jeszcze przechodzi (≤)
    assert "treść 3" not in system
    assert [s["url"] for s in evs[0]["sources"]] == [EDUROAM, "https://us.edu.pl/strona/"]


def test_z_rerankingiem_prog_na_wyniku_rerankera_nie_na_odleglosci():
    far_but_relevant = chunk(1, dist=0.60, rerank=0.90)
    near_but_irrelevant = chunk(2, dist=0.20, rerank=0.005)
    svc, _, llm = service([far_but_relevant, near_but_irrelevant])
    events(svc, "Jak uzyskać urlop dziekański?")
    system = llm.calls[0][0]["content"]
    assert "treść 1" in system and "treść 2" not in system


def test_cutoff_none_wylacza_prog():
    chunks = [chunk(1, dist=0.9), chunk(2, dist=0.1, rerank=0.0)]
    assert cutoff(chunks, None, None) == chunks
    assert cutoff(chunks, 0.5, 0.01) == []


# --- dopytywanie o wydział -------------------------------------------------------

def test_dziekanat_bez_wydzialu_clarify_bez_llm():
    svc, _, llm = service([chunk(1, WNP, dist=0.3)])
    evs = events(svc, "Jakie są godziny otwarcia dziekanatu?")
    assert [e["type"] for e in evs] == ["clarify", "done"]
    clarify = evs[0]
    assert {o["code"] for o in clarify["options"]} == {"WH", "WNP", "WNS", "WNŚiT", "WPiA",
                                                         "WSNE", "WTL"}
    assert clarify["message"] == CLARIFY_MESSAGE == evs[1]["answer"]
    assert llm.calls == []


def test_dziekanat_z_wydzialem_odpowiada_llm():
    svc, _, llm = service([chunk(1, WNP, dist=0.3)])
    evs = events(svc, "Godziny otwarcia dziekanatu WNP?")
    assert evs[0]["type"] == "sources" and len(llm.calls) == 1


def test_odpowiedz_na_dopytanie_trafia_do_llm():
    history = [{"role": "user", "content": "Gdzie jest dziekanat?"},
               {"role": "assistant", "content": CLARIFY_MESSAGE}]
    svc, retriever, llm = service([chunk(1, WNP, dist=0.3)])
    evs = events(svc, "Wydział Nauk Przyrodniczych", history)
    assert evs[0]["type"] == "sources"
    # zapytanie z historii skleja oba pytania użytkownika
    assert retriever.calls[0][0][0] == "Gdzie jest dziekanat?\nWydział Nauk Przyrodniczych"


def test_odmowa_ma_pierwszenstwo_przed_dopytaniem():
    svc, _, _ = service([chunk(1, dist=0.9)])
    assert events(svc, "Gdzie jest dziekanat?")[0]["type"] == "refused"


# --- budowa kontekstu i strumień ------------------------------------------------

def test_kontekst_wiadomosci_i_strumien_zdarzen():
    chunks = [chunk(1, EDUROAM, dist=0.25, content="Eduroam > Konfiguracja\nKrok 1",
                    title="Eduroam"),
              chunk(2, EDUROAM, dist=0.28, content="Eduroam > Konfiguracja\nKrok 2"),
              chunk(3, WNP, dist=0.35, content="WNP > Dziekanat\npon 10–14")]
    history = [{"role": "user", "content": "Cześć"},
               {"role": "assistant", "content": "Dzień dobry!"}]
    svc, _, llm = service(chunks, FakeLLM(["Skonfiguruj ", "eduroam."]))
    evs = events(svc, "Jak skonfigurować eduroam?", history)

    assert [e["type"] for e in evs] == ["sources", "token", "token", "done"]
    # źródła z wyników wyszukiwania: każda strona raz, z najlepszego fragmentu
    assert evs[0]["sources"] == [
        {"url": EDUROAM, "title": "Eduroam", "page_type": "static", "published_at": None,
         "dist": 0.25, "rerank": None},
        {"url": WNP, "title": None, "page_type": "static", "published_at": None,
         "dist": 0.35, "rerank": None},
    ]
    assert [e["text"] for e in evs[1:3]] == ["Skonfiguruj ", "eduroam."]
    assert evs[-1] == {"type": "done", "answer": "Skonfiguruj eduroam."}

    messages = llm.calls[0]
    assert messages[0]["role"] == "system"
    assert messages[1:] == [*history, {"role": "user", "content": "Jak skonfigurować eduroam?"}]
    system = messages[0]["content"]
    assert system.startswith("Jesteś asystentem Uniwersytetu Śląskiego")
    assert (f"<context>\n[Źródło: {EDUROAM}]\nEduroam > Konfiguracja\nKrok 1\n\n"
            f"[Źródło: {EDUROAM}]\nEduroam > Konfiguracja\nKrok 2\n\n"
            f"[Źródło: {WNP}]\nWNP > Dziekanat\npon 10–14\n</context>\n") in system


def test_top_k_przekazane_do_retrievera_i_pierwsze_pytanie_jednym_zapytaniem():
    svc, retriever, _ = service([chunk(1)], top_k=8)
    events(svc, "Jak złożyć wniosek o stypendium?")
    assert retriever.calls == [(["Jak złożyć wniosek o stypendium?"], 8)]


def test_follow_up_szuka_z_historia_i_samym_pytaniem():
    history = [{"role": "user", "content": "Godziny dziekanatu WH"},
               {"role": "assistant", "content": "pon–pt 9–13"}]
    svc, retriever, _ = service([chunk(1)])
    events(svc, "a wnst?", history)
    assert retriever.calls[0][0] == [
        "Godziny dziekanatu Wydział Humanistyczny (WH)\n"
        "a Wydział Nauk Ścisłych i Technicznych (wnst)?",
        "a Wydział Nauk Ścisłych i Technicznych (wnst)?",
    ]


def test_odpowiedz_skrotem_po_dopytaniu_szuka_z_pelna_nazwa():
    history = [{"role": "user", "content": "Gdzie jest dziekanat?"},
               {"role": "assistant", "content": CLARIFY_MESSAGE}]
    svc, retriever, _ = service([chunk(1)])
    events(svc, "WNP", history)
    assert retriever.calls[0][0] == [
        "Gdzie jest dziekanat?\nWydział Nauk Przyrodniczych (WNP)",
        "Wydział Nauk Przyrodniczych (WNP)",
    ]


def test_pusta_odpowiedz_llm():
    svc, _, _ = service([chunk(1)], FakeLLM([]))
    evs = events(svc, "Jak skonfigurować eduroam?")
    assert [e["type"] for e in evs] == ["sources", "done"] and evs[-1]["answer"] == ""


def test_wyjatek_retrievera_przechodzi_do_klienta():
    class Broken:
        def search(self, queries, k):
            raise ConnectionError("baza nie działa")

    svc = ChatService(Broken(), FakeLLM(), top_k=5, max_distance=0.43, min_rerank_score=0.01)
    with pytest.raises(ConnectionError):
        events(svc, "Jak skonfigurować eduroam?")
