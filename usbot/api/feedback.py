"""Oceny odpowiedzi (POST /api/feedback): tabela `feedback` bez danych identyfikujących.

Po co: oceny -1 to materiał na nowe pytania testowe (evaluation/feedback_export.py), a
odsetek ocen -1 w czasie pokazuje, czy zmiany pomagają (evaluation/feedback_report.py).

Te same zasady co w dzienniku rozmów (usbot/api/chatlog.py, docs/architektura.md 2.4):

- Bez adresu IP i żadnego identyfikatora klienta — nawet skrótu dziennego. Limit żądań
  liczy slowapi w pamięci, do tabeli nic z tego nie trafia.
- Pytanie, odpowiedź i komentarz po `redact()`, pytanie skrócone jak w dzienniku.
  Nazwisk i innych danych w zwykłym tekście automatycznie usunąć się nie da — dlatego
  po FEEDBACK_RETENTION_DAYS znikają same treści; ocena, źródła i konfiguracja zostają,
  żeby dało się liczyć trend.
- Konfigurację bierzemy z ustawień serwera, nigdy z żądania.

Serwer jest bezstanowy, więc treść oceny przysyła klient i może ją podrobić: skrypty
traktują ją jako dane do przejrzenia przez człowieka, nie jako wynik pomiaru.

init.sql działa tylko przy pierwszym starcie bazy, a ingestia uruchamia się według
harmonogramu, dlatego w istniejącej bazie tabelę tworzy `ensure_schema()` przy pierwszym
użyciu — bez osobnego kroku wdrożenia.
"""
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import psycopg
from psycopg.types.json import Jsonb

from usbot.api.chatlog import redact
from usbot.api.schemas import FeedbackRequest
from usbot.config import Settings

log = logging.getLogger("usbot.api")

# Ta sama definicja co w infra/init.sql
SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS feedback (
        id             BIGSERIAL PRIMARY KEY,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
        question       TEXT,
        answer         TEXT,
        sources        JSONB NOT NULL DEFAULT '[]'::jsonb,
        rating         SMALLINT NOT NULL CHECK (rating IN (-1, 1)),
        comment        TEXT,
        followup       BOOLEAN NOT NULL DEFAULT false,
        retrieval_mode TEXT NOT NULL,
        rerank         BOOLEAN NOT NULL,
        threshold      DOUBLE PRECISION NOT NULL,
        llm_model      TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS feedback_created_at_idx ON feedback (created_at)",
)
# Dwa równoległe CREATE TABLE IF NOT EXISTS potrafią się wywrócić na pg_type
_SCHEMA_LOCK = 720_260_001


def ensure_schema(conn: psycopg.Connection) -> None:
    """Tworzy tabelę, jeśli jej nie ma. Zatwierdza transakcję."""
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_LOCK,))
    for statement in SCHEMA:
        conn.execute(statement)
    conn.commit()


@dataclass(frozen=True)
class FeedbackRecord:
    question: str
    answer: str
    sources: list[dict]
    rating: int
    comment: str | None
    followup: bool
    retrieval_mode: str
    rerank: bool
    threshold: float
    llm_model: str


def build_record(body: FeedbackRequest, settings: Settings) -> FeedbackRecord:
    """Wiersz do zapisu: treści bez danych osobowych, konfiguracja z ustawień serwera."""
    comment = redact(body.comment) if body.comment else None
    return FeedbackRecord(
        # Najpierw redact(): przycięcie nie może zostawić połowy numeru poza wzorcem
        question=redact(body.question)[:settings.chat_log_max_question_chars],
        answer=redact(body.answer),
        sources=[{"url": s.url, "dist": round(s.dist, 4),
                  **({"rerank": round(s.rerank, 4)} if s.rerank is not None else {})}
                 for s in body.sources],
        rating=body.rating,
        comment=comment,
        followup=body.followup,
        retrieval_mode=settings.retrieval_mode,
        rerank=settings.rerank,
        # Ten próg faktycznie decydował o odpowiedzi — patrz cutoff() w usbot/core/chat.py
        threshold=settings.min_rerank_score if settings.rerank else settings.max_distance,
        llm_model=settings.llm_model,
    )


class PgFeedbackStore:
    """Zapis ocen przez pulę połączeń API."""

    def __init__(self, pool) -> None:
        self.pool = pool
        self._ready = False

    @contextmanager
    def _connection(self) -> Iterator[psycopg.Connection]:
        with self.pool.connection() as conn:
            if not self._ready:
                ensure_schema(conn)
                self._ready = True
            yield conn

    def add(self, record: FeedbackRecord) -> int:
        with self._connection() as conn:
            row = conn.execute(
                "INSERT INTO feedback (question, answer, sources, rating, comment, followup, "
                "retrieval_mode, rerank, threshold, llm_model) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (record.question, record.answer, Jsonb(record.sources), record.rating,
                 record.comment, record.followup, record.retrieval_mode, record.rerank,
                 record.threshold, record.llm_model),
            ).fetchone()
        return row[0]

    def purge(self, retention_days: int) -> int:
        """Usuwa treści starszych ocen; zwraca liczbę zmienionych wierszy."""
        with self._connection() as conn:
            cleared = conn.execute(
                "UPDATE feedback SET question = NULL, answer = NULL, comment = NULL "
                "WHERE created_at < now() - make_interval(days => %s) "
                "AND (question IS NOT NULL OR answer IS NOT NULL OR comment IS NOT NULL)",
                (retention_days,),
            ).rowcount
        if cleared:
            log.info("oceny: usunięto treści %d ocen starszych niż %d dni", cleared, retention_days)
        return cleared
