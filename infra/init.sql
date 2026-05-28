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
