"""Retrieval: embedding pytania + wyszukanie najbliższych fragmentów w pgvector."""
from functools import lru_cache

import psycopg
from pgvector.psycopg import register_vector
from sentence_transformers import SentenceTransformer

from . import config


@lru_cache(maxsize=1)
def get_embedder() -> SentenceTransformer:
    # ładowany leniwie i tylko raz (model ~2 GB pobierany przy pierwszym użyciu)
    return SentenceTransformer(config.EMBED_MODEL)


def embed(text: str) -> list[float]:
    vec = get_embedder().encode(text, normalize_embeddings=True)
    return vec.tolist()


def retrieve(question: str, k: int = config.TOP_K) -> list[dict]:
    qvec = embed(question)
    with psycopg.connect(config.DATABASE_URL) as conn:
        register_vector(conn)
        rows = conn.execute(
            """
            SELECT url, title, content
            FROM chunks
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            (qvec, k),
        ).fetchall()
    return [{"url": r[0], "title": r[1], "content": r[2]} for r in rows]


def build_context(chunks: list[dict]) -> str:
    if not chunks:
        return "(brak pasujących informacji)"
    return "\n\n".join(f"[Źródło: {c['url']}]\n{c['content']}" for c in chunks)
