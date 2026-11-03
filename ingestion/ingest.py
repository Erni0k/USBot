"""Pipeline: crawl -> chunking -> embeddingi -> zapis do pgvector.

Crawl jest INKREMENTALNY: stan poprzedniego przebiegu (etag, last-modified,
hash tresci, linki) trzymany jest w tabeli `pages`. Strony, ktore sie nie
zmienily, sa pomijane - nie sa ponownie embedowane ani zapisywane.

Uruchom:            python ingestion/ingest.py
Pelny re-crawl:     CRAWL_FRESH=true python ingestion/ingest.py
Ponowna ekstrakcja: CRAWL_REFETCH=true python ingestion/ingest.py
                    (np. po zmianie ekstrakcji tekstu; embeduje tylko zmienione strony)
Po zmianie chunkingu podbij CHUNKER_VERSION - kolejny przebieg pobierze i podzieli
od nowa wszystkie strony zapisane starsza wersja (bez CRAWL_REFETCH).
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
CRAWL_REFETCH = os.getenv("CRAWL_REFETCH", "false").lower() == "true"  # po zmianie ekstrakcji tekstu

# Wersja podzialu na fragmenty (sekcje, prefiks, rozmiar). Zapisywana w pages.chunker_version;
# strony zapisane inna wersja sa przy starcie pobierane i embedowane od nowa. Sam hash
# tekstu tego nie zalatwi: zmiana chunkingu nie zmienia tekstu strony, a CRAWL_REFETCH
# liczy embeddingi tylko dla stron ze zmienionym hashem.
# 1 - plaski tekst, RecursiveCharacterTextSplitter(800, 120)
# 2 - podzial po naglowkach, prefiks "<tytul strony> > <naglowek sekcji>"
CHUNKER_VERSION = 2

splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=120)
model = SentenceTransformer(EMBED_MODEL)


def _ensure_schema(conn: psycopg.Connection) -> None:
    """Tworzy tabele/indeksy jesli baza wstala bez init.sql i migruje starszy schemat.

    init.sql wykonuje sie tylko przy pierwszym starcie kontenera, wiec nowe kolumny
    dodajemy tutaj (ADD COLUMN IF NOT EXISTS) - takze w istniejacej bazie.
    """
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
    conn.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS chunker_version INT")
    conn.execute("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS page_type TEXT")
    conn.execute("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ")
    conn.execute("CREATE INDEX IF NOT EXISTS chunks_url_idx ON chunks (url)")
    conn.commit()


def _load_cache(conn: psycopg.Connection) -> dict[str, dict]:
    """Wczytuje stan poprzedniego crawla z tabeli pages."""
    rows = conn.execute(
        "SELECT url, etag, last_modified, content_hash, links, chunker_version FROM pages"
    ).fetchall()
    return {
        r[0]: {"etag": r[1], "last_modified": r[2], "content_hash": r[3], "links": r[4],
               "chunker_version": r[5]}
        for r in rows
    }


def _purge_excluded(conn: psycopg.Connection) -> int:
    """Usuwa z bazy strony, ktore obecne reguly crawlera wykluczaja (np. galerie)."""
    urls = [r[0] for r in conn.execute("SELECT url FROM pages UNION SELECT url FROM chunks")]
    bad = [u for u in urls if scrape.excluded(u)]
    if bad:
        conn.execute("DELETE FROM chunks WHERE url = ANY(%s)", (bad,))
        conn.execute("DELETE FROM pages WHERE url = ANY(%s)", (bad,))
        conn.commit()
    return len(bad)


def _hash(page: scrape.Page) -> str:
    """Hash wszystkiego, z czego powstaja fragmenty: tekstu i metadanych strony."""
    key = "\n".join(str(v) for v in (page.title, page.page_type, page.published_at, page.text))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _sections(text: str) -> list[tuple[str | None, str]]:
    """Dzieli tekst na sekcje po naglowkach markdown: [(naglowek, tresc sekcji)].

    Tresc zaczyna sie od naglowka (juz bez "#"). Naglowek bez wlasnej tresci (np. h2,
    po ktorym od razu jest h3) laczymy z nastepna sekcja - prefiks dostaje wtedy
    blizszy, bardziej szczegolowy naglowek, a nadrzedny zostaje w tresci.
    """
    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    for line in text.splitlines():
        m = scrape.HEADING_RE.match(line)
        if not m:
            sections[-1][1].append(line)
            continue
        heading, lines = sections[-1]
        if heading is not None and len(lines) == 1:  # sam naglowek bez tresci
            sections[-1] = (m.group(2), lines + [m.group(2)])
        else:
            sections.append((m.group(2), [m.group(2)]))
    return [(h, "\n".join(lines)) for h, lines in sections if lines]


def _chunks(page: scrape.Page) -> list[str]:
    """Fragmenty strony: sekcje po naglowkach, za dlugie ciete splitterem.

    Kazdy fragment dostaje prefiks "<tytul strony> > <naglowek sekcji>" - bez niego
    np. "Godziny pracy: pon 10-14" nie mowi, ktorego dziekanatu dotyczy. Prefiks
    jest czescia tresci, z ktorej liczony jest embedding, i trafia tez do LLM-a.
    """
    chunks = []
    for heading, body in _sections(page.text):
        prefix = " > ".join(p for p in (page.title, heading) if p)
        for part in splitter.split_text(body):
            chunks.append(f"{prefix}\n{part}" if prefix else part)
    return chunks


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
                    "INSERT INTO chunks (url, title, page_type, published_at, content, embedding) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (p["url"], p["title"], p["page_type"], p["published_at"], chunk, emb),
                )

            # Zapisz nowy stan strony
            cur.execute(
                """
                INSERT INTO pages (url, etag, last_modified, content_hash, links,
                                   chunker_version, last_crawled)
                VALUES (%s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (url) DO UPDATE SET
                    etag            = EXCLUDED.etag,
                    last_modified   = EXCLUDED.last_modified,
                    content_hash    = EXCLUDED.content_hash,
                    links           = EXCLUDED.links,
                    chunker_version = EXCLUDED.chunker_version,
                    last_crawled    = now()
                """,
                (p["url"], p["etag"], p["last_modified"], p["hash"],
                 json.dumps(p["links"]) if p["links"] else None, CHUNKER_VERSION),
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
            n_purged = _purge_excluded(conn)
            if n_purged:
                print(f"Usunieto {n_purged} stron wykluczonych przez reguly crawlera")
            cache = _load_cache(conn)
            print(f"Wczytano stan {len(cache)} stron z poprzedniego crawla")
            if CRAWL_REFETCH:
                # Bez naglowkow warunkowych serwer nie odpowie 304, wiec kazda strona
                # zostanie pobrana i wyekstrahowana od nowa. Hash tresci zostaje,
                # wiec embeddingi licza sie tylko dla stron, ktorych tekst sie zmienil.
                print("CRAWL_REFETCH=true -> pobieram wszystkie strony od nowa")
                for entry in cache.values():
                    entry["etag"] = entry["last_modified"] = None
            # Strony podzielone inna wersja chunkera: pobierz od nowa (bez 304) i zignoruj
            # hash, zeby zostaly podzielone i embedowane ponownie.
            stale = [e for e in cache.values() if e["chunker_version"] != CHUNKER_VERSION]
            if stale:
                print(f"{len(stale)} stron podzielonych inna wersja chunkera "
                      f"-> pobieram i embeduje od nowa (CHUNKER_VERSION={CHUNKER_VERSION})")
                for entry in stale:
                    entry["etag"] = entry["last_modified"] = entry["content_hash"] = None

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

            new_hash = _hash(page)
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
                "chunks": _chunks(page),
                "title": page.title,
                "page_type": page.page_type,
                "published_at": page.published_at,
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
