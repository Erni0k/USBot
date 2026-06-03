"""Pipeline: crawl -> chunking -> embeddingi -> zapis do pgvector.

Uruchom: python ingestion/ingest.py
Odpalaj okresowo (np. cron raz na tydzien), gdy tresci na stronie sie zmienia.
"""
import os

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

splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=120)
model = SentenceTransformer(EMBED_MODEL)


def _flush(conn: psycopg.Connection, pages: list[tuple[str, list[str]]]) -> None:
    """Embeduje i zapisuje paczke stron w jednej transakcji."""
    all_chunks = [chunk for _, chunks in pages for chunk in chunks]
    if not all_chunks:
        return

    all_embeddings = model.encode(all_chunks, normalize_embeddings=True, batch_size=64)

    idx = 0
    with conn.cursor() as cur:
        for url, chunks in pages:
            embs = all_embeddings[idx: idx + len(chunks)]
            idx += len(chunks)
            for chunk, emb in zip(chunks, embs):
                cur.execute(
                    "INSERT INTO chunks (url, content, embedding) VALUES (%s, %s, %s)",
                    (url, chunk, emb),
                )
    conn.commit()


def run() -> None:
    with psycopg.connect(DATABASE_URL) as conn:
        register_vector(conn)
        conn.execute("TRUNCATE chunks")
        conn.commit()

        bar = tqdm(
            scrape.crawl(limit=CRAWL_LIMIT, max_depth=CRAWL_DEPTH, workers=CRAWL_WORKERS),
            total=CRAWL_LIMIT,
            unit="stron",
            dynamic_ncols=True,
        )

        pending: list[tuple[str, list[str]]] = []
        total_pages = 0

        for url, text in bar:
            total_pages += 1
            chunks = splitter.split_text(text)
            pending.append((url, chunks))
            bar.set_postfix_str(url.removeprefix("https://us.edu.pl")[:60])

            if len(pending) >= EMBED_BATCH:
                _flush(conn, pending)
                pending = []

        if pending:
            _flush(conn, pending)

    print(f"Gotowe. Zaindeksowano {total_pages} stron.")


if __name__ == "__main__":
    run()
