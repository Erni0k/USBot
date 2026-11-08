"""Zaliczanie pytań w teście 80 pytań (evaluation/evaluate_golden.py)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evaluation"))
import evaluate_golden as eg  # noqa: E402

A = "https://us.edu.pl/a/"


def item(answer="Odpowiedź", event="answered", sources=()):
    return {"answer": answer, "event": event, "sources": [{"url": u} for u in sources], "context": ""}


def gold(expect="answer", facts=(), forbidden=(), sources=()):
    return {"expect": expect, "expected_facts": list(facts), "forbidden_facts": list(forbidden),
            "gold_sources": list(sources)}


def test_wszystkie_fakty_zaliczaja():
    assert eg.score(item("Złóż wniosek w USOSweb."), gold(facts=[["USOS", "USOSweb"]]))["passed"]


def test_brak_faktu_nie_zalicza():
    s = eg.score(item("Idź do dziekanatu."), gold(facts=["USOSweb"]))
    assert not s["passed"] and s["missing"] == ["USOSweb"]


def test_zakazana_tresc_nie_zalicza_mimo_faktow():
    s = eg.score(item("Było to w 2016 roku, 23 grudnia."), gold(facts=["23 grudnia"], forbidden=["2016"]))
    assert not s["passed"] and s["forbidden"] == ["2016"]


def test_odmowa_przy_oczekiwanej_odpowiedzi_to_niesluszna_odmowa():
    s = eg.score(item("Nie mam tej informacji.", event="refused"), gold(facts=["USOSweb"]))
    assert not s["passed"] and s["false_refusal"]


@pytest.mark.parametrize("event", ["refused", "clarify"])
def test_odmowa_lub_dopytanie_zaliczaja_refuse_ok(event):
    assert eg.score(item("x", event=event), gold(expect="refuse_ok", facts=["USOSweb"]))["passed"]


def test_clarify_wymaga_dopytania():
    assert eg.score(item("x", event="clarify"), gold(expect="clarify"))["passed"]
    assert not eg.score(item("Godziny: 8–15."), gold(expect="clarify"))["passed"]


def test_strona_wzorcowa_w_kontekscie():
    assert eg.score(item(sources=[A]), gold(sources=[A]))["gold_in_context"] is True
    assert eg.score(item(), gold(sources=[A]))["gold_in_context"] is False
    assert eg.score(item(), gold())["gold_in_context"] is None
