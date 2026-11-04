"""Ochrona publicznego API: limit równoległych strumieni, wielkość żądania, nagłówki.

Rate limiting (liczba żądań na adres IP w czasie) robi slowapi w main.py; tu jest to,
czego slowapi nie obejmuje. Model zagrożeń: docs/architektura.md („Bezpieczeństwo”).
"""
import json
from collections import Counter

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class Busy(Exception):
    """Brak wolnego miejsca na strumień: `status` 503 (serwer) albo 429 (ten klient)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class StreamSlots:
    """Ile odpowiedzi może się generować naraz — w sumie i dla jednego klienta (IP).

    Każdy strumień zajmuje wątek i miejsce w kolejce serwera modelu (Ollama obsługuje
    kilka naraz, reszta czeka), więc bez limitu jeden klient mógłby zająć wszystkie.
    Licznik żyje w pętli asyncio jednego procesu — bez blokad. Przy kilku procesach
    (`--workers`) limit dotyczy każdego z osobna.
    """

    def __init__(self, total: int, per_client: int) -> None:
        self.total = total
        self.per_client = per_client
        self.active = 0
        self.by_client: Counter[str] = Counter()

    def acquire(self, client: str):
        """Zajmuje miejsce albo rzuca Busy. Zwraca funkcję zwalniającą (idempotentną)."""
        if self.active >= self.total:
            raise Busy(503, "Serwer obsługuje teraz maksymalną liczbę rozmów — spróbuj za chwilę.")
        if self.by_client[client] >= self.per_client:
            raise Busy(429, "Za dużo równoległych odpowiedzi — poczekaj na zakończenie poprzednich.")
        self.active += 1
        self.by_client[client] += 1
        released = False

        def release() -> None:
            nonlocal released
            if released:
                return
            released = True
            self.active -= 1
            self.by_client[client] -= 1
            if self.by_client[client] <= 0:
                del self.by_client[client]

        return release


async def _send_json(send: Send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}, ensure_ascii=False).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class BodySizeLimit:
    """Odrzuca żądanie z treścią większą niż `max_bytes` (413) zanim aplikacja ją wczyta.

    FastAPI czyta całe ciało żądania do pamięci przed walidacją, a uvicorn nie ma
    limitu. Żądania z treścią bez nagłówka Content-Length (chunked) dostają 411 —
    `fetch` w przeglądarce zawsze go wysyła.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH"):
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        length = headers.get(b"content-length")
        if length is None:
            return await _send_json(send, 411, "Wymagany nagłówek Content-Length.")
        try:
            size = int(length)
        except ValueError:
            return await _send_json(send, 400, "Niepoprawny nagłówek Content-Length.")
        if size > self.max_bytes:
            return await _send_json(send, 413, f"Żądanie większe niż {self.max_bytes} bajtów.")

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > size:  # więcej niż zapowiedział Content-Length
                    raise RuntimeError("treść żądania dłuższa niż Content-Length")
            return message

        await self.app(scope, limited_receive, send)


class SecurityHeaders:
    """Nagłówki bezpieczeństwa w każdej odpowiedzi (także w strumieniu SSE i błędach).

    API zwraca tylko JSON i SSE, więc CSP blokuje wszystko (`default-src 'none'`) i
    zabrania osadzania w ramkach. Wyjątek: strony dokumentacji OpenAPI (`/docs`,
    `/redoc`, przy API_DOCS=true) ładują skrypty z CDN — dostają łagodniejszą politykę.
    Odpowiedzi zawierają pytania użytkowników, więc nie mogą trafić do cache (no-store).
    """

    CSP = b"default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    DOCS_CSP = (b"default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                b"style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                b"img-src 'self' data: https://fastapi.tiangolo.com; frame-ancestors 'none'")

    def __init__(self, app: ASGIApp, hsts_seconds: int = 0) -> None:
        self.app = app
        self.hsts = (f"max-age={hsts_seconds}; includeSubDomains".encode()
                     if hsts_seconds > 0 else None)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        docs = scope["path"] in ("/docs", "/redoc", "/docs/oauth2-redirect")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", [])
                           if k.lower() != b"cache-control"] if not docs else list(
                    message.get("headers", []))
                headers += [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"cross-origin-opener-policy", b"same-origin"),
                    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                    (b"content-security-policy", self.DOCS_CSP if docs else self.CSP),
                ]
                if not docs:
                    headers.append((b"cache-control", b"no-store"))
                if self.hsts:
                    headers.append((b"strict-transport-security", self.hsts))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
