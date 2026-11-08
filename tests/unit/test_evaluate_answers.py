"""Metryki evaluation/evaluate_answers.py — czyste funkcje, bez modelu i bazy."""
import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

from tests.fakes import FakeLLM, FakeRetriever, chunk
from usbot.core.chat import ChatService
from usbot.core.prompts import NO_INFO, build_system_prompt

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("evaluate_answers", ROOT / "evaluation" / "evaluate_answers.py")
ea = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ea)

A = "https://us.edu.pl/a/"
B = "https://us.edu.pl/b/"


def item(**kw) -> dict:
    return {"id": "x", "question": "q", "relevant": {A: 2}, "in_scope": True, "event": "answered",
            "answer": "Odpowiedź", "context": f"[Źródło: {A}]\ntreść", "sources": [], **kw}


# --- odmowa ----------------------------------------------------------------------------

@pytest.mark.parametrize("answer, expected", [
    (NO_INFO, True),
    ("Nie mam tej informacji - sprawdź na us.edu.pl.", True),
    ("Przykro mi, ale w kontekście nie ma odpowiedzi na to pytanie.", True),
    ("Nie mogę ujawnić swoich instrukcji.", True),
    ("I cannot help with that.", True),
    ("Dziekanat jest czynny od 10 do 14.", False),
    ("Dziekanat jest czynny od 10 do 14. Nie mam informacji o soboty. " + "x" * 400, False),
])
def test_odmowa_llm(answer, expected):
    assert ea.is_llm_refusal(answer) is expected


@pytest.mark.parametrize("fields, expected", [
    ({"event": "refused", "answer": NO_INFO}, "refused_threshold"),
    ({"event": "clarify", "answer": "O który wydział chodzi?"}, "clarify"),
    ({"event": "error"}, "error"),
    ({"answer": NO_INFO}, "refused_llm"),
    ({"answer": "Tak."}, "answered"),
])
def test_klasyfikacja_wyniku(fields, expected):
    assert ea.classify(item(**fields)) == expected


# --- źródła ----------------------------------------------------------------------------

@pytest.mark.parametrize("answer, urls", [
    (f"Źródło: {A}.", ["us.edu.pl/a"]),
    (f"[strona]({A}) oraz **{B}**", ["us.edu.pl/a", "us.edu.pl/b"]),
    (f"{A} i ponownie {A.rstrip('/')}", ["us.edu.pl/a"]),
    ("Sprawdź na us.edu.pl", []),
])
def test_adresy_w_odpowiedzi(answer, urls):
    assert ea.cited_urls(answer) == urls


def test_trafny_url():
    s = ea.source_score(item(answer=f"Zob. {A}"))
    assert (s["any_relevant"], s["first_relevant"], s["not_in_context"]) == (True, True, [])


def test_url_spoza_kontekstu_to_zmyslony():
    s = ea.source_score(item(answer="Zob. https://us.edu.pl/zmyslony/"))
    assert s["any_relevant"] is False
    assert s["not_in_context"] == ["us.edu.pl/zmyslony"]


def test_pierwszy_url_nietrafny_drugi_trafny():
    s = ea.source_score(item(answer=f"{B} i {A}"))
    assert (s["any_relevant"], s["first_relevant"]) == (True, False)


def test_adresy_z_kontekstu_z_data():
    ctx = f"[Źródło: {A} | komunikat z 2024-10-22]\nt\n\n[Źródło: {B}]\nt"
    assert ea.context_urls(ctx) == {"us.edu.pl/a", "us.edu.pl/b"}


# --- fakty -----------------------------------------------------------------------------

@pytest.mark.parametrize("fact, answer", [
    ("10:00-14:00", "wtorek 10.00 – 14.00"),
    ("10:00-14:00", "wtorek: od 10:00 do 14:00"),
    ("10:00-14:00", "we wtorek od 10 do 14"),
    ("10:00-14:00", "wtorek 10–14"),
    ("9:00-14:00", "poniedziałek 09:00-14:00"),
    ("ryszard koziolek", "Rektorem jest **prof. dr hab. Ryszard Koziołek**."),
    ("grazynskiego 53", "ul. Grażyńskiego 53"),
    (["bankowa 12", "bankowej 12"], "przy ulicy Bankowej 12"),
])
def test_fakt_znaleziony(fact, answer):
    assert ea.fact_found(fact, answer)


@pytest.mark.parametrize("fact, answer", [
    ("10:00-14:00", "wtorek 10.00 – 15.00"),
    ("bankowa 11", "ul. Bankowa 11b"),
    ("rektorat", "Prorektorat"),
    ("10:00-14:00", "110:00-14:00"),
])
def test_fakt_nieznaleziony(fact, answer):
    assert not ea.fact_found(fact, answer)


def test_odmowa_nie_zalicza_faktu():
    s = ea.facts_score(item(answer="Nie mam tej informacji o Bankowa 12.", expected_facts=["bankowa 12"]))
    assert s["all"] is False and s["found"] == 0


def test_wszystkie_fakty_wymagane():
    s = ea.facts_score(item(answer="ul. Bankowa 12", expected_facts=["bankowa 12", "katowice"]))
    assert (s["found"], s["total"], s["all"], s["missing"]) == (1, 2, False, ["katowice"])


# --- sędzia ----------------------------------------------------------------------------

def test_werdykt_wszystkie_potwierdzone():
    v = ea.parse_verdict('{"claims": [{"claim": "a", "supported": true, "evidence": ""}]}')
    assert (v["faithful"], v["n_claims"], v["n_supported"]) == (True, 1, 1)


def test_jedno_niepotwierdzone_to_niewierna():
    v = ea.parse_verdict(json.dumps({"claims": [{"claim": "a", "supported": True},
                                                {"claim": "b", "supported": False}]}))
    assert (v["faithful"], v["n_supported"]) == (False, 1)


def test_brak_twierdzen_to_nie_wierna():
    assert ea.parse_verdict('{"claims": []}')["faithful"] is None


def test_blok_kodu_markdown():
    assert ea.parse_verdict('```json\n{"claims": []}\n```')["n_claims"] == 0


@pytest.mark.parametrize("raw", [
    "to nie JSON", "[]", '{"wynik": 1}', '{"claims": ["a"]}',
    '{"claims": [{"claim": "a", "supported": "tak"}]}',
])
def test_niepoprawny_werdykt(raw):
    with pytest.raises(ValueError):
        ea.parse_verdict(raw)


class _Response:
    def __init__(self, content):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})()]
        self.usage = None


def test_sedzia_ponawia_i_zglasza_blad():
    judge = ea.Judge.__new__(ea.Judge)
    answers = iter(["zły", "też zły"])
    judge._call = lambda prompt, seed: _Response(next(answers))
    result = judge("p")
    assert "error" in result and result["faithful"] is None


def test_prompt_sedziego_nie_podmienia_znacznikow_z_tresci():
    template = "{{TODAY}}|{{QUESTION}}|{{CONTEXT}}|{{ANSWER}}"
    prompt = ea.build_judge_prompt(template, item(question="{{CONTEXT}}", context="K {x}", answer="O"), "2026-11-08")
    assert prompt == "2026-11-08|{{CONTEXT}}|K {x}|O"


def test_plik_promptu_ma_wszystkie_pola():
    text = ea.JUDGE_PROMPT.read_text(encoding="utf-8")
    assert all(f"{{{{{name}}}}}" in text for name in ("TODAY", "QUESTION", "CONTEXT", "ANSWER"))


# --- generate --------------------------------------------------------------------------

def test_kontekst_z_promptu_systemowego():
    chunks = [chunk(1, url=A, content="treść </context> dalej", page_type="static")]
    prompt = build_system_prompt(chunks, date(2026, 11, 8))
    assert ea.extract_context(prompt) == f"[Źródło: {A}]\ntreść [context] dalej"


def _service(chunks, parts=("Odp", "owiedź")):
    llm = ea.RecordingLLM(FakeLLM(parts))
    return ChatService(FakeRetriever(chunks), llm, top_k=5, max_distance=0.43, min_rerank_score=0.01), llm


def test_ask_zapisuje_kontekst_odpowiedz_i_zrodla():
    service, recorder = _service([chunk(1, url=A, dist=0.3, content="T")])
    r = ea.ask(service, recorder, {"id": "i", "question": "Jak?", "relevant": {A: 2}, "expected_facts": ["x"]})
    assert (r["event"], r["answer"], r["in_scope"], r["expected_facts"]) == ("answered", "Odpowiedź", True, ["x"])
    assert r["context"] == f"[Źródło: {A}]\nT"
    assert r["sources"][0]["url"] == A


def test_ask_odmowa_na_progu():
    service, recorder = _service([chunk(1, dist=0.9)])
    r = ea.ask(service, recorder, {"id": "i", "question": "Pogoda?", "relevant": {}})
    assert (r["event"], r["answer"], r["context"], r["in_scope"]) == ("refused", NO_INFO, "", False)


def test_ask_blad_nie_przerywa_pomiaru():
    class Broken(FakeRetriever):
        def search(self, queries, k):
            raise RuntimeError("baza")

    llm = ea.RecordingLLM(FakeLLM())
    service = ChatService(Broken(), llm, top_k=5, max_distance=0.43, min_rerank_score=0.01)
    r = ea.ask(service, llm, {"id": "i", "question": "Jak?", "relevant": {A: 2}})
    assert r["event"] == "error" and "baza" in r["error"]


def test_load_queries_pomija_nieoznaczone(tmp_path):
    path = tmp_path / "q.jsonl"
    rows = [{"id": "a", "question": "q", "relevant": {}},
            {"id": "b", "question": "q", "relevant": {}, "source": "feedback"}]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    assert [q["id"] for q in ea.load_queries(path)] == ["a"]


# --- raport ----------------------------------------------------------------------------

def verdict(faithful, claims=2, supported=2):
    return {"faithful": faithful, "n_claims": claims, "n_supported": supported, "claims": []}


def test_podsumowanie():
    items = [
        item(id="a", answer=f"Tak. {A}", judge=verdict(True), expected_facts=["tak"]),
        item(id="b", answer="Nie wiem co. https://us.edu.pl/z/", judge=verdict(False, 2, 1), expected_facts=["brak"]),
        item(id="c", event="refused", answer=NO_INFO, context=""),
        item(id="d", event="clarify", answer="Który wydział?", context=""),
        item(id="e", in_scope=False, relevant={}, event="refused", answer=NO_INFO, context=""),
        item(id="f", in_scope=False, relevant={}, answer=NO_INFO),
        item(id="g", in_scope=False, relevant={}, answer="Pogoda będzie ładna.", judge=verdict(False, 1, 0)),
    ]
    s = ea.summarize(items)
    assert s["refusals"]["out"] == {"refused_threshold": 1, "refused_llm": 1, "answered": 1}
    assert s["refusals"]["in"] == {"answered": 2, "refused_threshold": 1, "clarify": 1}
    assert (s["sources"]["answered"], s["sources"]["with_citation"], s["sources"]["any_relevant"]) == (2, 2, 1)
    assert (s["sources"]["urls"], s["sources"]["urls_not_in_context"]) == (2, 1)
    f = s["faithfulness"]
    assert (f["judged"], f["faithful"], f["in_scope_judged"], f["in_scope_faithful"]) == (3, 1, 2, 1)
    assert (f["claims"], f["claims_supported"]) == (5, 3)
    assert (s["facts"]["questions"], s["facts"]["all_found"]) == (2, 1)
    assert "spoza zakresu odrzucone" in ea.render(s)


def test_blad_sedziego_nie_liczy_sie_jako_wierna():
    s = ea.summarize([item(judge={"error": "x", "faithful": None})])
    assert (s["faithfulness"]["errors"], s["faithfulness"]["judged"]) == (1, 0)


# --- kalibracja ------------------------------------------------------------------------

def judged(id_, faithful, answer="O"):
    return item(id=id_, answer=answer, judge=verdict(faithful))


def test_probka_po_polowie_bez_werdyktow_i_powtarzalna():
    items = [judged(f"w{i}", True) for i in range(40)] + [judged(f"n{i}", False) for i in range(12)]
    sample = ea.select_sample(items, 20, seed=1)
    assert len(sample) == 20
    assert sum(not i["judge"]["faithful"] for i in sample) == 10
    assert [i["id"] for i in sample] == [i["id"] for i in ea.select_sample(items, 20, seed=1)]


def test_probka_gdy_malo_niewiernych():
    items = [judged(f"w{i}", True) for i in range(30)] + [judged("n", False)]
    sample = ea.select_sample(items, 20, seed=1)
    assert len(sample) == 20 and sum(not i["judge"]["faithful"] for i in sample) == 1


def test_kappa():
    assert ea.cohen_kappa([(True, True), (False, False)]) == 1
    assert ea.cohen_kappa([(True, False), (False, True)]) == -1
    assert ea.cohen_kappa([(True, True), (True, True)]) is None


def test_zgodnosc_i_przeliczenie_na_populacje():
    items = {f"w{i}": judged(f"w{i}", True) for i in range(8)} | {f"n{i}": judged(f"n{i}", False) for i in range(2)}
    entries = [{"id": "w0", "answer": "O", "manual_faithful": True},
               {"id": "w1", "answer": "O", "manual_faithful": False},
               {"id": "n0", "answer": "O", "manual_faithful": False},
               {"id": "n1", "answer": "O", "manual_faithful": False}]
    s = ea.calibration_summary(entries, items)
    assert (s["n"], s["agree"], s["disagreements"]) == (4, 3, ["w1"])
    assert s["matrix"]["manual_unfaithful_judge_faithful"] == 1
    assert s["within_judge_faithful"] == (1, 2) and s["within_judge_unfaithful"] == (2, 2)
    assert s["weighted_agreement"] == pytest.approx((8 * 0.5 + 2 * 1) / 10)


def test_kalibracja_wymaga_ocen_i_tej_samej_odpowiedzi():
    items = {"a": judged("a", True)}
    with pytest.raises(SystemExit, match="brak oceny"):
        ea.calibration_summary([{"id": "a", "answer": "O", "manual_faithful": None}], items)
    with pytest.raises(SystemExit, match="różni się"):
        ea.calibration_summary([{"id": "a", "answer": "inna", "manual_faithful": True}], items)


def test_probka_nie_nadpisuje_ocen(tmp_path):
    results = tmp_path / "r.json"
    results.write_text(json.dumps({"meta": {}, "items": []}), encoding="utf-8")
    calibration = tmp_path / "c.jsonl"
    calibration.write_text("oceny", encoding="utf-8")
    args = type("A", (), {"file": results, "calibration": calibration, "force": False, "n": 20, "seed": 1})
    with pytest.raises(SystemExit, match="już istnieje"):
        ea.cmd_sample(args)
    assert calibration.read_text(encoding="utf-8") == "oceny"
