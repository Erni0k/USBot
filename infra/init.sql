-- Tworzone automatycznie przy pierwszym starcie kontenera Postgres.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS chunks (
    id          BIGSERIAL PRIMARY KEY,
    url         TEXT NOT NULL,
    title       TEXT,                    -- <title> strony bez nazwy serwisu
    page_type   TEXT,                    -- student | wydzial | static | news | event
    published_at TIMESTAMPTZ,            -- data publikacji (article:published_time, data z tresci)
    content     TEXT NOT NULL,           -- z prefiksem "<tytul> > <naglowek sekcji>"
    embedding   vector(1024) NOT NULL,   -- bge-m3 = 1024 wymiarów
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- Indeks pod wyszukiwanie kosinusowe (operator <=>)
CREATE INDEX IF NOT EXISTS chunks_embedding_idx
    ON chunks USING hnsw (embedding vector_cosine_ops);

-- Szybkie usuwanie/aktualizacja fragmentow danej strony przy re-crawlu
CREATE INDEX IF NOT EXISTS chunks_url_idx ON chunks (url);

-- Stan crawlera: pozwala pomijac strony, ktore sie nie zmienily.
CREATE TABLE IF NOT EXISTS pages (
    url           TEXT PRIMARY KEY,
    etag          TEXT,            -- naglowek ETag z ostatniego pobrania
    last_modified TEXT,            -- naglowek Last-Modified z ostatniego pobrania
    content_hash  TEXT,            -- SHA-256 wyekstrahowanego tekstu i metadanych
    links         JSONB,           -- linki znalezione na stronie (do BFS przy 304)
    chunker_version INT,           -- CHUNKER_VERSION z ingest.py; inna = podziel od nowa
    last_crawled  TIMESTAMPTZ DEFAULT now()
);
