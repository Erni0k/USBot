-- Tworzone automatycznie przy pierwszym starcie kontenera Postgres.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS chunks (
    id          BIGSERIAL PRIMARY KEY,
    url         TEXT NOT NULL,
    title       TEXT,
    content     TEXT NOT NULL,
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
    content_hash  TEXT,            -- SHA-256 wyekstrahowanego tekstu
    links         JSONB,           -- linki znalezione na stronie (do BFS przy 304)
    last_crawled  TIMESTAMPTZ DEFAULT now()
);
