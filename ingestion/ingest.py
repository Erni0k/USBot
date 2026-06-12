"""Pipeline: crawl -> chunking -> embeddingi -> zapis do pgvector.

Crawl jest INKREMENTALNY: stan poprzedniego przebiegu (etag, last-modified,
hash tresci, linki) trzymany jest w tabeli `pages`. Strony, ktore sie nie
zmienily, sa pomijane - nie sa ponownie embedowane ani zapisywane.

Uruchom:            python ingestion/ingest.py
Pelny re-crawl:     CRAWL_FRESH=true python ingestion/ingest.py
Odpalaj okresowo (np. cron raz dziennie), gdy tresci na stronie sie zmienia.
"""
import hashlib
import json
import os

# Model embeddingow jest cache'owany lokalnie po pierwszym pobraniu -> pracuj offline.
# Usuwa ostrzezenie o HF Hub i zawieszanie przy starcie. Pierwsze pobranie: HF_HUB_OFFLINE=0.
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import psycopg
from dotenv import load_dotenv
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pgvector.psycopg import register_vector
from sentence_transformers import SentenceTransformer
from tqdm import tqdm

import scrape

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://chatbot:changeme@localhost:5433/chatbot")
EMBED_MODEL   = os.getenv("EMBED_MODEL", "BAAI/bge-m3")
CRAWL_LIMIT   = int(os.getenv("CRAWL_LIMIT", "2000"))
CRAWL_DEPTH   = int(os.getenv("CRAWL_DEPTH", "2"))
CRAWL_WORKERS = int(os.getenv("CRAWL_WORKERS", "6"))
EMBED_BATCH   = int(os.getenv("EMBED_BATCH", "16"))   # ile stron naraz do embeddingu
CRAWL_FRESH   = os.getenv("CRAWL_FRESH", "false").lower() == "true"

splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=120)
model = SentenceTransformer(EMBED_MODEL)


def _ensure_schema(conn: psycopg.Connection) -> None:
    """Tworzy tabele/indeksy jesli baza wstala bez init.sql."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pages (
            url           TEXT PRIMARY KEY,
            etag          TEXT,
            last_modified TEXT,
            content_hash  TEXT,
            links         JSONB,
            last_crawled  TIMESTAMPTZ DEFAULT now()
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS chunks_url_idx ON chunks (url)")
    conn.commit()


def _load_cache(conn: psycopg.Connection) -> dict[str, dict]:
    """Wczytuje stan poprzedniego crawla z tabeli pages."""
    rows = conn.execute(
        "SELECT url, etag, last_modified, content_hash, links FROM pages"
    ).fetchall()
    return {
        r[0]: {"etag": r[1], "last_modified": r[2], "content_hash": r[3], "links": r[4]}
        for r in rows
    }


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _touch_page(conn: psycopg.Connection, page: scrape.Page) -> None:
    """Strona bez zmian: tylko odswiezamy last_crawled (oraz etag/linki przy 304->200)."""
    conn.execute(
        """
        INSERT INTO pages (url, etag, last_modified, links, last_crawled)
        VALUES (%s, %s, %s, %s, now())
        ON CONFLICT (url) DO UPDATE SET
            etag          = COALESCE(EXCLUDED.etag, pages.etag),
            last_modified = COALESCE(EXCLUDED.last_modified, pages.last_modified),
            links         = COALESCE(EXCLUDED.links, pages.links),
            last_crawled  = now()
        """,
        (page.url, page.etag, page.last_modified,
         json.dumps(page.links) if page.links else None),
    )
    conn.commit()


def _flush(conn: psycopg.Connection, pending: list[dict]) -> None:
    """Embeduje i zapisuje paczke ZMIENIONYCH stron w jednej transakcji."""
    all_chunks = [c for p in pending for c in p["chunks"]]
    if not all_chunks:
        return

    all_embeddings = model.encode(all_chunks, normalize_embeddings=True, batch_size=64)

    idx = 0
    with conn.cursor() as cur:
        for p in pending:
            chunks = p["chunks"]
            embs = all_embeddings[idx: idx + len(chunks)]
            idx += len(chunks)

            # Zastap stare fragmenty tej strony
            cur.execute("DELETE FROM chunks WHERE url = %s", (p["url"],))
            for chunk, emb in zip(chunks, embs):
                cur.execute(
                    "INSERT INTO chunks (url, content, embedding) VALUES (%s, %s, %s)",
                    (p["url"], chunk, emb),
                )

            # Zapisz nowy stan strony
            cur.execute(
                """
                INSERT INTO pages (url, etag, last_modified, content_hash, links, last_crawled)
                VALUES (%s, %s, %s, %s, %s, now())
                ON CONFLICT (url) DO UPDATE SET
                    etag          = EXCLUDED.etag,
                    last_modified = EXCLUDED.last_modified,
                    content_hash  = EXCLUDED.content_hash,
                    links         = EXCLUDED.links,
                    last_crawled  = now()
                """,
                (p["url"], p["etag"], p["last_modified"], p["hash"],
                 json.dumps(p["links"]) if p["links"] else None),
            )
    conn.commit()


def run() -> None:
    with psycopg.connect(DATABASE_URL) as conn:
        register_vector(conn)
        _ensure_schema(conn)

        if CRAWL_FRESH:
            print("CRAWL_FRESH=true -> pelny re-crawl (czyszcze chunks i pages)")
            conn.execute("TRUNCATE chunks")
            conn.execute("TRUNCATE pages")
            conn.commit()
            cache: dict[str, dict] = {}
        else:
            cache = _load_cache(conn)
            print(f"Wczytano stan {len(cache)} stron z poprzedniego crawla")

        bar = tqdm(
            scrape.crawl(limit=CRAWL_LIMIT, max_depth=CRAWL_DEPTH,
                         workers=CRAWL_WORKERS, cache=cache),
            total=CRAWL_LIMIT,
            unit="stron",
            dynamic_ncols=True,
        )

        pending: list[dict] = []
        n_new = n_same = 0

        for page in bar:
            # 304 - serwer potwierdzil brak zmian
            if page.status == "unchanged":
                n_same += 1
                _touch_page(conn, page)
                bar.set_postfix_str("= " + page.url.removeprefix("https://us.edu.pl")[:50])
                continue

            if not page.text:
                continue

            new_hash = _hash(page.text)
            old_hash = cache.get(page.url, {}).get("content_hash")

            # Pobrano tresc, ale jest identyczna jak poprzednio -> bez re-embeddingu
            if old_hash and new_hash == old_hash:
                n_same += 1
                _touch_page(conn, page)
                bar.set_postfix_str("= " + page.url.removeprefix("https://us.edu.pl")[:50])
                continue

            # Strona nowa lub zmieniona -> do embeddingu
            n_new += 1
            pending.append({
                "url": page.url,
                "chunks": splitter.split_text(page.text),
                "etag": page.etag,
                "last_modified": page.last_modified,
                "links": page.links,
                "hash": new_hash,
            })
            bar.set_postfix_str("+ " + page.url.removeprefix("https://us.edu.pl")[:50])

            if len(pending) >= EMBED_BATCH:
                _flush(conn, pending)
                pending = []

        if pending:
            _flush(conn, pending)

    print(f"Gotowe. Nowych/zmienionych: {n_new}, bez zmian: {n_same}.")


if __name__ == "__main__":
    run()
