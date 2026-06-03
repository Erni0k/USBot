"""Bot Uniwersytetu Śląskiego — aplikacja terminalowa."""
import os
import sys
from functools import lru_cache

# UTF-8 na Windows (cp1252 nie obsługuje polskich znaków)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
from openai import OpenAI
import psycopg
from pgvector.psycopg import register_vector
from sentence_transformers import SentenceTransformer

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://chatbot:changeme@localhost:5432/chatbot")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")
LLM_API_KEY  = os.getenv("LLM_API_KEY", "not-needed")
LLM_MODEL    = os.getenv("LLM_MODEL", "qwen3:1.7b")
EMBED_MODEL  = os.getenv("EMBED_MODEL", "BAAI/bge-m3")
TOP_K        = int(os.getenv("TOP_K", "5"))
ENABLE_THINKING = os.getenv("ENABLE_THINKING", "false").lower() == "true"

SYSTEM_PROMPT = """Jesteś asystentem Uniwersytetu Śląskiego w Katowicach.
Odpowiadaj WYŁĄCZNIE na podstawie poniższego kontekstu.
Jeśli w kontekście nie ma odpowiedzi, powiedz:
"Nie mam tej informacji - sprawdź na us.edu.pl lub skontaktuj się z dziekanatem."
Podawaj źródłowy adres URL, z którego pochodzi informacja.
Nie zmyślaj dat, nazwisk, godzin ani kwot. Odpowiadaj po polsku, zwięźle i uprzejmie.

KONTEKST:
{context}
"""


@lru_cache(maxsize=1)
def get_embedder() -> SentenceTransformer:
    print("Ładowanie modelu embeddingów (pierwsze uruchomienie może chwilę potrwać)...", flush=True)
    return SentenceTransformer(EMBED_MODEL)


def embed(text: str) -> list[float]:
    return get_embedder().encode(text, normalize_embeddings=True).tolist()


def retrieve(question: str) -> list[dict]:
    qvec = embed(question)
    with psycopg.connect(DATABASE_URL) as conn:
        register_vector(conn)
        rows = conn.execute(
            "SELECT url, title, content FROM chunks ORDER BY embedding <=> %s::vector LIMIT %s",
            (qvec, TOP_K),
        ).fetchall()
    return [{"url": r[0], "title": r[1], "content": r[2]} for r in rows]


def build_context(chunks: list[dict]) -> str:
    if not chunks:
        return "(brak pasujących informacji w bazie — uruchom najpierw ingestion/ingest.py)"
    return "\n\n".join(f"[Źródło: {c['url']}]\n{c['content']}" for c in chunks)


def main() -> None:
    client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)
    history: list[dict] = []

    print()
    print("=" * 60)
    print("   Bot Uniwersytetu Śląskiego w Katowicach")
    print("   Wpisz 'wyjdź' lub Ctrl+C aby zakończyć")
    print("=" * 60)
    print()

    while True:
        try:
            user_input = input("Ty: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nDo widzenia!")
            sys.exit(0)

        if not user_input:
            continue
        if user_input.lower() in ("wyjdź", "wyjdz", "exit", "quit", "q"):
            print("Do widzenia!")
            sys.exit(0)

        history.append({"role": "user", "content": user_input})

        try:
            chunks = retrieve(user_input)
            context = build_context(chunks)
            messages = [{"role": "system", "content": SYSTEM_PROMPT.format(context=context)}] + history

            print("\nBot: ", end="", flush=True)
            reply_parts: list[str] = []

            stream = client.chat.completions.create(
                model=LLM_MODEL,
                messages=messages,
                temperature=0.3,
                stream=True,
                extra_body={"chat_template_kwargs": {"enable_thinking": ENABLE_THINKING}},
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    print(delta, end="", flush=True)
                    reply_parts.append(delta)

            print("\n")
            history.append({"role": "assistant", "content": "".join(reply_parts)})

        except psycopg.OperationalError as e:
            print(f"\n[Błąd bazy danych: {e}]")
            print("[Upewnij się że Postgres działa i uruchom: docker compose up -d db]\n")
        except Exception as e:
            print(f"\n[Błąd: {e}]\n")


if __name__ == "__main__":
    main()
