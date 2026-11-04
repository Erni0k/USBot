"""Zabezpieczenia publicznego API: limity żądań, rate limiting, limit strumieni, CORS,
nagłówki, dziennik rozmów bez danych osobowych."""
import asyncio
import json
import logging
import threading
from datetime import UTC, date, datetime

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tests.fakes import FakeLLM, FakeRetriever, chunk
from usbot.api.chatlog import REDACTED, ChatLog, DailySalt, redact
from usbot.api.limits import Busy, StreamSlots
from usbot.api.main import _HideClientAddress, create_app
from usbot.api.resources import Resources
from usbot.config import Settings
from usbot.core.chat import ChatService

WNP = "https://us.edu.pl/wydzial/wnp/student/dziekanat/"


class TopicRetriever(FakeRetriever):
    """Fragmenty o dziekanacie; pytanie o pierogi dostaje tylko dalekie (→ refused)."""

    def search(self, queries, k):
        self.calls.append((list(queries), k))
        if any("pierogi" in q for q in queries):
            return [chunk(9, dist=0.7)]
        return [chunk(1, url=WNP, dist=0.30), chunk(2, dist=0.35)]


def service():
    return ChatService(TopicRetriever(), FakeLLM(["Pon", "–pt 9–13."]), top_k=5,
                       max_distance=0.43, min_rerank_score=0.01)


def make_app(svc=None, checks=None, **settings):
    settings.setdefault("chat_log_enabled", False)
    return create_app(Settings(_env_file=None, **settings),
                      lambda _s: Resources(service=svc or service(), checks=checks or {}))


def client(svc=None, **settings) -> TestClient:
    return TestClient(make_app(svc, **settings))


def ask(c, message="Godziny dziekanatu WNP?", history=(), **kw):
    return c.post("/api/chat", json={"message": message, "history": list(history)}, **kw)


def turns(n, content="x"):
    h = []
    for _ in range(n):
        h += [{"role": "user", "content": content}, {"role": "assistant", "content": content}]
    return h


# --- długość wiadomości i historii (422), wielkość ciała (413) ---------------------------

@pytest.mark.parametrize("settings, message, history, field", [
    ({"api_max_message_chars": 50}, "x" * 51, [], "message"),
    ({"api_max_history_messages": 4}, "pytanie", turns(3), "history"),
    ({"api_max_history_chars": 100}, "pytanie", turns(2, "y" * 30), "history"),
])
def test_za_dluga_wiadomosc_lub_historia_422(settings, message, history, field):
    with client(**settings) as c:
        r = ask(c, message, history)
    assert r.status_code == 422
    assert r.json()["detail"][0]["loc"] == ["body", field]


def test_w_granicach_limitow_200():
    with client(api_max_message_chars=50, api_max_history_messages=4,
                api_max_history_chars=100) as c:
        assert ask(c, "x" * 50, turns(2, "y" * 25)).status_code == 200


def test_za_duze_cialo_413_przed_walidacja():
    with client(api_max_body_bytes=1024) as c:
        r = ask(c, "x" * 1500)
    assert r.status_code == 413 and "1024" in r.json()["detail"]


def test_cialo_bez_content_length_411():
    def chunks():
        yield b'{"message": "hej"}'

    with client() as c:
        r = c.post("/api/chat", content=chunks(), headers={"Content-Type": "application/json"})
    assert r.status_code == 411


# --- rate limiting -------------------------------------------------------------------

def test_rate_limit_czatu_429_z_retry_after():
    with client(api_rate_limit_chat="2/minute") as c:
        assert [ask(c).status_code for _ in range(2)] == [200, 200]
        r = ask(c)
        faculties = c.get("/api/faculties")
    assert r.status_code == 429
    assert "Za dużo zapytań" in r.json()["detail"] and int(r.headers["Retry-After"]) > 0
    assert faculties.status_code == 200  # osobny limit dla pozostałych endpointów


def test_rate_limit_pozostalych_endpointow():
    with client(api_rate_limit_default="3/minute") as c:
        codes = [c.get("/api/faculties").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_rate_limit_osobno_dla_kazdego_adresu():
    app = make_app(api_rate_limit_chat="1/minute")
    with TestClient(app, client=("10.0.0.1", 1000)) as a, \
            TestClient(app, client=("10.0.0.2", 1000)) as b:
        assert ask(a).status_code == 200 and ask(a).status_code == 429
        assert ask(b).status_code == 200


# --- limit równoległych strumieni ----------------------------------------------------

def test_stream_slots_limit_sumaryczny_i_na_klienta():
    slots = StreamSlots(total=2, per_client=1)
    release_a = slots.acquire("a")
    with pytest.raises(Busy) as e:
        slots.acquire("a")
    assert e.value.status == 429
    release_b = slots.acquire("b")
    with pytest.raises(Busy) as e:
        slots.acquire("c")
    assert e.value.status == 503
    release_a()
    release_a()  # drugie zwolnienie nic nie robi
    assert slots.active == 1 and dict(slots.by_client) == {"b": 1}
    release_b()
    assert slots.active == 0 and not slots.by_client


class BlockingService:
    """Odpowiada dopiero po `go.set()` — trzyma miejsce na strumień."""

    def __init__(self):
        self.started = threading.Event()
        self.go = threading.Event()

    def stream(self, question, history=()):
        self.started.set()
        self.go.wait(5)
        yield {"type": "token", "text": "ok"}
        yield {"type": "done", "answer": "ok"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_rownolegle_strumienie_429_i_503_potem_zwolnione():
    svc = BlockingService()
    app = make_app(svc, api_max_streams=2, api_max_streams_per_client=1)
    app.state.resources = Resources(service=svc)  # ASGITransport nie uruchamia lifespan

    def http(ip):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(ip, 1)),
                                 base_url="http://api")

    async with http("10.0.0.1") as a, http("10.0.0.1") as a2, http("10.0.0.2") as b, \
            http("10.0.0.3") as c:
        body = {"message": "pytanie"}
        first = asyncio.create_task(a.post("/api/chat", json=body))
        await asyncio.to_thread(svc.started.wait, 5)
        assert (await a2.post("/api/chat", json=body)).status_code == 429  # ten sam klient
        second = asyncio.create_task(b.post("/api/chat", json=body))
        while app.state.slots.active < 2:
            await asyncio.sleep(0.01)
        busy = await c.post("/api/chat", json=body)  # serwer pełny
        assert busy.status_code == 503 and busy.headers["Retry-After"] == "5"
        svc.go.set()
        assert (await first).status_code == 200 and (await second).status_code == 200
        assert app.state.slots.active == 0
        assert (await c.post("/api/chat", json=body)).status_code == 200


# --- CORS i nagłówki bezpieczeństwa --------------------------------------------------

def test_cors_tylko_dla_frontendu_takze_przy_preflight():
    with client(api_cors_origins="https://czat.us.edu.pl") as c:
        pre = c.options("/api/chat", headers={"Origin": "https://czat.us.edu.pl",
                                              "Access-Control-Request-Method": "POST",
                                              "Access-Control-Request-Headers": "content-type"})
        evil = c.options("/api/chat", headers={"Origin": "https://zly.example",
                                               "Access-Control-Request-Method": "POST"})
    assert pre.status_code == 200
    assert pre.headers["access-control-allow-origin"] == "https://czat.us.edu.pl"
    assert "access-control-allow-origin" not in evil.headers


@pytest.mark.parametrize("value", ["*", "https://czat.us.edu.pl/", "czat.us.edu.pl",
                                   "https://a.pl, *"])
def test_cors_bez_gwiazdki_i_sciezek(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, api_cors_origins=value)


@pytest.mark.parametrize("request_", [
    lambda c: c.get("/api/faculties"),
    lambda c: ask(c),
    lambda c: ask(c, ""),  # 422
    lambda c: c.get("/nie-ma"),  # 404
])
def test_naglowki_bezpieczenstwa_w_kazdej_odpowiedzi(request_):
    with client(api_hsts_seconds=31536000) as c:
        r = request_(c)
    h = r.headers
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "no-referrer"
    assert h["content-security-policy"].startswith("default-src 'none'")
    assert h["cache-control"] == "no-store"
    assert h["strict-transport-security"].startswith("max-age=31536000")


def test_dokumentacja_openapi_domyslnie_wylaczona():
    with client() as c:
        assert [c.get(p).status_code for p in ("/docs", "/redoc", "/openapi.json")] == [404] * 3
    with client(api_docs=True) as c:
        docs = c.get("/docs")
        assert docs.status_code == 200 and "cdn.jsdelivr.net" in docs.headers[
            "content-security-policy"]
        assert c.get("/openapi.json").status_code == 200


def test_health_nie_ujawnia_szczegolow_bledu():
    def db_down():
        raise psycopg.OperationalError(
            'connection to server at "10.1.2.3", port 5433 failed: FATAL: password '
            'authentication failed for user "chatbot"')

    with TestClient(make_app(checks={"database": db_down})) as c:
        r = c.get("/api/health")
    assert r.status_code == 503
    assert r.json()["checks"]["database"]["error"] == "baza danych niedostępna"
    assert "10.1.2.3" not in r.text and "chatbot" not in r.text


# --- dziennik rozmów (RODO) ----------------------------------------------------------

def read_log(directory):
    files = list(directory.glob("chat-*.jsonl"))
    assert len(files) == 1
    assert files[0].name == f"chat-{datetime.now(UTC).date().isoformat()}.jsonl"
    return [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]


def test_dziennik_bez_ip_z_wynikiem_zrodlami_i_czasem(tmp_path):
    question = ("Jestem Jan, mój mail jan.kowalski@us.edu.pl, tel. +48 600 123 456, album "
                "312345, PESEL 02270803628 — kiedy czynny dziekanat WNP w 2026?")
    with TestClient(make_app(chat_log_enabled=True, chat_log_dir=str(tmp_path)),
                    client=("203.0.113.7", 5000)) as c:
        ask(c, question)
        ask(c, "Przepis na pierogi?")
    raw = next(tmp_path.glob("chat-*.jsonl")).read_text(encoding="utf-8")
    for secret in ("203.0.113.7", "jan.kowalski", "600 123 456", "312345", "02270803628",
                   "Pon"):  # ani IP, ani danych z pytania, ani treści odpowiedzi
        assert secret not in raw
    ok, refused = read_log(tmp_path)
    assert ok["outcome"] == "ok" and refused["outcome"] == "refused"
    assert ok["question"].count(REDACTED) == 4 and "dziekanat WNP w 2026" in ok["question"]
    assert ok["sources"] == 2 and ok["dists"] == [0.3, 0.35]
    assert ok["ms_total"] >= ok["ms_first_token"] >= ok["ms_sources"] >= 0
    assert ok["answer_chars"] == len("Pon–pt 9–13.") and ok["history_messages"] == 0
    assert len(ok["client"]) == 16 and ok["client"] == refused["client"]  # ten sam dzień
    assert refused["sources"] == 0 and refused["ms_first_token"] is None
    assert set(ok) >= {"ts", "retrieval_mode", "rerank_on", "question_chars"}


def test_dziennik_clarify_i_error(tmp_path):
    class Raising:
        def stream(self, question, history=()):
            raise psycopg.OperationalError("down")
            yield

    with TestClient(make_app(chat_log_enabled=True, chat_log_dir=str(tmp_path))) as c:
        ask(c, "Gdzie jest dziekanat?")
    with TestClient(make_app(Raising(), chat_log_enabled=True, chat_log_dir=str(tmp_path))) as c:
        ask(c, "Gdzie jest dziekanat WNP?")
    clarify, error = read_log(tmp_path)
    assert clarify["outcome"] == "clarify"
    assert error["outcome"] == "error" and error["error_code"] == "db_unavailable"


def test_dziennik_mozna_wylaczyc(tmp_path):
    with TestClient(make_app(chat_log_enabled=False, chat_log_dir=str(tmp_path))) as c:
        ask(c)
    assert not list(tmp_path.iterdir())


def test_sol_zmienia_sie_codziennie_i_nie_da_sie_powiazac_dni():
    salt = DailySalt()
    d1, d2 = date(2026, 11, 4), date(2026, 11, 5)
    a = salt.hash("203.0.113.7", d1)
    assert salt.hash("203.0.113.7", d1) == a and salt.hash("203.0.113.8", d1) != a
    assert salt.hash("203.0.113.7", d2) != a
    assert "203" not in a


@pytest.mark.parametrize("text, expected", [
    ("mail: jan.kowalski@us.edu.pl?", f"mail: {REDACTED}?"),
    ("tel 600-123-456 albo +48 32 359 20 00", f"tel {REDACTED} albo {REDACTED}"),
    ("PESEL 02270803628, album 312345", f"PESEL {REDACTED}, album {REDACTED}"),
    # rok, kod pocztowy, godziny i numer sali zostają — potrzebne do oceny pytań
    ("ul. Bankowa 12, 40-007 Katowice, sala 105, 2026 r., 10:00", None),
])
def test_redact(text, expected):
    assert redact(text) == (expected or text)


def test_retencja_usuwa_stare_pliki(tmp_path):
    for d in ("2026-09-01", "2026-10-04", "2026-10-05", "2026-11-04"):
        (tmp_path / f"chat-{d}.jsonl").write_text("{}\n")
    (tmp_path / "inny-plik.txt").write_text("zostaje")
    removed = ChatLog(tmp_path, retention_days=30).cleanup(today=date(2026, 11, 4))
    assert sorted(p.name for p in removed) == ["chat-2026-09-01.jsonl", "chat-2026-10-04.jsonl"]
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "chat-2026-10-05.jsonl", "chat-2026-11-04.jsonl", "inny-plik.txt"]


def test_retencja_przy_starcie_serwera(tmp_path):
    (tmp_path / "chat-2020-01-01.jsonl").write_text("{}\n")
    with TestClient(make_app(chat_log_enabled=True, chat_log_dir=str(tmp_path))) as c:
        c.get("/api/faculties")
    assert not (tmp_path / "chat-2020-01-01.jsonl").exists()


def test_logi_uvicorna_i_slowapi_bez_ip():
    f = _HideClientAddress()
    access = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                               ("203.0.113.7:51234", "POST", "/api/chat", "1.1", 200), None)
    limit = logging.LogRecord("slowapi", logging.WARNING, "", 0,
                              "ratelimit %s (%s) exceeded at endpoint: %s",
                              ("10 per 1 minute", "203.0.113.7", "/api/chat"), None)
    assert f.filter(access) and f.filter(limit)
    assert "203.0.113.7" not in access.getMessage() and "203.0.113.7" not in limit.getMessage()
    assert access.getMessage() == '- - "POST /api/chat HTTP/1.1" 200'
