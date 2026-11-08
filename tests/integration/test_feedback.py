"""Oceny na prawdziwym Postgresie: migracja, zapis przez API, retencja, eksport, raport."""
import importlib.util
import json
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from tests.fakes import FakeEmbedder, FakeLLM, FakeRetriever
from tests.integration.conftest import STYPENDIA, STYPENDIA_URL, vec
from usbot.adapters.pgvector import PgvectorRetriever, create_pool
from usbot.api.feedback import SCHEMA, PgFeedbackStore, ensure_schema
from usbot.api.main import create_app
from usbot.api.resources import Resources
from usbot.config import Settings
from usbot.core.chat import ChatService

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]


def script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "evaluation" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def pool(database_url):
    with psycopg.connect(database_url) as conn:
        ensure_schema(conn)
        conn.execute("TRUNCATE feedback RESTART IDENTITY")
    pool = create_pool(database_url, min_size=1, max_size=2, timeout=5)
    pool.open(wait=True)
    yield pool
    pool.close()


def make_client(pool) -> TestClient:
    service = ChatService(FakeRetriever(), FakeLLM(), top_k=5, max_distance=0.43, min_rerank_score=0.01)
    settings = Settings(_env_file=None, chat_log_enabled=False, api_rate_limit_feedback="100/minute",
                        retrieval_mode="vector", max_distance=0.43, llm_model="qwen3:8b")
    return TestClient(create_app(settings, lambda _s: Resources(service=service, feedback=PgFeedbackStore(pool))))


@pytest.fixture
def api(pool):
    with make_client(pool) as client:
        yield client


def send(api, question="Jak dostać stypendium?", rating=-1, **extra):
    payload = {"question": question, "answer": "Złóż wniosek.", "rating": rating,
               "sources": [{"url": STYPENDIA_URL, "dist": 0.31, "rerank": None}], **extra}
    r = api.post("/api/feedback", json=payload)
    assert r.status_code == 204, r.text


def test_ensure_schema_w_bazie_bez_tabeli_i_powtorzone(database_url):
    with psycopg.connect(database_url) as conn:
        conn.execute("DROP TABLE feedback")
        ensure_schema(conn)
        ensure_schema(conn)
        assert conn.execute("SELECT count(*) FROM feedback").fetchone()[0] == 0


def test_init_sql_i_migracja_daja_ten_sam_schemat(database_url):
    query = ("SELECT column_name, data_type, is_nullable FROM information_schema.columns "
             "WHERE table_name = 'feedback' ORDER BY ordinal_position")
    with psycopg.connect(database_url) as conn:
        conn.execute("DROP TABLE feedback")
        ensure_schema(conn)
        migrated = conn.execute(query).fetchall()
        conn.execute("DROP TABLE feedback")
        init_sql = (ROOT / "infra" / "init.sql").read_text(encoding="utf-8")
        start = init_sql.index("CREATE TABLE IF NOT EXISTS feedback")
        conn.execute(init_sql[start:])
        conn.commit()
        assert conn.execute(query).fetchall() == migrated
    assert len(SCHEMA) == 2


def test_zapis_przez_api_w_istniejacej_bazie_bez_tabeli(pool, database_url):
    with psycopg.connect(database_url) as conn:
        conn.execute("DROP TABLE feedback")
    with make_client(pool) as api:
        send(api, "Kontakt: anna@example.com, jak dostać stypendium?", comment="Zła strona")
    with psycopg.connect(database_url) as conn:
        row = conn.execute("SELECT question, answer, sources, rating, comment, followup, retrieval_mode, "
                           "rerank, threshold, llm_model, created_at FROM feedback").fetchone()
    assert row[0] == "Kontakt: [usunięto], jak dostać stypendium?"
    assert row[1:10] == ("Złóż wniosek.", [{"url": STYPENDIA_URL, "dist": 0.31}], -1, "Zła strona", False,
                         "vector", False, 0.43, "qwen3:8b")
    assert row[10] is not None


def test_ograniczenie_oceny_w_bazie(database_url):
    with psycopg.connect(database_url) as conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO feedback (rating, retrieval_mode, rerank, threshold, llm_model) "
                     "VALUES (0, 'vector', false, 0.4, 'm')")


def test_retencja_zeruje_tresci_i_zostawia_ocene(api, pool, database_url):
    send(api, "stare", comment="k")
    send(api, "nowe", comment="k")
    with psycopg.connect(database_url) as conn:
        conn.execute("UPDATE feedback SET created_at = now() - interval '31 days' WHERE question = 'stare'")
    store = PgFeedbackStore(pool)
    assert store.purge(30) == 1
    assert store.purge(30) == 0
    with psycopg.connect(database_url) as conn:
        rows = conn.execute("SELECT question, answer, comment, rating, sources <> '[]' FROM feedback ORDER BY id").fetchall()
    assert rows == [(None, None, None, -1, True), ("nowe", "Złóż wniosek.", "k", -1, True)]


def test_eksport_na_prawdziwych_danych_nie_rusza_oznaczen(api, database_url, tmp_path):
    export = script("feedback_export")
    send(api, "Jak dostać stypendium?", comment="zła strona")
    send(api, "Jak dostać stypendium?")
    send(api, "dobre pytanie", rating=1)
    send(api, "a telefon?", followup=True)
    out = tmp_path / "roboczy.jsonl"
    queries = tmp_path / "queries.jsonl"
    queries.write_text("", encoding="utf-8")

    first = export.export(database_url, out, queries)
    assert [e["question"] for e in first] == ["Jak dostać stypendium?", "a telefon?"]
    assert first[0]["feedback"]["count"] == 2
    assert first[1]["feedback"]["followup"] is True

    # Oznaczenie w pliku, nowa ocena, drugi eksport
    labeled = export.read_jsonl(out)
    labeled[0]["relevant"] = {STYPENDIA_URL: 2}
    out.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in labeled), encoding="utf-8")
    send(api, "Gdzie jest akademik?")
    second = export.export(database_url, out, queries)
    assert [e["question"] for e in second] == ["Gdzie jest akademik?"]
    after = export.read_jsonl(out)
    assert len(after) == 3
    assert after[0]["relevant"] == {STYPENDIA_URL: 2}
    assert export.export(database_url, out, queries) == []


def test_kandydaci_z_obu_list(api, database_url, tmp_path):
    export = script("feedback_export")
    send(api, "stypendium socjalne")
    embedder = FakeEmbedder({"stypendium socjalne": vec({STYPENDIA: 1})})
    retriever = PgvectorRetriever(database_url, embedder, mode="vector", max_per_url=0)
    queries = tmp_path / "queries.jsonl"
    queries.write_text("", encoding="utf-8")
    entry = export.export(database_url, tmp_path / "out.jsonl", queries, retriever=retriever)[0]
    candidates = {c["url"]: c for c in entry["feedback"]["candidates"]}
    assert set(candidates[STYPENDIA_URL]["found_by"]) == {"bot", "wektorowo", "słowa kluczowe"}
    assert candidates[STYPENDIA_URL]["title"] == "Stypendia"
    assert len(candidates) > 1


def test_raport_na_prawdziwych_danych(api, database_url):
    report = script("feedback_report")
    send(api, "Gdzie jest dziekanat?")
    send(api, "dziekanat WNP")
    send(api, "dobre", rating=1)
    with psycopg.connect(database_url) as conn:
        periods = report.fetch_periods(conn, "week")
        questions = report.fetch_down_questions(conn)
    assert [p[1:] for p in periods] == [(3, 2)]
    assert sorted(questions) == ["Gdzie jest dziekanat?", "dziekanat WNP"]
    text = report.render(periods, questions, "week", 5)
    assert "66.7%" in text and "dziekanat" in text
