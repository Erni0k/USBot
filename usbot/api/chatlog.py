"""Dziennik rozmów API zgodny z RODO: co wiemy o odpowiedzi, bez danych identyfikujących.

Po co: ocena jakości (które pytania dostają odmowę, jak daleko są źródła, ile trwa
odpowiedź) i wykrywanie nadużyć (seria pytań z jednego adresu w ciągu dnia). Opis pól,
podstawy i retencji: docs/architektura.md („Bezpieczeństwo” → „Dziennik rozmów”).

- Bez adresu IP: zamiast niego skrót HMAC-SHA256 z solą losowaną co dobę i trzymaną
  tylko w pamięci procesu — w obrębie dnia da się powiązać pytania jednego klienta,
  ale nie da się odtworzyć adresu ani powiązać dni (sól nie jest nigdzie zapisywana).
- Pytanie po usunięciu adresów e-mail, numerów telefonu, PESEL-u i innych ciągów cyfr
  (np. numeru albumu) i skrócone do CHAT_LOG_MAX_QUESTION_CHARS znaków. Nazwisk i
  innych danych w zwykłym tekście automatycznie usunąć się nie da — dlatego retencja.
- Bez treści odpowiedzi i historii rozmowy (tylko ich długość).
- Jeden plik JSON Lines na dobę (UTC); pliki starsze niż CHAT_LOG_RETENTION_DAYS są
  usuwane przy starcie, co godzinę przez serwer i poleceniem
  `python -m usbot.api.chatlog cleanup` (np. z crona, gdy serwer nie działa).
"""
import hashlib
import hmac
import json
import logging
import re
import secrets
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

log = logging.getLogger("usbot.api")

_FILE = re.compile(r"^chat-(\d{4}-\d{2}-\d{2})\.jsonl$")

# Dane, które student może wpisać w pytanie, a których nie potrzebujemy do oceny jakości.
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Ciągi co najmniej 6 cyfr, także z odstępami/myślnikami: telefon, PESEL, numer albumu,
# numer konta. Kod pocztowy (40-007) i rok (2026) zostają.
_DIGITS = re.compile(r"(?<![\w-])\+?\d(?:[ \-]?\d){5,}(?![\w-])")
REDACTED = "[usunięto]"


def redact(text: str) -> str:
    """Pytanie bez adresów e-mail i długich ciągów cyfr."""
    return _DIGITS.sub(REDACTED, _EMAIL.sub(REDACTED, text))


class DailySalt:
    """Sól HMAC losowana przy zmianie doby (UTC); tylko w pamięci."""

    def __init__(self) -> None:
        self._day: date | None = None
        self._salt = b""
        self._lock = threading.Lock()

    def hash(self, value: str, today: date | None = None) -> str:
        today = today or datetime.now(UTC).date()
        with self._lock:
            if today != self._day:
                self._day, self._salt = today, secrets.token_bytes(32)
            salt = self._salt
        return hmac.new(salt, value.encode(), hashlib.sha256).hexdigest()[:16]


class ChatLog:
    """Zapis rekordów do `dir/chat-RRRR-MM-DD.jsonl` i usuwanie starych plików."""

    def __init__(self, directory: str | Path, *, retention_days: int = 30,
                 enabled: bool = True, max_question_chars: int = 500) -> None:
        self.dir = Path(directory)
        self.retention_days = retention_days
        self.enabled = enabled
        self.max_question_chars = max_question_chars
        self.salt = DailySalt()
        self._lock = threading.Lock()
        # Odbiorcy każdego rekordu (np. metryki) — także przy wyłączonym dzienniku
        self.listeners: list[Callable[[dict], None]] = []

    def publish(self, record: dict) -> None:
        """Rekord jednej odpowiedzi: do odbiorców i (jeśli włączony) do pliku."""
        for listener in self.listeners:
            try:
                listener(record)
            except Exception:  # noqa: BLE001 — metryki nie mogą zatrzymać odpowiedzi
                log.exception("odbiorca rekordu dziennika rzucił wyjątek")
        self.write(record)

    def client_id(self, ip: str | None) -> str | None:
        return self.salt.hash(ip) if ip else None

    def write(self, record: dict) -> None:
        if not self.enabled:
            return
        now = datetime.now(UTC)
        line = json.dumps({"ts": now.isoformat(timespec="seconds"), **record},
                          ensure_ascii=False, separators=(",", ":"))
        try:
            with self._lock:
                self.dir.mkdir(parents=True, exist_ok=True)
                with open(self.dir / f"chat-{now.date().isoformat()}.jsonl", "a",
                          encoding="utf-8") as f:
                    f.write(line + "\n")
        except OSError as exc:  # dziennik nie może zatrzymać odpowiedzi
            log.warning("nie udało się zapisać dziennika rozmów: %s", exc)

    def cleanup(self, today: date | None = None) -> list[Path]:
        """Usuwa pliki starsze niż `retention_days` dni; zwraca usunięte."""
        if not self.dir.is_dir():
            return []
        today = today or datetime.now(UTC).date()
        oldest = today - timedelta(days=self.retention_days)
        removed = []
        for path in self.dir.iterdir():
            m = _FILE.match(path.name)
            if m and date.fromisoformat(m.group(1)) < oldest:
                path.unlink(missing_ok=True)
                removed.append(path)
        if removed:
            log.info("dziennik rozmów: usunięto %d plików starszych niż %d dni",
                     len(removed), self.retention_days)
        return removed

    def trace(self, question: str, history_len: int, client_ip: str | None, **extra) -> "ChatTrace":
        q = redact(question)
        return ChatTrace(self, {
            "client": self.client_id(client_ip),
            "question": q[:self.max_question_chars],
            "question_chars": len(question),
            "history_messages": history_len,
            **extra,
        })


@dataclass
class ChatTrace:
    """Zbiera o jednej odpowiedzi to, co trafi do dziennika, ze zdarzeń ChatService."""

    chatlog: ChatLog
    record: dict
    start: float = field(default_factory=time.perf_counter)
    outcome: str | None = None
    answer_chars: int = 0
    _first_token: float | None = None
    _sources: float | None = None

    def _ms(self, t: float | None = None) -> int:
        return round(((t or time.perf_counter()) - self.start) * 1000)

    def event(self, event: dict) -> None:
        kind = event["type"]
        if kind == "sources":
            self._sources = time.perf_counter()
            self.outcome = "ok"
            self.record["sources"] = len(event["sources"])
            self.record["dists"] = [round(s["dist"], 3) for s in event["sources"]]
            if any(s.get("rerank") is not None for s in event["sources"]):
                self.record["rerank"] = [None if s["rerank"] is None else round(s["rerank"], 3)
                                         for s in event["sources"]]
        elif kind == "token" and self._first_token is None:
            self._first_token = time.perf_counter()
        elif kind in ("refused", "clarify"):
            self._sources = time.perf_counter()
            self.outcome = kind
            self.record["sources"] = 0
        elif kind == "error":
            self.outcome = "error"
            self.record["error_code"] = event["code"]
        elif kind == "done":
            self.answer_chars = len(event["answer"])

    def close(self, *, cancelled: bool = False) -> None:
        if cancelled and self.outcome not in ("error",):
            self.record["cancelled"] = True
        self.chatlog.publish({
            **self.record,
            "outcome": self.outcome or ("cancelled" if cancelled else "error"),
            "ms_sources": self._ms(self._sources) if self._sources else None,
            "ms_first_token": self._ms(self._first_token) if self._first_token else None,
            "ms_total": self._ms(),
            "answer_chars": self.answer_chars,
        })


def main(argv: list[str]) -> int:
    """`python -m usbot.api.chatlog cleanup` — usuwa stare pliki dziennika."""
    if argv[1:] != ["cleanup"]:
        print("użycie: python -m usbot.api.chatlog cleanup")
        return 2
    from usbot.config import get_settings
    s = get_settings()
    removed = ChatLog(s.chat_log_dir, retention_days=s.chat_log_retention_days).cleanup()
    print(f"usunięto {len(removed)} plików dziennika starszych niż {s.chat_log_retention_days} dni")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
