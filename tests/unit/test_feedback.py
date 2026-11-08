"""POST /api/feedback: walidacja, limit, brak danych osobowych; logika eksportu i raportu."""
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from tests.fakes import FakeLLM, FakeRetriever
from usbot.api.chatlog import REDACTED
from usbot.api.main import create_app
from usbot.api.resources import Resources
from usbot.config import Settings
from usbot.core.chat import ChatService

ROOT = Path(__file__).resolve().parents[2]
SOURCE = {"url": "https://us.edu.pl/student/stypendia/", "dist": 0.31, "rerank": None}


def script(name: str):
    """Skrypty z evaluation/ nie są pakietem."""
    spec = importlib.util.spec_from_file_location(name, ROOT / "evaluation" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeStore:
    def __init__(self, error: Exception | None = None) -> None:
        self.records = []
        self.purged = []
        self.error = error

    def add(self, record) -> int:
        if self.error:
            raise self.error
        self.records.append(record)
        return len(self.records)

    def purge(self, retention_days: int) -> int:
        self.purged.append(retention_days)
        return 0


def client(store=None, **settings) -> TestClient:
    settings.setdefault("chat_log_enabled", False)
    service = ChatService(FakeRetriever(), FakeLLM(), top_k=5, max_distance=0.43, min_rerank_score=0.01)
    return TestClient(create_app(Settings(_env_file=None, **settings),
                                 lambda _s: Resources(service=service, feedback=store)))


def body(**overrides) -> dict:
    return {"question": "Jak dostać stypendium?", "answer": "Złóż wniosek.", "rating": -1,
            "sources": [SOURCE], **overrides}


# --- zapis -----------------------------------------------------------------------------

def test_ocena_trafia_do_magazynu_z_konfiguracja_serwera():
    store = FakeStore()
    with client(store, retrieval_mode="hybrid", max_distance=0.41, llm_model="m1") as c:
        r = c.post("/api/feedback", json=body(comment="  zła strona ", followup=True))
    assert (r.status_code, r.content) == (204, b"")
    rec = store.records[0]
    assert (rec.rating, rec.comment, rec.followup) == (-1, "zła strona", True)
    assert rec.sources == [{"url": SOURCE["url"], "dist": 0.31}]
    assert (rec.retrieval_mode, rec.rerank, rec.threshold, rec.llm_model) == ("hybrid", False, 0.41, "m1")


def test_przy_rerankingu_zapisywany_jest_prog_rerankera():
    store = FakeStore()
    with client(store, rerank=True, min_rerank_score=0.07) as c:
        c.post("/api/feedback", json=body(sources=[{**SOURCE, "rerank": 0.5}]))
    assert (store.records[0].rerank, store.records[0].threshold) == (True, 0.07)
    assert store.records[0].sources[0]["rerank"] == 0.5


def test_klient_nie_moze_podac_konfiguracji():
    with client(FakeStore()) as c:
        assert c.post("/api/feedback", json=body(llm_model="podrobiony")).status_code == 422


def test_dane_osobowe_usuwane_ze_wszystkich_tresci():
    store = FakeStore()
    leak = "jan.kowalski@us.edu.pl, tel. 600 700 800, album 312456"
    with client(store) as c:
        c.post("/api/feedback", json=body(question=leak, answer=f"Odp: {leak}", comment=leak))
    rec = store.records[0]
    for text in (rec.question, rec.answer, rec.comment):
        assert REDACTED in text
        for secret in ("kowalski", "600", "312456"):
            assert secret not in text


def test_redakcja_przed_przycieciem_pytania():
    store = FakeStore()
    with client(store, chat_log_max_question_chars=20) as c:
        c.post("/api/feedback", json=body(question="Mój numer 12345678901 i dalszy tekst"))
    assert "123" not in store.records[0].question
    assert len(store.records[0].question) == 20


def test_rekord_nie_ma_pola_z_adresem_ip():
    store = FakeStore()
    with client(store) as c:
        c.post("/api/feedback", json=body(), headers={"X-Forwarded-For": "203.0.113.7"})
    assert "203.0.113.7" not in repr(store.records[0])
    assert not {"ip", "client", "client_id"} & set(vars(store.records[0]))


@pytest.mark.parametrize("text, expected", [
    ("pyta\x00nie", "pytanie"),
    ("pytanie\ud800", "pytanie"),
])
def test_znaki_nie_do_zapisania_w_bazie_sa_usuwane(text, expected):
    store = FakeStore()
    with client(store) as c:
        # json.dumps ucieka NUL i surogaty, jak klient wysyłający \u0000 w JSON-ie
        r = c.post("/api/feedback", content=json.dumps(body(question=text)),
                   headers={"Content-Type": "application/json"})
    assert r.status_code == 204
    assert store.records[0].question == expected


def test_odleglosc_tuz_ponizej_zera_jest_poprawna():
    with client(FakeStore()) as c:
        assert c.post("/api/feedback", json=body(sources=[{**SOURCE, "dist": -2e-16}])).status_code == 204


# --- walidacja -------------------------------------------------------------------------

@pytest.mark.parametrize("overrides, field", [
    ({"rating": 0}, "rating"),
    ({"rating": 2}, "rating"),
    ({"rating": "up"}, "rating"),
    ({"question": "   "}, "question"),
    ({"answer": ""}, "answer"),
    ({"question": "x" * 2001}, "question"),
    ({"answer": "x" * 20_001}, "answer"),
    ({"comment": "x" * 501}, "comment"),
    ({"sources": [SOURCE] * 51}, "sources"),
    ({"sources": [{**SOURCE, "dist": 3}]}, "sources"),
    ({"sources": [{**SOURCE, "url": "https://evil.example/us.edu.pl"}]}, "sources"),
    ({"sources": [{**SOURCE, "url": "https://us.edu.pl.evil.example/"}]}, "sources"),
    ({"sources": [{**SOURCE, "url": "https://evil.example\\@us.edu.pl/"}]}, "sources"),
    ({"sources": [{**SOURCE, "url": "http://us.edu.pl/"}]}, "sources"),
    ({"sources": [{**SOURCE, "url": "javascript:alert(1)//us.edu.pl"}]}, "sources"),
    ({"sources": [{**SOURCE, "url": "https://us.edu.pl/\x00"}]}, "sources"),
])
def test_niepoprawna_ocena_422(overrides, field):
    store = FakeStore()
    with client(store) as c:
        r = c.post("/api/feedback", json=body(**overrides))
    assert r.status_code == 422
    assert r.json()["detail"][0]["loc"][1] == field
    assert store.records == []


def test_dozwolone_subdomeny_uczelni():
    with client(FakeStore()) as c:
        url = "https://usnet.us.edu.pl/siec/serwer-vpn/"
        assert c.post("/api/feedback", json=body(sources=[{**SOURCE, "url": url}])).status_code == 204


# --- limity i awarie -------------------------------------------------------------------

def test_rate_limit_429_z_retry_after():
    with client(FakeStore(), api_rate_limit_feedback="2/minute") as c:
        codes = [c.post("/api/feedback", json=body()).status_code for _ in range(3)]
        limited = c.post("/api/feedback", json=body())
    assert codes == [204, 204, 429]
    assert int(limited.headers["Retry-After"]) >= 1


def test_blad_bazy_503_bez_szczegolow():
    store = FakeStore(psycopg.OperationalError("connection to 10.0.0.5 user chatbot failed"))
    with client(store) as c:
        r = c.post("/api/feedback", json=body())
    assert r.status_code == 503
    assert "10.0.0.5" not in r.text


def test_retencja_uruchamiana_przy_starcie():
    store = FakeStore()
    with client(store, feedback_retention_days=12) as c:
        c.get("/api/faculties")
    assert store.purged == [12]


def test_serwer_wstaje_mimo_bledu_czyszczenia():
    class Broken(FakeStore):
        def purge(self, retention_days):
            raise psycopg.OperationalError("brak bazy")

    with client(Broken()) as c:
        assert c.get("/api/faculties").status_code == 200


# --- eksport ---------------------------------------------------------------------------

def row(id_, question="Jak dostać stypendium?", followup=False, comment=None):
    return {"id": id_, "created_at": datetime(2026, 11, 8, 12, tzinfo=UTC), "question": question,
            "answer": "Odp.", "sources": [{"url": SOURCE["url"], "dist": 0.31}], "comment": comment,
            "followup": followup, "retrieval_mode": "vector", "rerank": False, "threshold": 0.43,
            "llm_model": "qwen3:8b"}


def test_eksport_format_queries_jsonl():
    export = script("feedback_export")
    entry = export.build_entries([row(7)], [])[0]
    assert (entry["id"], entry["relevant"], entry["source"]) == ("fb-7", {}, "feedback")
    assert entry["question"] == "Jak dostać stypendium?"
    assert entry["feedback"]["config"]["llm_model"] == "qwen3:8b"


def test_eksport_laczy_powtorzenia_ale_nie_dopytania():
    export = script("feedback_export")
    entries = export.build_entries([
        row(1, comment="a"), row(2, "jak  DOSTAĆ stypendium?", comment="b"),
        row(3, "a telefon?", followup=True), row(4, "a telefon?", followup=True),
    ], [])
    assert [e["id"] for e in entries] == ["fb-1", "fb-3", "fb-4"]
    assert (entries[0]["feedback"]["count"], entries[0]["feedback"]["comments"]) == (2, ["a", "b"])


def test_eksport_pomija_znane_pytania():
    export = script("feedback_export")
    known = [{"id": "fb-1", "question": "x"}, {"id": "stypendium", "question": "Jak dostać stypendium?"}]
    assert export.build_entries([row(1, "x"), row(2)], known) == []


def test_kandydaci_laczone_z_trzech_list(tmp_path):
    export = script("feedback_export")

    class C:
        def __init__(self, url, title):
            self.url, self.title = url, title

    merged = export.merge_candidates([{"url": "a", "dist": 0.3}], [C("a", "A"), C("b", "B")], [C("b", "B"), C("c", "C")])
    assert merged == [{"url": "a", "title": "A", "found_by": ["bot", "wektorowo"]},
                      {"url": "b", "title": "B", "found_by": ["wektorowo", "słowa kluczowe"]},
                      {"url": "c", "title": "C", "found_by": ["słowa kluczowe"]}]


def test_zepsuty_plik_roboczy_nie_jest_nadpisywany(tmp_path):
    export = script("feedback_export")
    path = tmp_path / "plik.jsonl"
    path.write_text('{"id": "fb-1"\n', encoding="utf-8")
    with pytest.raises(SystemExit, match="linia 1"):
        export.read_jsonl(path)


def test_dopisanie_do_pliku_bez_nowej_linii(tmp_path):
    export = script("feedback_export")
    path = tmp_path / "plik.jsonl"
    path.write_text('{"id": "fb-1", "question": "q"}', encoding="utf-8")
    export.append_entries(path, [{"id": "fb-2", "question": "ż"}])
    assert [e["id"] for e in export.read_jsonl(path)] == ["fb-1", "fb-2"]


# --- evaluate.py -----------------------------------------------------------------------

def test_evaluate_pomija_nieoznaczone_pytania_z_ocen(tmp_path, capsys):
    evaluate = script("evaluate")
    path = tmp_path / "q.jsonl"
    lines = [
        {"id": "stare-oos", "question": "pogoda", "relevant": {}},
        {"id": "fb-1", "question": "nieoznaczone", "relevant": {}, "source": "feedback"},
        {"id": "fb-2", "question": "oznaczone", "relevant": {"https://us.edu.pl/a/": 2}, "source": "feedback"},
        {"id": "fb-3", "question": "spoza", "relevant": {}, "source": "feedback", "out_of_scope": True},
    ]
    path.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    assert [q["id"] for q in evaluate.load_queries(path)] == ["stare-oos", "fb-2", "fb-3"]
    assert "Pominięto 1" in capsys.readouterr().err


# --- raport ----------------------------------------------------------------------------

@pytest.mark.parametrize("question, topics", [
    ("Gdzie jest dziekanat WNP?", ["dziekanat"]),
    ("godziny DZIEKANATU", ["dziekanat"]),
    ("Jakie stypendia są dostępne?", ["stypendia i zapomogi"]),
    ("Nie mogę się zalogować, zapomniałem hasła", ["konto i hasło"]),
    ("Stypendium a urlop dziekański", ["stypendia i zapomogi", "urlopy"]),
    ("duplikat ELS", ["legitymacja"]),
    ("elsewhere", ["inne"]),
    ("przepis na pierogi", ["inne"]),
])
def test_tematy_pytan(question, topics):
    assert script("feedback_report").topics_of(question) == topics


def test_raport_zlicza_odsetek_i_tematy():
    report = script("feedback_report")
    text = report.render([(datetime(2026, 11, 2).date(), 4, 1), (datetime(2026, 11, 9).date(), 2, 2)],
                         ["Gdzie dziekanat?", "dziekanat WNP", "przepis na pierogi"], "week", 10)
    assert "2026-11-02" in text and "25.0%" in text and "100.0%" in text
    assert "razem" in text and "50.0%" in text
    assert "dziekanat" in text and "pierogi (1)" in text
    assert "przepis (1)" in text and "na (1)" not in text


def test_raport_bez_ocen():
    assert script("feedback_report").render([], [], "week", 10) == "Brak ocen w bazie."
