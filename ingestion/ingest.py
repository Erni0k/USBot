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
import sys
from pathlib import Path

import psycopg
from langchain_text_splitters import RecursiveCharacterTextSplitter
from tqdm import tqdm

import scrape

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from usbot.adapters.pgvector import connect  # noqa: E402
from usbot.bootstrap import build_embedder  # noqa: E402
from usbot.config import get_settings  # noqa: E402

# Ustawienia (nazwy zmiennych env bez zmian) - opis w usbot/config.py i README
settings = get_settings()
DATABASE_URL = settings.database_url
CRAWL_LIMIT = settings.crawl_limit
CRAWL_DEPTH = settings.crawl_depth
CRAWL_WORKERS = settings.crawl_workers
EMBED_BATCH = settings.embed_batch          # ile stron naraz do embeddingu
CRAWL_FRESH = settings.crawl_fresh
CRAWL_REFETCH = settings.crawl_refetch      # po zmianie ekstrakcji tekstu
# Fragment, ktorego tresc (bez prefiksu) wystepuje na wiecej niz BOILERPLATE_URLS stronach,
# to boilerplate (przyciski "Udostepnij", same naglowki) - nie trafia do chunks. 0 = wylaczone.
BOILERPLATE_URLS = settings.boilerplate_urls

# Wersja podzialu na fragmenty (sekcje, prefiks, rozmiar). Zapisywana w pages.chunker_version;
# strony zapisane inna wersja sa przy starcie pobierane i embedowane od nowa. Sam hash
# tekstu tego nie zalatwi: zmiana chunkingu nie zmienia tekstu strony, a CRAWL_REFETCH
# liczy embeddingi tylko dla stron ze zmienionym hashem.
# 1 - plaski tekst, RecursiveCharacterTextSplitter(800, 120)
# 2 - podzial po naglowkach, prefiks "<tytul strony> > <naglowek sekcji>"
CHUNKER_VERSION = 2

splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=120)
# Ten sam adapter embeddingow co w cli.py (usbot.adapters.embeddings); model laduje run()
embedder = build_embedder(settings, load_message=None)


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
    conn.execute("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS content_hash TEXT")
    conn.execute("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS body_hash TEXT")
    conn.execute("ALTER TABLE pages ADD COLUMN IF NOT EXISTS chunk_hashes TEXT[]")
    conn.execute("CREATE INDEX IF NOT EXISTS chunks_url_idx ON chunks (url)")
    conn.execute("CREATE INDEX IF NOT EXISTS chunks_content_hash_idx ON chunks (content_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS chunks_body_hash_idx ON chunks (body_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS pages_chunk_hashes_idx ON pages USING gin (chunk_hashes)")
    # Wyszukiwanie pelnotekstowe - opis w init.sql. Dodanie kolumny GENERATED przepisuje
    # cala tabele (z indeksem HNSW), wiec pierwsza migracja trwa dluzej.
    conn.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    conn.execute("""
        CREATE OR REPLACE FUNCTION immutable_unaccent(text) RETURNS text
            LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
            AS $$ SELECT public.unaccent('public.unaccent'::regdictionary, $1) $$
    """)
    conn.execute("""
        ALTER TABLE chunks ADD COLUMN IF NOT EXISTS content_tsv tsvector GENERATED ALWAYS AS
            (to_tsvector('simple'::regconfig, immutable_unaccent(content))) STORED
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS chunks_content_tsv_idx ON chunks USING gin (content_tsv)")
    _backfill_hashes(conn)
    conn.commit()


def _text_hash(text: str) -> str:
    """SHA-256 tekstu po ujednoliceniu bialych znakow (spacje, nowe linie, nbsp)."""
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


def _strip_prefix(title: str | None, content: str) -> str:
    """Tresc zapisanego fragmentu bez prefiksu "<tytul> > <naglowek>" (pierwszej linii)."""
    first, sep, rest = content.partition("\n")
    if title and sep and (first == title or first.startswith(title + " > ")):
        return rest
    return content


def _backfill_hashes(conn: psycopg.Connection) -> None:
    """Uzupelnia hashe fragmentow zapisanych przed dodaniem kolumn content_hash/body_hash."""
    rows = conn.execute("SELECT id, title, content FROM chunks WHERE content_hash IS NULL").fetchall()
    if rows:
        print(f"Uzupelniam content_hash/body_hash dla {len(rows)} fragmentow")
        with conn.cursor() as cur:
            cur.executemany(
                "UPDATE chunks SET content_hash = %s, body_hash = %s WHERE id = %s",
                [(_text_hash(c), _text_hash(_strip_prefix(t, c)), i) for i, t, c in rows],
            )
    conn.execute("""
        UPDATE pages p SET chunk_hashes = c.hashes
        FROM (SELECT url, array_agg(body_hash ORDER BY id) AS hashes FROM chunks
              WHERE url IN (SELECT url FROM pages WHERE chunk_hashes IS NULL)
              GROUP BY url) c
        WHERE p.url = c.url
    """)


def _boilerplate(conn: psycopg.Connection, hashes: list[str] | None = None) -> set[str]:
    """Hashe tresci (body_hash) wystepujace na wiecej niz BOILERPLATE_URLS stronach.

    Liczymy po pages.chunk_hashes - hashach WSZYSTKICH fragmentow strony, takze tych
    niezapisanych w chunks. Liczac po chunks, po wykluczeniu boilerplate'u licznik
    spadlby do zera i kolejna strona zapisalaby go z powrotem.
    Z `hashes` sprawdzamy tylko podane (indeks GIN na chunk_hashes).
    """
    if BOILERPLATE_URLS <= 0:
        return set()
    sql = "SELECT h FROM pages, unnest(chunk_hashes) AS h"
    params: list = []
    if hashes is not None:
        sql += " WHERE chunk_hashes && %s AND h = ANY(%s)"
        params += [hashes, hashes]
    sql += " GROUP BY h HAVING count(DISTINCT url) > %s"
    return {r[0] for r in conn.execute(sql, params + [BOILERPLATE_URLS])}


def _purge_boilerplate(conn: psycopg.Connection) -> int:
    """Usuwa z chunks fragmenty boilerplate'u (np. po zmianie BOILERPLATE_URLS)."""
    bp = _boilerplate(conn)
    if not bp:
        return 0
    n = conn.execute("DELETE FROM chunks WHERE body_hash = ANY(%s)", (list(bp),)).rowcount
    conn.commit()
    return n


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


def _chunks(page: scrape.Page) -> list[dict]:
    """Fragmenty strony: sekcje po naglowkach, za dlugie ciete splitterem.

    Kazdy fragment dostaje prefiks "<tytul strony> > <naglowek sekcji>" - bez niego
    np. "Godziny pracy: pon 10-14" nie mowi, ktorego dziekanatu dotyczy. Prefiks
    jest czescia tresci, z ktorej liczony jest embedding, i trafia tez do LLM-a.

    content_hash (z prefiksem) to klucz cache'u embeddingow; body_hash (bez prefiksu)
    rozpoznaje ten sam tekst na roznych stronach - boilerplate i duplikaty w retrieve().
    """
    chunks = []
    for heading, body in _sections(page.text):
        prefix = " > ".join(p for p in (page.title, heading) if p)
        for part in splitter.split_text(body):
            content = f"{prefix}\n{part}" if prefix else part
            chunks.append({"content": content, "content_hash": _text_hash(content),
                           "body_hash": _text_hash(part)})
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


def _flush(conn: psycopg.Connection, pending: list[dict]) -> tuple[int, int, int]:
    """Embeduje i zapisuje paczke ZMIENIONYCH stron w jednej transakcji.

    Embedding liczymy tylko dla tresci, ktorej content_hash nie ma jeszcze w chunks:
    ten sam fragment na kilku stronach i niezmienione sekcje edytowanej strony biora
    gotowy embedding z bazy. Fragmenty boilerplate'u (_boilerplate) nie sa zapisywane.
    Zwraca (policzone embeddingi, wziete z cache'u, pominiety boilerplate).
    """
    with conn.cursor() as cur:
        # Najpierw stan stron - chunk_hashes paczki licza sie juz w _boilerplate()
        for p in pending:
            cur.execute(
                """
                INSERT INTO pages (url, etag, last_modified, content_hash, links,
                                   chunker_version, chunk_hashes, last_crawled)
                VALUES (%s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (url) DO UPDATE SET
                    etag            = EXCLUDED.etag,
                    last_modified   = EXCLUDED.last_modified,
                    content_hash    = EXCLUDED.content_hash,
                    links           = EXCLUDED.links,
                    chunker_version = EXCLUDED.chunker_version,
                    chunk_hashes    = EXCLUDED.chunk_hashes,
                    last_crawled    = now()
                """,
                (p["url"], p["etag"], p["last_modified"], p["hash"],
                 json.dumps(p["links"]) if p["links"] else None, CHUNKER_VERSION,
                 [c["body_hash"] for c in p["chunks"]]),
            )

        boiler = _boilerplate(conn, list({c["body_hash"] for p in pending for c in p["chunks"]}))
        keep = [c for p in pending for c in p["chunks"] if c["body_hash"] not in boiler]

        # Cache sprawdzamy przed usunieciem starych fragmentow - to glowne zrodlo trafien
        cache = dict(cur.execute(
            "SELECT DISTINCT ON (content_hash) content_hash, embedding FROM chunks "
            "WHERE content_hash = ANY(%s)",
            (list({c["content_hash"] for c in keep}),),
        ).fetchall())
        missing = {c["content_hash"]: c["content"] for c in keep if c["content_hash"] not in cache}
        if missing:
            embs = embedder.embed_batch(list(missing.values()))
            cache.update(zip(missing, embs))

        for p in pending:
            # Zastap stare fragmenty tej strony
            cur.execute("DELETE FROM chunks WHERE url = %s", (p["url"],))
            for c in p["chunks"]:
                if c["body_hash"] in boiler:
                    continue
                cur.execute(
                    "INSERT INTO chunks (url, title, page_type, published_at, content, "
                    "content_hash, body_hash, embedding) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (p["url"], p["title"], p["page_type"], p["published_at"], c["content"],
                     c["content_hash"], c["body_hash"], cache[c["content_hash"]]),
                )

        # Kopie zapisane z innych stron, zanim fragment przekroczyl prog
        if boiler:
            cur.execute("DELETE FROM chunks WHERE body_hash = ANY(%s)", (list(boiler),))
    conn.commit()
    # Kazda tresc embedowana raz: reszta zapisanych fragmentow uzyla gotowego embeddingu
    n_all = sum(len(p["chunks"]) for p in pending)
    return len(missing), len(keep) - len(missing), n_all - len(keep)


def run() -> None:
    embedder.load()  # jak dotad: model przed crawlem, blad modelu wychodzi od razu
    with connect(DATABASE_URL) as conn:
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
            n_boiler = _purge_boilerplate(conn)
            if n_boiler:
                print(f"Usunieto {n_boiler} fragmentow boilerplate'u "
                      f"(ta sama tresc na >{BOILERPLATE_URLS} stronach)")
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
        stats = [0, 0, 0]  # embeddingi policzone, z cache'u, pominiety boilerplate

        def flush() -> None:
            for i, n in enumerate(_flush(conn, pending)):
                stats[i] += n

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
                flush()
                pending = []

        if pending:
            flush()

    print(f"Gotowe. Nowych/zmienionych: {n_new}, bez zmian: {n_same}. Fragmenty: "
          f"{stats[0]} embeddingow policzonych, {stats[1]} z cache'u, "
          f"{stats[2]} pominietych (boilerplate).")


if __name__ == "__main__":
    run()
