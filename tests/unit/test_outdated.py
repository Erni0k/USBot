"""Ostrzeżenie o starych komunikatach w odpowiedzi (usbot/core/citations.py, ChatService)."""
from datetime import date, datetime, timezone

from tests.fakes import FakeLLM, FakeRetriever, chunk
from usbot.core.chat import ChatService
from usbot.core.citations import is_outdated, outdated_note, source_numbers, without_citations

TODAY = date(2026, 11, 23)


def news(i, published, page_type="news"):
    c = chunk(i, f"https://us.edu.pl/komunikat-{i}")
    c.page_type, c.published_at = page_type, datetime(*published, tzinfo=timezone.utc)
    return c


def test_stary_to_komunikat_sprzed_ponad_roku():
    assert is_outdated(news(1, (2025, 11, 22)), TODAY)
    assert not is_outdated(news(1, (2026, 7, 6)), TODAY)  # nabór do akademików na 2026/2027
    assert is_outdated(news(1, (2024, 11, 1), "event"), TODAY)
    static = chunk(2)
    static.published_at = datetime(2019, 1, 1, tzinfo=timezone.utc)
    assert not is_outdated(static, TODAY)  # data stron informacyjnych to data utworzenia


def test_ostrzezenie_tylko_dla_cytowanych_starych():
    chunks = [news(1, (2026, 11, 2)), news(2, (2024, 3, 22)), news(3, (2023, 10, 5), "event")]
    numbers = source_numbers(chunks)
    assert outdated_note([1], numbers, chunks, TODAY) == ""
    one = outdated_note([1, 2], numbers, chunks, TODAY)
    assert "źródło [2] (komunikat z 22 marca 2024 r.) ma ponad rok, więc informacje mogą być nieaktualne" in one
    both = outdated_note([2, 3], numbers, chunks, TODAY)
    assert "źródła [2] (komunikat z 22 marca 2024 r.), [3] (wydarzenie z 5 października 2023 r.) mają ponad rok" in both


def test_ostrzezenie_znika_z_historii():
    chunks = [news(1, (2024, 3, 22))]
    numbers = source_numbers(chunks)
    answer = "Próg wynosi 1570,50 zł [1]." + outdated_note([1], numbers, chunks, TODAY) + "\n\nŹródła:\n[1] https://us.edu.pl/k"
    assert without_citations([{"role": "assistant", "content": answer}])[0]["content"] == "Próg wynosi 1570,50 zł."


def run(question, chunks):
    svc = ChatService(FakeRetriever(chunks), FakeLLM(["Próg wynosi 1570,50 zł [1]."]), top_k=5,
                      max_distance=0.43, min_rerank_score=None, today=lambda: TODAY)
    return list(svc.stream(question))


def test_chat_service_dopisuje_ostrzezenie_i_oznacza_zrodlo():
    events = run("Jaki jest próg dochodu?", [news(1, (2024, 3, 22))])
    assert events[0]["sources"][0]["outdated"] is True
    answer = events[-1]["answer"]
    assert "**Uwaga:**" in answer and answer.index("**Uwaga:**") < answer.index("Źródła:")


def test_bez_ostrzezenia_przy_pytaniu_o_przeszlosc_i_aktualnym_zrodle():
    assert "**Uwaga:**" not in run("Jaki był próg dochodu w 2024?", [news(1, (2024, 3, 22))])[-1]["answer"]
    assert "**Uwaga:**" not in run("Jaki jest próg dochodu?", [news(1, (2026, 11, 2))])[-1]["answer"]
