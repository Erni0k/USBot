"""Aplikacja FastAPI: POST /api/chat (SSE), GET /api/health, GET /api/faculties.

Uruchomienie: `uvicorn usbot.api.main:app` (szczegóły: docs/uruchomienie.md). Serwer jest
bezstanowy — historię rozmowy przysyła klient w każdym żądaniu. Zabezpieczenia
publicznego API (limity, rate limiting, CORS, nagłówki, dziennik bez IP) opisuje
docs/architektura.md („Bezpieczeństwo”).
"""
import asyncio
import logging
import time
from collections.abc import Callable
from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIASGIMiddleware

from usbot.api.chatlog import ChatLog
from usbot.api.limits import BodySizeLimit, Busy, SecurityHeaders, StreamSlots
from usbot.api.metrics import Metrics
from usbot.api.resources import Resources, build_resources
from usbot.api.schemas import ChatRequest, Check, Faculty, Health, check_limits
from usbot.api.sse import stream_events
from usbot.config import Settings, get_settings
from usbot.core.faculties import faculty_options

log = logging.getLogger("usbot.api")

SSE_HEADERS = {"X-Accel-Buffering": "no"}  # nginx: nie buforuj strumienia
CLEANUP_INTERVAL_SECONDS = 3600


def client_ip(request: Request) -> str:
    """Adres klienta — za reverse proxy tylko z `uvicorn --proxy-headers
    --forwarded-allow-ips=<adres proxy>` (inaczej byłby to adres proxy)."""
    return request.client.host if request.client else "unknown"


class SlotStreamingResponse(StreamingResponse):
    """Strumień, który na pewno zwalnia miejsce w StreamSlots — także gdy klient
    rozłączy się, zanim zacznie się wysyłanie treści (generator nie wystartuje)."""

    def __init__(self, *args, release: Callable[[], None], **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._release = release

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._release()


def create_app(settings: Settings | None = None,
               resources_factory: Callable[[Settings], Resources] = build_resources) -> FastAPI:
    """Aplikacja z zasobami z `resources_factory` (testy podają atrapę ChatService)."""
    settings = settings or get_settings()
    chatlog = ChatLog(settings.chat_log_dir, retention_days=settings.chat_log_retention_days,
                      enabled=settings.chat_log_enabled,
                      max_question_chars=settings.chat_log_max_question_chars)
    slots = StreamSlots(settings.api_max_streams, settings.api_max_streams_per_client)
    metrics = Metrics()
    chatlog.listeners.append(metrics.observe)
    limiter = Limiter(key_func=client_ip, default_limits=[settings.api_rate_limit_default],
                      storage_uri=settings.api_rate_limit_storage, strategy="moving-window")

    async def cleanup_loop() -> None:
        while True:
            await anyio.to_thread.run_sync(chatlog.cleanup)
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Ładowanie modeli trwa kilka–kilkanaście sekund: w wątku, by nie blokować pętli
        app.state.resources = await anyio.to_thread.run_sync(resources_factory, settings)
        cleanup = asyncio.create_task(cleanup_loop())
        log.info("USBot API gotowe")
        try:
            yield
        finally:
            cleanup.cancel()
            await anyio.to_thread.run_sync(app.state.resources.close)

    docs = settings.api_docs
    app = FastAPI(title="USBot API", version="1.0",
                  summary="Chatbot RAG Uniwersytetu Śląskiego", lifespan=lifespan,
                  docs_url="/docs" if docs else None, redoc_url="/redoc" if docs else None,
                  openapi_url="/openapi.json" if docs else None)
    app.state.limiter = limiter
    app.state.chatlog = chatlog
    app.state.slots = slots
    app.state.metrics = metrics
    app.add_exception_handler(RateLimitExceeded, _rate_limited)

    # Kolejność: ostatnio dodany działa pierwszy. Nagłówki bezpieczeństwa i CORS obejmują
    # też odpowiedzi 413/429 (przeglądarka musi móc odczytać błąd), limit wielkości ciała
    # działa przed rate limitingiem.
    app.add_middleware(SlowAPIASGIMiddleware)
    app.add_middleware(BodySizeLimit, max_bytes=settings.api_max_body_bytes)
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                           allow_methods=["GET", "POST"], allow_headers=["Content-Type"],
                           expose_headers=["Retry-After"], max_age=600)
    app.add_middleware(SecurityHeaders, hsts_seconds=settings.api_hsts_seconds)

    @app.post("/api/chat", response_class=StreamingResponse,
              responses={200: {"content": {"text/event-stream": {}},
                               "description": "Strumień zdarzeń SSE (docs/api.md)"},
                         429: {"description": "Rate limit albo za dużo strumieni tego klienta"},
                         503: {"description": "Serwer obsługuje maksymalną liczbę strumieni"}})
    @limiter.limit(settings.api_rate_limit_chat)
    async def chat(request: Request, body: ChatRequest) -> Response:
        """Odpowiedź jako Server-Sent Events: sources / token / done, refused, clarify, error."""
        check_limits(body, settings)
        ip = client_ip(request)
        try:
            release = slots.acquire(ip)
        except Busy as exc:
            return JSONResponse({"detail": exc.message}, status_code=exc.status,
                                headers={"Retry-After": "5"})
        resources: Resources = request.app.state.resources
        history = [m.model_dump() for m in body.history]
        trace = chatlog.trace(body.message, len(history), ip,
                              retrieval_mode=settings.retrieval_mode, rerank_on=settings.rerank)
        return SlotStreamingResponse(
            stream_events(resources.service, body.message, history, trace=trace),
            release=release, media_type="text/event-stream", headers=SSE_HEADERS)

    @app.get("/api/health", response_model=Health,
             responses={503: {"model": Health, "description": "Któreś sprawdzenie nie przeszło"}})
    async def health(request: Request) -> JSONResponse:
        """Stan bazy, modelu embeddingów (i rerankera) oraz LLM-a; każde z limitem czasu."""
        resources: Resources = request.app.state.resources
        names = list(resources.checks)
        results = await asyncio.gather(
            *(_run_check(n, resources.checks[n], settings.health_timeout) for n in names))
        checks = dict(zip(names, results, strict=True))
        ok = all(c.ok for c in checks.values())
        body = Health(status="ok" if ok else "error", checks=checks)
        return JSONResponse(body.model_dump(), status_code=200 if ok else 503)

    @app.get("/api/faculties", response_model=list[Faculty])
    async def faculties() -> JSONResponse:
        """Wydziały — te same co w `clarify.options` (np. przyciski w UI)."""
        return JSONResponse(faculty_options())

    @app.get("/api/metrics", response_class=PlainTextResponse, include_in_schema=False)
    async def metrics_endpoint() -> PlainTextResponse:
        """Liczniki w formacie Prometheusa: odpowiedzi według wyniku, odsetek refused, czas
        do pierwszego tokenu i całej odpowiedzi. Niepubliczny — Caddy go nie przepuszcza."""
        return PlainTextResponse(metrics.render(active_streams=slots.active),
                                 media_type="text/plain; version=0.0.4; charset=utf-8")

    return app


def _rate_limited(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """429 z Retry-After: za ile sekund zwolni się miejsce w przekroczonym limicie."""
    retry_after = 60
    try:
        limit, args = request.state.view_rate_limit
        reset, _remaining = request.app.state.limiter.limiter.get_window_stats(limit, *args)
        retry_after = max(1, int(reset - time.time()) + 1)
    except (AttributeError, TypeError, ValueError):
        pass
    return JSONResponse(
        {"detail": f"Za dużo zapytań (limit: {exc.detail}) — spróbuj ponownie później."},
        status_code=429, headers={"Retry-After": str(retry_after)})


def _public_error(exc: BaseException) -> str:
    """Opis błędu sprawdzenia dla publicznego /api/health — bez adresów, użytkowników i
    nazw hostów z komunikatów sterowników (szczegóły trafiają do logu serwera)."""
    import openai
    import psycopg
    if isinstance(exc, psycopg.Error):
        return "baza danych niedostępna"
    if isinstance(exc, openai.APIError):
        return "serwer modelu niedostępny"
    if isinstance(exc, RuntimeError):  # nasze komunikaty z ping() — bez danych wrażliwych
        return str(exc).split(" (")[0][:200]
    return "błąd sprawdzenia"


async def _run_check(name: str, check: Callable[[], dict], timeout: float) -> Check:
    start = time.perf_counter()

    def elapsed() -> float:
        return round((time.perf_counter() - start) * 1000, 1)

    try:
        with anyio.fail_after(timeout):
            # abandon_on_cancel: po przekroczeniu czasu nie czekamy na zawieszony wątek
            detail = await anyio.to_thread.run_sync(check, abandon_on_cancel=True)
    except TimeoutError:
        log.warning("health %s: brak odpowiedzi w %g s", name, timeout)
        return Check(ok=False, ms=elapsed(), error=f"brak odpowiedzi w {timeout:g} s")
    except Exception as exc:  # noqa: BLE001 — błąd sprawdzenia to wynik, nie awaria
        log.warning("health %s: %s: %s", name, type(exc).__name__, exc)
        return Check(ok=False, ms=elapsed(), error=_public_error(exc))
    return Check(ok=True, ms=elapsed(), detail=detail or {})


class _HideClientAddress(logging.Filter):
    """Usuwa adres IP klienta z logów uvicorna i slowapi (RODO: dziennik bez IP)."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if record.name == "uvicorn.access" and isinstance(args, tuple) and len(args) == 5:
            record.args = ("-", *args[1:])
        elif (record.name.startswith("slowapi") and isinstance(args, tuple) and len(args) == 3
              and "exceeded" in str(record.msg)):
            record.args = (args[0], "-", args[2])
        return True


def _configure_logging() -> None:
    import sys
    if hasattr(sys.stderr, "reconfigure"):  # Windows: domyślne cp1252 psuje polskie znaki
        sys.stderr.reconfigure(encoding="utf-8")
    for name in ("uvicorn.access", "slowapi"):
        logging.getLogger(name).addFilter(_HideClientAddress())
    logger = logging.getLogger("usbot")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    # LOG_FORMAT=json (kontenery): uvicorn skonfigurował już swoje loggery, zanim
    # zaimportował aplikację — podmieniamy im formatter
    if get_settings().log_format == "json":
        from usbot.logjson import use_json
        use_json(("usbot", "uvicorn", "uvicorn.error", "uvicorn.access", "slowapi"))


_configure_logging()
app = create_app()
