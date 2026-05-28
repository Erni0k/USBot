"""Pipeline: scrape -> chunking -> embeddingi -> zapis do pgvector.

Uruchom: python ingest.py
Odpalaj okresowo (np. cron), gdy treści na stronie się zmienią.
"""
import os
import time

import psycopg
from dotenv import load_dotenv
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pgvector.psycopg import register_vector
from sentence_transformers import SentenceTransformer

import scrape

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://chatbot:changeme@localhost:5432/chatbot")
EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-m3")

splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=120)
model = SentenceTransformer(EMBED_MODEL)


def run(limit: int = 200) -> None:
    urls = scrape.get_urls(limit=limit)
    print(f"Znaleziono {len(urls)} URL-i.")

    with psycopg.connect(DATABASE_URL) as conn:
        register_vector(conn)
        # Pełne odświeżenie. Na produkcji rozważ upsert po URL zamiast TRUNCATE.
        conn.execute("TRUNCATE chunks")
        conn.commit()

        for i, url in enumerate(urls, 1):
            try:
                text = scrape.fetch_text(url)
            except Exception as e:
                print(f"[{i}/{len(urls)}] pomijam {url}: {e}")
                continue
            if not text:
                continue

            chunks = splitter.split_text(text)
            embeddings = model.encode(chunks, normalize_embeddings=True)

            with conn.cursor() as cur:
                for chunk, emb in zip(chunks, embeddings):
                    cur.execute(
                        "INSERT INTO chunks (url, content, embedding) VALUES (%s, %s, %s)",
                        (url, chunk, emb),
                    )
            conn.commit()
            print(f"[{i}/{len(urls)}] {url} -> {len(chunks)} fragmentów")
            time.sleep(0.5)  # bądź grzeczny dla serwera uczelni

    print("Gotowe.")


if __name__ == "__main__":
    run()
