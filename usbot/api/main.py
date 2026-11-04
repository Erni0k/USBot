"""Aplikacja FastAPI: POST /api/chat (SSE), GET /api/health, GET /api/faculties.

Uruchomienie: `uvicorn usbot.api.main:app` (szczegóły: docs/uruchomienie.md). Serwer jest
bezstanowy — historię rozmowy przysyła klient w każdym żądaniu.
"""
import asyncio
import logging
import time
from collections.abc import Callable
from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from usbot.api.resources import Resources, build_resources
from usbot.api.schemas import ChatRequest, Check, Faculty, Health
from usbot.api.sse import stream_events
from usbot.config import Settings, get_settings
from usbot.core.faculties import faculty_options

log = logging.getLogger("usbot.api")

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",  # nginx: nie buforuj strumienia
}


def create_app(settings: Settings | None = None,
               resources_factory: Callable[[Settings], Resources] = build_resources) -> FastAPI:
    """Aplikacja z zasobami z `resources_factory` (testy podają atrapę ChatService)."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Ładowanie modeli trwa kilka–kilkanaście sekund: w wątku, by nie blokować pętli
        app.state.resources = await anyio.to_thread.run_sync(resources_factory, settings)
        log.info("USBot API gotowe")
        try:
            yield
        finally:
            await anyio.to_thread.run_sync(app.state.resources.close)

    app = FastAPI(title="USBot API", version="1.0",
                  summary="Chatbot RAG Uniwersytetu Śląskiego", lifespan=lifespan)
    origins = [o.strip() for o in settings.api_cors_origins.split(",") if o.strip()]
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins,
                           allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

    @app.post("/api/chat", response_class=StreamingResponse,
              responses={200: {"content": {"text/event-stream": {}},
                               "description": "Strumień zdarzeń SSE (docs/api.md)"}})
    async def chat(body: ChatRequest, request: Request) -> StreamingResponse:
        """Odpowiedź jako Server-Sent Events: sources / token / done, refused, clarify, error."""
        resources: Resources = request.app.state.resources
        history = [m.model_dump() for m in body.history]
        return StreamingResponse(stream_events(resources.service, body.message, history),
                                 media_type="text/event-stream", headers=SSE_HEADERS)

    @app.get("/api/health", response_model=Health,
             responses={503: {"model": Health, "description": "Któreś sprawdzenie nie przeszło"}})
    async def health(request: Request) -> JSONResponse:
        """Stan bazy, modelu embeddingów (i rerankera) oraz LLM-a; każde z limitem czasu."""
        resources: Resources = request.app.state.resources
        names = list(resources.checks)
        results = await asyncio.gather(
            *(_run_check(resources.checks[n], settings.health_timeout) for n in names))
        checks = dict(zip(names, results, strict=True))
        ok = all(c.ok for c in checks.values())
        body = Health(status="ok" if ok else "error", checks=checks)
        return JSONResponse(body.model_dump(), status_code=200 if ok else 503)

    @app.get("/api/faculties", response_model=list[Faculty])
    async def faculties() -> list[dict[str, str]]:
        """Wydziały — te same co w `clarify.options` (np. przyciski w UI)."""
        return faculty_options()

    return app


async def _run_check(check: Callable[[], dict], timeout: float) -> Check:
    start = time.perf_counter()

    def elapsed() -> float:
        return round((time.perf_counter() - start) * 1000, 1)

    try:
        with anyio.fail_after(timeout):
            # abandon_on_cancel: po przekroczeniu czasu nie czekamy na zawieszony wątek
            detail = await anyio.to_thread.run_sync(check, abandon_on_cancel=True)
    except TimeoutError:
        return Check(ok=False, ms=elapsed(), error=f"brak odpowiedzi w {timeout:g} s")
    except Exception as exc:  # noqa: BLE001 — błąd sprawdzenia to wynik, nie awaria
        return Check(ok=False, ms=elapsed(), error=f"{type(exc).__name__}: {exc}"[:300])
    return Check(ok=True, ms=elapsed(), detail=detail or {})


def _configure_logging() -> None:
    import sys
    if hasattr(sys.stderr, "reconfigure"):  # Windows: domyślne cp1252 psuje polskie znaki
        sys.stderr.reconfigure(encoding="utf-8")
    logger = logging.getLogger("usbot")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)


_configure_logging()
app = create_app()
