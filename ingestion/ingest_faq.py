"""Wczytuje ręcznie sprawdzone odpowiedzi z knowledge/faq.jsonl do tabeli `chunks`.

Każde pytanie wpisu to osobny fragment z tą samą odpowiedzią i tym samym `body_hash`, więc
wyszukiwarka trafia po dowolnym sformułowaniu, a select() zostawia jeden. Wpis ma adres
`faq://<id>#<strona źródłowa>`; crawler go nie usuwa, a stopka odpowiedzi oznacza go jako FAQ.
Plik jest źródłem prawdy: stare wpisy są zastępowane w jednej transakcji.

Uruchom:  python ingestion/ingest_faq.py    (robi to też ingestion/ingest.py po crawlu)
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from usbot.core.citations import FAQ_SCHEME  # noqa: E402

__all__ = ["FAQ_SCHEME", "faq_chunks", "load_entries", "load_faq"]

FAQ_FILE = ROOT / "knowledge" / "faq.jsonl"


def _hash(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


def load_entries(path: Path = FAQ_FILE) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def faq_chunks(entry: dict) -> list[dict]:
    """Fragmenty jednego wpisu: po jednym na sformułowanie pytania."""
    url = FAQ_SCHEME + entry["id"] + (f"#{entry['source']}" if entry.get("source") else "")
    body = entry["answer"] + (f"\nStrona: {entry['source']}" if entry.get("source") else "")
    return [{"url": url, "title": f"FAQ: {entry['questions'][0]}", "content": f"{question}\n{body}",
             "content_hash": _hash(f"{question}\n{body}"), "body_hash": _hash(body)}
            for question in entry["questions"]]


def load_faq(conn, embedder, path: Path = FAQ_FILE) -> int:
    """Zastępuje wpisy FAQ w `chunks` zawartością pliku; zwraca liczbę fragmentów."""
    chunks = [c for entry in load_entries(path) for c in faq_chunks(entry)]
    embeddings = embedder.embed_batch([c["content"] for c in chunks])
    with conn.cursor() as cur:
        cur.execute("DELETE FROM chunks WHERE page_type = 'faq'")
        cur.executemany(
            "INSERT INTO chunks (url, title, page_type, published_at, content, content_hash, "
            "body_hash, embedding) VALUES (%s, %s, 'faq', NULL, %s, %s, %s, %s)",
            [(c["url"], c["title"], c["content"], c["content_hash"], c["body_hash"], e)
             for c, e in zip(chunks, embeddings, strict=True)])
    conn.commit()
    return len(chunks)


if __name__ == "__main__":
    from usbot.adapters.pgvector import connect
    from usbot.bootstrap import build_embedder
    from usbot.config import get_settings
    settings = get_settings()
    embedder = build_embedder(settings)
    embedder.load()
    with connect(settings.database_url) as connection:
        print(f"Wpisy FAQ: {load_faq(connection, embedder)} fragmentów")
