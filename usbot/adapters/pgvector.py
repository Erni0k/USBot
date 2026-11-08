"""Retriever na PostgreSQL + pgvector: wyszukiwanie wektorowe (HNSW) i hybrydowe (+ FTS, RRF).

Schemat tabeli `chunks`: infra/init.sql. Kolejność, duplikaty i fuzja list są w
`usbot.core.ranking`; tu są zapytania SQL i zamiana wierszy na `Chunk`.
"""
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Literal

import psycopg
from pgvector.psycopg import register_vector

from usbot.core.models import Chunk
from usbot.core.ranking import merge_best, rank_key, rrf, select
from usbot.ports import Embedder

Mode = Literal["vector", "hybrid"]
MODES = ("vector", "hybrid")

# Słowa pytań, które nic nie mówią wyszukiwarce pełnotekstowej (po unaccent). Konfiguracja
# 'simple' nie ma stop-słów, a ts_rank_cd nie zna IDF — bez tej listy „jak”, „się”, „do”
# liczyłyby się tak samo jak „USOS”.
FTS_STOPWORDS = frozenset("""
    a aby albo ale bo by byc czy co czym czego dla do gdy gdzie i ich ile jak jaka jaki
    jakie jakich jakim jakiej jako jest juz kiedy kto ktora ktore ktory ktorych lub ma mam
    mi mnie moge mozna moj moja moje na nad nie o od oraz po pod przez przy sa sie sobie
    ta tak te tego tej ten to tu u w we z za ze
    an and are can does how in is my of on or the what when where which who
""".split())

_COLUMNS = (
    "id, url, title, content, page_type, published_at, body_hash, "
    "embedding <=> %(qvec)s::vector AS dist, "
    "page_type IN ('news', 'event') "
    "  AND published_at < now() - make_interval(days => %(old_days)s) AS old_news"
)


def connect(database_url: str) -> psycopg.Connection:
    """Połączenie z zarejestrowanym typem `vector` (wektory jako numpy)."""
    conn = psycopg.connect(database_url)
    register_vector(conn)
    return conn


def _configure(conn: psycopg.Connection) -> None:
    """Nowe połączenie w puli: typ `vector`; pula wymaga połączenia bez otwartej transakcji."""
    register_vector(conn)
    conn.commit()


def create_pool(database_url: str, *, min_size: int = 1, max_size: int = 4,
                timeout: float = 10.0):
    """Pula połączeń (psycopg_pool) dla API: połączenia otwierane raz i używane ponownie.

    `timeout` (sekundy) ogranicza czekanie na połączenie z puli, nawiązanie połączenia
    (`connect_timeout`) i czas pojedynczego zapytania (`statement_timeout`). Pula jest
    zwracana nieotwarta — otwiera ją `open()` (lifespan aplikacji); każde połączenie jest
    sprawdzane przed wydaniem, więc restart bazy nie zostawia martwych połączeń.
    """
    from psycopg_pool import ConnectionPool
    return ConnectionPool(
        database_url, min_size=min_size, max_size=max_size, timeout=timeout, open=False,
        configure=_configure, check=ConnectionPool.check_connection,
        kwargs={"connect_timeout": max(1, round(timeout)),
                "options": f"-c statement_timeout={round(timeout * 1000)}"},
        name="usbot",
    )


def distinct_urls(database_url: str) -> set[str]:
    """Wszystkie adresy stron, które mają fragmenty w bazie."""
    with psycopg.connect(database_url) as conn:
        return {r[0] for r in conn.execute("SELECT DISTINCT url FROM chunks")}


def _to_chunk(r: tuple, penalty: float) -> Chunk:
    return Chunk(id=r[0], url=r[1], title=r[2], content=r[3], page_type=r[4],
                 published_at=r[5], body_hash=r[6], dist=r[7],
                 score=r[7] + (penalty if r[8] else 0.0))


def fulltext_query(conn: psycopg.Connection, question: str) -> str | None:
    """tsquery z pytania: jego leksemy połączone OR, bez FTS_STOPWORDS; None, gdy pusto.

    Leksemy liczy Postgres tą samą konfiguracją co chunks.content_tsv ('simple' +
    unaccent), więc zgadzają się z dokumentami. OR zamiast AND (plainto_tsquery,
    websearch_to_tsquery): bez stemmingu „dziekanatu” w pytaniu nie pasuje do
    „dziekanat” w treści, a AND wymagałoby każdego słowa w dokładnie tej formie.
    Porównanie wariantów: docs/testy.md (6.4).
    """
    lexemes = conn.execute(
        "SELECT unnest(tsvector_to_array(to_tsvector('simple', immutable_unaccent(%s))))",
        (question,),
    ).fetchall()
    terms = [lex for (lex,) in lexemes if lex not in FTS_STOPWORDS]
    if not terms:
        return None
    return " | ".join("'" + t.replace("\\", "\\\\").replace("'", "''") + "'" for t in terms)


class PgvectorRetriever:
    """Retriever (port) na tabeli `chunks`.

    `mode`: "vector" — według odległości wektorowej; "hybrid" — top-N wektorowo i top-N
    pełnotekstowo, złączone przez RRF. Embeddingi słabo radzą sobie ze skrótami i
    nazwami własnymi (USOS, IRK, ELS, WNŚiT, CINiBA) — część pełnotekstowa znajduje je
    po dokładnym słowie. Każdy wynik ma `dist` (odległość wektorową), więc próg
    MAX_DISTANCE działa w obu trybach, także dla fragmentów znalezionych tylko
    pełnotekstowo.

    Z `old_news_penalty` > 0 fragmenty aktualności/wydarzeń starszych niż
    `old_news_days` dostają `score` = dist + kara i według niego są sortowane; próg
    nadal dotyczy samej odległości. `max_per_url`: limit fragmentów z jednej strony
    (0 = bez limitu).

    Z `pool` (create_pool()) połączenia pochodzą z puli; bez niej każde wyszukiwanie
    otwiera własne połączenie z `database_url` (cli.py, evaluate.py).
    """

    def __init__(self, database_url: str, embedder: Embedder, *, mode: Mode = "vector",
                 old_news_penalty: float = 0.0, old_news_days: int = 730,
                 max_per_url: int = 2, pool=None) -> None:
        if mode not in MODES:
            raise ValueError(f"RETRIEVAL_MODE musi być 'vector' albo 'hybrid', jest {mode!r}")
        self.database_url = database_url
        self.embedder = embedder
        self.mode = mode
        self.old_news_penalty = old_news_penalty
        self.old_news_days = old_news_days
        self.max_per_url = max_per_url
        self.pool = pool

    @contextmanager
    def _connection(self, timeout: float | None = None) -> Iterator[psycopg.Connection]:
        """Połączenie na jedno wyszukiwanie — jedna transakcja, zatwierdzana na końcu."""
        if self.pool is not None:
            with self.pool.connection(timeout=timeout) as conn:
                yield conn
        else:
            with connect(self.database_url) as conn:
                yield conn

    def ping(self, timeout: float | None = None) -> dict:
        """Sprawdzenie dla /api/health: baza odpowiada, a tabela chunks nie jest pusta."""
        with self._connection(timeout) as conn:
            if not conn.execute("SELECT EXISTS (SELECT 1 FROM chunks)").fetchone()[0]:
                raise RuntimeError("tabela chunks jest pusta — uruchom ingestion/ingest.py")
        return {"chunks": True}

    # --- port Retriever ---

    def search(self, queries: Sequence[str], k: int) -> list[Chunk]:
        """Do k fragmentów dla zapytań, bez progu (port Retriever).

        Każdy fragment dostaje lepszy z wyników po zapytaniach (merge_best); duplikaty
        treści i limit na stronę (select) stosujemy po złączeniu.
        """
        merged = merge_best(self.candidates(q, k) for q in queries)
        return select(merged, k, self.max_per_url)

    # --- kandydaci ---

    def candidates(self, question: str, k: int) -> list[Chunk]:
        """Kandydaci z zapasem (5·k, min. 40), posortowani według `score`.

        Zapas jest potrzebny, bo select() pomija duplikaty i nadmiar fragmentów z jednej
        strony, a kara za stare aktualności zmienia kolejność.
        W trybie hybrid z zapasem pobieramy obie listy, a `score` to wynik rrf(); kara za
        stare aktualności działa wtedy na kolejność listy wektorowej przed fuzją.
        """
        qvec = self.embedder.embed(question)
        n = max(5 * k, 40)
        with self._connection() as conn:
            chunks = self._vector_candidates(conn, qvec, n)
            if self.mode == "hybrid":
                chunks = rrf(chunks, self._fulltext_candidates(conn, question, qvec, n))
        return chunks

    def candidate_lists(self, question: str, n: int) -> tuple[list[Chunk], list[Chunk]]:
        """Osobno n kandydatów wektorowych i n pełnotekstowych, niezależnie od `mode`.

        Do oznaczania pytań testowych (evaluation/feedback_export.py): etykiety nie mogą
        faworyzować żadnego ze sposobów wyszukiwania.
        """
        qvec = self.embedder.embed(question)
        with self._connection() as conn:
            return (self._vector_candidates(conn, qvec, n),
                    self._fulltext_candidates(conn, question, qvec, n))

    def _vector_candidates(self, conn: psycopg.Connection, qvec: list[float],
                           n: int) -> list[Chunk]:
        """n najbliższych fragmentów z indeksu HNSW, posortowanych według `score`."""
        if n > 40:  # domyślny hnsw.ef_search = 40 to też limit liczby wyników
            # SET LOCAL: tylko do końca transakcji — połączenie z puli wraca bez zmiany
            conn.execute(f"SET LOCAL hnsw.ef_search = {int(n)}")
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM chunks ORDER BY dist LIMIT %(n)s",
            {"qvec": qvec, "old_days": self.old_news_days, "n": n},
        ).fetchall()
        return sorted((_to_chunk(r, self.old_news_penalty) for r in rows), key=rank_key)

    def _fulltext_candidates(self, conn: psycopg.Connection, question: str,
                             qvec: list[float], n: int) -> list[Chunk]:
        """n fragmentów najlepiej pasujących słowami (ts_rank_cd), w kolejności rankingu.

        Odległość wektorową (`dist`) liczymy tu osobno dla każdego fragmentu — potrzebuje
        jej próg MAX_DISTANCE, także dla fragmentów, których nie znalazło HNSW. Przy remisie
        ts_rank_cd (częsty: fragmenty z jednym wystąpieniem słowa) wygrywa bliższy wektorowo.
        """
        query = fulltext_query(conn, question)
        if query is None:
            return []
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM chunks, CAST(%(q)s AS tsquery) AS q "
            "WHERE content_tsv @@ q ORDER BY ts_rank_cd(content_tsv, q) DESC, dist LIMIT %(n)s",
            {"qvec": qvec, "old_days": self.old_news_days, "q": query, "n": n},
        ).fetchall()
        return [_to_chunk(r, self.old_news_penalty) for r in rows]
