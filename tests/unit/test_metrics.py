"""GET /api/metrics (format Prometheusa) i logi JSON (LOG_FORMAT=json)."""
import json
import logging
import re

import psycopg
from fastapi.testclient import TestClient

from tests.unit.test_api_security import ask, make_app
from usbot.api.metrics import Metrics
from usbot.logjson import JsonFormatter


def sample(text: str, name: str) -> float:
    m = re.search(rf"^{re.escape(name)} (\S+)$", text, re.M)
    assert m, f"brak {name} w:\n{text}"
    return float(m.group(1))


def test_metryki_licza_wyniki_odsetek_refused_i_czas_pierwszego_tokenu():
    with TestClient(make_app()) as c:
        ask(c)                                   # ok
        ask(c, "Przepis na pierogi?")           # refused
        ask(c, "Gdzie jest dziekanat?")         # clarify
        ask(c, "Godziny dziekanatu WNP?")       # ok
        r = c.get("/api/metrics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    t = r.text
    assert sample(t, 'usbot_chat_requests_total{outcome="ok"}') == 2
    assert sample(t, 'usbot_chat_requests_total{outcome="refused"}') == 1
    assert sample(t, 'usbot_chat_requests_total{outcome="clarify"}') == 1
    assert sample(t, "usbot_chat_refused_ratio") == 0.25
    # Czas do 1. tokenu tylko dla odpowiedzi z LLM-a; całość dla wszystkich czterech
    assert sample(t, "usbot_chat_first_token_seconds_count") == 2
    assert sample(t, 'usbot_chat_first_token_seconds_bucket{le="+Inf"}') == 2
    assert sample(t, "usbot_chat_duration_seconds_count") == 4
    assert 'usbot_chat_first_token_seconds_recent{quantile="0.5"}' in t
    assert sample(t, "usbot_chat_active_streams") == 0


def test_metryki_bledow_wedlug_kodu():
    class Raising:
        def stream(self, question, history=()):
            raise psycopg.OperationalError("down")
            yield

    with TestClient(make_app(Raising())) as c:
        ask(c)
        t = c.get("/api/metrics").text
    assert sample(t, 'usbot_chat_requests_total{outcome="error"}') == 1
    assert sample(t, 'usbot_chat_errors_total{code="db_unavailable"}') == 1
    assert sample(t, "usbot_chat_refused_ratio") == 0


def test_histogram_skumulowany_i_kwantyle():
    m = Metrics(clock=lambda: 100.0)
    for ms in (300, 700, 900, 5000):
        m.observe({"outcome": "ok", "ms_first_token": ms, "ms_total": ms + 1000})
    m.observe({"outcome": "cancelled", "ms_first_token": None, "ms_total": 50})
    # przerwana w trakcie pisania: dziennik ma outcome "ok" i cancelled=True
    m.observe({"outcome": "ok", "cancelled": True, "ms_first_token": None, "ms_total": 900})
    t = m.render()
    assert sample(t, 'usbot_chat_first_token_seconds_bucket{le="0.5"}') == 1
    assert sample(t, 'usbot_chat_first_token_seconds_bucket{le="1"}') == 3
    assert sample(t, 'usbot_chat_first_token_seconds_bucket{le="8"}') == 4
    assert sample(t, "usbot_chat_first_token_seconds_sum") == 6.9
    assert sample(t, "usbot_chat_duration_seconds_count") == 4   # przerwana się nie liczy
    assert sample(t, 'usbot_chat_requests_total{outcome="cancelled"}') == 2
    assert sample(t, 'usbot_chat_requests_total{outcome="ok"}') == 4
    assert sample(t, 'usbot_chat_first_token_seconds_recent{quantile="0.5"}') == 0.8


def test_log_json_bez_ip_klienta():
    f = JsonFormatter()
    access = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                               ("-", "POST", "/api/chat", "1.1", 200), None)
    out = json.loads(f.format(access))
    assert out["level"] == "INFO" and out["logger"] == "uvicorn.access"
    assert (out["method"], out["path"], out["status"]) == ("POST", "/api/chat", 200)
    assert out["ts"].endswith("+00:00")

    try:
        raise ValueError("zły stan")
    except ValueError:
        import sys
        err = logging.LogRecord("usbot.api", logging.ERROR, "", 0, "błąd: %s", ("x",), sys.exc_info())
    out = json.loads(f.format(err))
    assert out["msg"] == "błąd: x" and "ValueError: zły stan" in out["exc"]
