-- Tworzone automatycznie przy pierwszym starcie kontenera Postgres.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS unaccent;

-- unaccent() jest STABLE (slownik mozna podmienic), a kolumna GENERATED wymaga
-- wyrazenia IMMUTABLE. Opakowanie z jawnym slownikiem i schematem nie zalezy od
-- search_path. Zmiana tresci funkcji nie przelicza zapisanych wartosci chunks.content_tsv
-- - po takiej zmianie trzeba odtworzyc kolumne.
CREATE OR REPLACE FUNCTION immutable_unaccent(text) RETURNS text
    LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
    AS $$ SELECT public.unaccent('public.unaccent'::regdictionary, $1) $$;

CREATE TABLE IF NOT EXISTS chunks (
    id          BIGSERIAL PRIMARY KEY,
    url         TEXT NOT NULL,
    title       TEXT,                    -- <title> strony bez nazwy serwisu
    page_type   TEXT,                    -- student | wydzial | static | news | event
    published_at TIMESTAMPTZ,            -- data publikacji (article:published_time, data z tresci)
    content     TEXT NOT NULL,           -- z prefiksem "<tytul> > <naglowek sekcji>"
    content_hash TEXT,                   -- SHA-256 content po ujednoliceniu bialych znakow; cache embeddingow
    body_hash   TEXT,                    -- to samo bez prefiksu; boilerplate i duplikaty w retrieve()
    embedding   vector(1024) NOT NULL,   -- bge-m3 = 1024 wymiarów
    created_at  TIMESTAMPTZ DEFAULT now(),
    -- Wyszukiwanie pelnotekstowe (RETRIEVAL_MODE=hybrid). Postgres nie ma konfiguracji
    -- dla polskiego, wiec 'simple' (bez stemmingu i stop-slow) + unaccent: "WNŚiT" i
    -- "wnsit" daja ten sam leksem.
    content_tsv tsvector GENERATED ALWAYS AS
        (to_tsvector('simple'::regconfig, immutable_unaccent(content))) STORED
);

-- Indeks pod wyszukiwanie kosinusowe (operator <=>)
CREATE INDEX IF NOT EXISTS chunks_embedding_idx
    ON chunks USING hnsw (embedding vector_cosine_ops);

-- Wyszukiwanie pelnotekstowe (operator @@)
CREATE INDEX IF NOT EXISTS chunks_content_tsv_idx ON chunks USING gin (content_tsv);

-- Szybkie usuwanie/aktualizacja fragmentow danej strony przy re-crawlu
CREATE INDEX IF NOT EXISTS chunks_url_idx ON chunks (url);

-- Cache embeddingow (content_hash) i wykluczanie boilerplate'u (body_hash)
CREATE INDEX IF NOT EXISTS chunks_content_hash_idx ON chunks (content_hash);
CREATE INDEX IF NOT EXISTS chunks_body_hash_idx ON chunks (body_hash);

-- Stan crawlera: pozwala pomijac strony, ktore sie nie zmienily.
CREATE TABLE IF NOT EXISTS pages (
    url           TEXT PRIMARY KEY,
    etag          TEXT,            -- naglowek ETag z ostatniego pobrania
    last_modified TEXT,            -- naglowek Last-Modified z ostatniego pobrania
    content_hash  TEXT,            -- SHA-256 wyekstrahowanego tekstu i metadanych
    links         JSONB,           -- linki znalezione na stronie (do BFS przy 304)
    chunker_version INT,           -- CHUNKER_VERSION z ingest.py; inna = podziel od nowa
    chunk_hashes  TEXT[],          -- body_hash wszystkich fragmentow strony (takze boilerplate'u)
    last_crawled  TIMESTAMPTZ DEFAULT now()
);

-- Liczenie, na ilu stronach wystepuje fragment (regula boilerplate'u)
CREATE INDEX IF NOT EXISTS pages_chunk_hashes_idx ON pages USING gin (chunk_hashes);
