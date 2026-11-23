"""Szacunek tokenów i limit promptu (usbot/core/tokens.py, ChatService.fit_prompt)."""
from datetime import date, datetime, timezone

from tests.fakes import FakeLLM, FakeRetriever, chunk
from usbot.core.chat import ChatService, drop_order
from usbot.core.prompts import build_system_prompt
from usbot.core.tokens import PROMPT_MARGIN, estimated_tokens, fits, prompt_tokens

LONG = "Treść fragmentu o zasadach studiów 2026/2027. " * 40


def service(chunks, max_tokens):
    retriever, llm = FakeRetriever(chunks), FakeLLM()
    return ChatService(retriever, llm, top_k=8, max_distance=0.43, min_rerank_score=None,
                       prompt_max_tokens=max_tokens), llm


def test_cyfry_drozsze_od_liter():
    assert estimated_tokens("1234567890") > 3 * estimated_tokens("abcdefghij")


def test_prompt_tokens_rosnie_z_liczba_wiadomosci():
    one = [{"role": "user", "content": "pytanie"}]
    assert prompt_tokens(one * 2) > prompt_tokens(one)
    assert fits(one, int(prompt_tokens(one) + PROMPT_MARGIN) + 1)
    assert not fits(one, int(prompt_tokens(one)))


def _budget_for(n_chunks, question="Pytanie?"):
    """Limit, w którym mieści się dokładnie n_chunks fragmentów LONG."""
    chunks = [chunk(i, f"https://us.edu.pl/{i}", dist=0.2 + i / 100, content=LONG) for i in range(1, n_chunks + 1)]
    prompt = [{"role": "system", "content": build_system_prompt(chunks)}, {"role": "user", "content": question}]
    return int(prompt_tokens(prompt) + PROMPT_MARGIN) + 1


def test_za_dlugi_prompt_traci_najdalsze_fragmenty_i_ich_zrodla():
    chunks = [chunk(i, f"https://us.edu.pl/{i}", dist=0.2 + i / 100, content=LONG) for i in range(1, 6)]
    svc, llm = service(chunks, _budget_for(3))
    events = list(svc.stream("Pytanie?"))
    assert [s["url"] for s in events[0]["sources"]] == [f"https://us.edu.pl/{i}" for i in (1, 2, 3)]
    system = llm.calls[0][0]["content"]
    assert "[Źródło 3]" in system and "[Źródło 4]" not in system
    assert fits(llm.calls[0], _budget_for(3))


def test_zostaje_co_najmniej_jeden_fragment():
    svc, llm = service([chunk(1, content=LONG), chunk(2, "https://us.edu.pl/2", content=LONG)], 500)
    list(svc.stream("Pytanie?"))
    assert "[Źródło 1]" in llm.calls[0][0]["content"] and "[Źródło 2]" not in llm.calls[0][0]["content"]


def test_potem_odpada_najstarsza_historia_a_pytanie_zostaje():
    history = [{"role": "user", "content": "Stare pytanie " + LONG}, {"role": "assistant", "content": "Stara odpowiedź " + LONG}]
    svc, llm = service([chunk(1, content="krótki fragment")], _budget_for(0) + 200)
    list(svc.stream("Nowe pytanie?", history))
    assert [m["role"] for m in llm.calls[0]] == ["system", "user"]
    assert llm.calls[0][-1]["content"] == "Nowe pytanie?"


def test_bez_limitu_nic_nie_jest_przycinane():
    chunks = [chunk(i, f"https://us.edu.pl/{i}", content=LONG) for i in range(1, 6)]
    svc, llm = service(chunks, None)
    events = list(svc.stream("Pytanie?"))
    assert len(events[0]["sources"]) == 5


def _news(i, year):
    c = chunk(i, f"https://us.edu.pl/komunikat-{i}", content=LONG)
    c.page_type, c.published_at = "news", datetime(year, 10, 1, tzinfo=timezone.utc)
    return c


def test_najpierw_odpada_najstarszy_komunikat_a_nie_najdalszy():
    # h25: odpadał najnowszy komunikat (2026/2027), zostawały starsze z nieaktualną kwotą
    chunks = [chunk(1, content=LONG), _news(2, 2024), _news(3, 2026), _news(4, 2025)]
    assert drop_order(chunks, "Jaki jest próg dochodu?", date(2026, 11, 23)).id == 2


def test_pytanie_o_przeszlosc_i_brak_komunikatow_najdalszy():
    chunks = [chunk(1, content=LONG), _news(2, 2024), chunk(3, "https://us.edu.pl/3", content=LONG)]
    assert drop_order(chunks, "Jaki był próg w 2024?", date(2026, 11, 23)).id == 3
    plain = [chunk(1), chunk(2, "https://us.edu.pl/2"), chunk(3, "https://us.edu.pl/3")]
    assert drop_order(plain, "Jaki jest próg?", date(2026, 11, 23)).id == 3


def test_pierwszy_fragment_nigdy_nie_odpada():
    chunks = [_news(1, 2010), _news(2, 2026)]
    assert drop_order(chunks, "Jaki jest próg?", date(2026, 11, 23)).id == 2
