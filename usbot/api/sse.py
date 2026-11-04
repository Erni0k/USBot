"""Zdarzenia ChatService → Server-Sent Events, z przerwaniem przy rozłączeniu klienta.

ChatService.stream() jest zwykłym (synchronicznym) generatorem — wyszukiwanie, model
embeddingów i klient LLM-a blokują. Generator działa więc w osobnym wątku, a zdarzenia
trafiają do pętli asyncio przez kolejkę. Gdy klient się rozłączy, serwer przestaje
czytać odpowiedź: ustawiamy flagę `stop`, wątek kończy pętlę przy najbliższym zdarzeniu
(kolejnym tokenie) i zamyka generator — to zamyka strumień HTTP do LLM-a, więc Ollama/vLLM
przestaje generować. Czekania na pierwszy token nie da się przerwać z zewnątrz; ogranicza
je LLM_TIMEOUT.
"""
import asyncio
import json
import logging
import threading
from collections.abc import AsyncIterator, Iterable, Iterator, Sequence
from typing import Literal, Protocol, TypedDict

from usbot.core.chat import Event
from usbot.core.models import Message

log = logging.getLogger("usbot.api")

# Komentarz SSE wysyłany, gdy przez tyle sekund nie ma zdarzenia (np. przy długim
# wyszukiwaniu albo czekaniu na pierwszy token). Utrzymuje połączenie przez proxy i
# pozwala wcześniej zauważyć rozłączenie klienta.
HEARTBEAT_SECONDS = 10.0


class ErrorEvent(TypedDict):
    type: Literal["error"]
    code: str
    message: str


class ChatStreamer(Protocol):
    """To, czego API potrzebuje od ChatService (w testach — atrapa)."""

    def stream(self, question: str, history: Sequence[Message] = ()) -> Iterator[Event]: ...


def format_sse(event: Event | ErrorEvent) -> str:
    """Jedno zdarzenie SSE: `event: <type>` i `data: <JSON całego zdarzenia>`."""
    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event['type']}\ndata: {data}\n\n"


def error_event(exc: BaseException) -> ErrorEvent:
    """Wyjątek adaptera → zdarzenie `error` z kodem dla klienta (szczegóły tylko w logu)."""
    code, message = _classify(exc)
    if code == "internal":
        log.error("błąd podczas odpowiedzi", exc_info=exc)
    else:
        log.warning("błąd podczas odpowiedzi: %s: %s", type(exc).__name__, exc)
    return {"type": "error", "code": code, "message": message}


def _classify(exc: BaseException) -> tuple[str, str]:
    import httpx
    import openai
    import psycopg

    if isinstance(exc, psycopg.errors.QueryCanceled):  # statement_timeout
        return "db_timeout", "Baza danych nie odpowiedziała w wymaganym czasie."
    # także psycopg_pool.PoolTimeout (brak wolnego połączenia), podklasa OperationalError
    if isinstance(exc, psycopg.OperationalError):
        return "db_unavailable", "Baza danych jest niedostępna."
    if isinstance(exc, openai.APITimeoutError | httpx.TimeoutException):
        return "llm_timeout", "Model językowy nie odpowiedział w wymaganym czasie."
    if isinstance(exc, openai.APIConnectionError | httpx.TransportError):
        return "llm_unavailable", "Model językowy jest niedostępny."
    if isinstance(exc, openai.APIStatusError):
        return "llm_error", f"Model językowy zwrócił błąd (HTTP {exc.status_code})."
    return "internal", "Wewnętrzny błąd serwera."


_END = object()


async def stream_events(service: ChatStreamer, question: str, history: Iterable[Message], *,
                        heartbeat: float = HEARTBEAT_SECONDS,
                        trace=None) -> AsyncIterator[str]:
    """Strumień SSE dla jednej odpowiedzi. Kończy się zdarzeniem `done` albo `error`.

    Przerwanie iteracji (rozłączenie klienta → anulowanie zadania, `aclose()`) zatrzymuje
    wątek z ChatService przy najbliższym zdarzeniu. `trace` (chatlog.ChatTrace) dostaje
    każde wysłane zdarzenie i na końcu zapisuje rekord dziennika rozmów.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    stop = threading.Event()
    history = list(history)

    def put(item: object) -> None:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, item)
        except RuntimeError:  # pętla już zamknięta (zatrzymanie serwera)
            pass

    def worker() -> None:
        events = None
        produced = 0
        try:
            events = service.stream(question, history)
            for event in events:
                if stop.is_set():
                    break
                put(event)
                produced += 1
        except Exception as exc:  # noqa: BLE001 — każdy błąd kończy strumień zdarzeniem
            if not stop.is_set():
                put(error_event(exc))
        finally:
            if events is not None:
                events.close()  # zamyka strumień LLM-a, jeśli jeszcze trwa
            if stop.is_set():
                log.info("klient rozłączył się — przerwano odpowiedź po %d zdarzeniach",
                         produced)
            put(_END)

    loop.run_in_executor(None, worker)
    finished = False
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), heartbeat)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            if item is _END:
                finished = True
                return
            if trace is not None:
                trace.event(item)
            yield format_sse(item)
    finally:
        stop.set()
        if trace is not None:
            trace.close(cancelled=not finished)
