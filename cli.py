"""Bot Uniwersytetu Śląskiego — aplikacja terminalowa."""
import os
import sys
from functools import lru_cache

# UTF-8 na Windows (cp1252 nie obsługuje polskich znaków)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Model embeddingów jest cache'owany lokalnie po pierwszym pobraniu — pracuj offline.
# Usuwa ostrzeżenie o HF Hub i zawieszanie przy starcie. Pierwsze pobranie: HF_HUB_OFFLINE=0.
os.environ.setdefault("HF_HUB_OFFLINE", "1")

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
# Fragmenty dalsze niż próg (odległość kosinusowa) są odrzucane; gdy nie zostanie
# żaden, bot odpowiada NO_INFO bez wywołania LLM-a. Wybór progu: docs/testy.md (6.4).
MAX_DISTANCE = float(os.getenv("MAX_DISTANCE", "0.43"))
# Kara do odległości (tylko przy sortowaniu) dla aktualności i wydarzeń starszych niż
# OLD_NEWS_DAYS — żeby np. list rektora z 2020 r. nie wygrywał ze stroną informacyjną.
# 0 = wyłączone. Wpływ na wyniki: docs/testy.md (6.4).
OLD_NEWS_PENALTY = float(os.getenv("OLD_NEWS_PENALTY", "0"))
OLD_NEWS_DAYS = int(os.getenv("OLD_NEWS_DAYS", "730"))
ENABLE_THINKING = os.getenv("ENABLE_THINKING", "false").lower() == "true"

NO_INFO = "Nie mam tej informacji – sprawdź na us.edu.pl lub skontaktuj się z dziekanatem."

SYSTEM_PROMPT = """Jesteś asystentem Uniwersytetu Śląskiego w Katowicach.
Odpowiadaj WYŁĄCZNIE na podstawie poniższego kontekstu.
Jeśli w kontekście nie ma odpowiedzi, powiedz:
"Nie mam tej informacji – sprawdź na us.edu.pl lub skontaktuj się z dziekanatem."
Podawaj źródłowy adres URL, z którego pochodzi informacja.
Nie zmyślaj dat, nazwisk, godzin ani kwot. Odpowiadaj po polsku, zwięźle i uprzejmie.

DOPYTYWANIE O DZIEKANAT:
Każdy wydział ma własny dziekanat z innymi godzinami i kontaktem. Jeśli użytkownik
pyta o dziekanat (godziny otwarcia, kontakt, lokalizację) i NIE wskazał wyraźnie
którego wydziału dotyczy pytanie, NIE zgaduj i NIE podawaj godzin pierwszego
lepszego dziekanatu z kontekstu. Zamiast tego dopytaj, o który wydział chodzi, i
wymień dostępne wydziały:
- Wydział Humanistyczny (WH)
- Wydział Nauk Przyrodniczych (WNP)
- Wydział Nauk Społecznych (WNS)
- Wydział Nauk Ścisłych i Technicznych (WNŚiT)(WNST)
- Wydział Prawa i Administracji (WPiA)
- Wydział Sztuki i Nauk o Edukacji (WSNE)
- Wydział Teologiczny (WTL)
Dopiero gdy użytkownik wskaże wydział, podaj informacje o jego dziekanacie.

KONTEKST:
{context}
"""


@lru_cache(maxsize=1)
def get_embedder() -> SentenceTransformer:
    print("Ładowanie modelu embeddingów (pierwsze uruchomienie może chwilę potrwać)...", flush=True)
    return SentenceTransformer(EMBED_MODEL)


def embed(text: str) -> list[float]:
    return get_embedder().encode(text, normalize_embeddings=True).tolist()


def retrieve(question: str, k: int = TOP_K, max_distance: float | None = MAX_DISTANCE,
             penalty: float = OLD_NEWS_PENALTY) -> list[dict]:
    """Zwraca do k najbliższych fragmentów; z `max_distance` tylko te nie dalsze niż próg.

    Próg sprawdzamy w Pythonie, nie w SQL: przy indeksie HNSW warunek WHERE na
    odległość filtruje dopiero kandydatów z indeksu i może zwrócić za mało wyników.

    Z `penalty` > 0 fragmenty starych aktualności/wydarzeń dostają `score` = dist +
    penalty i według niego są sortowane — pobieramy wtedy więcej kandydatów, żeby
    było z czego wybierać. Próg nadal dotyczy samej odległości (`dist`).
    """
    qvec = embed(question)
    n = max(4 * k, 40) if penalty else k
    with psycopg.connect(DATABASE_URL) as conn:
        register_vector(conn)
        if n > 40:  # domyślny hnsw.ef_search = 40 to też limit liczby wyników
            conn.execute(f"SET hnsw.ef_search = {int(n)}")
        rows = conn.execute(
            "SELECT id, url, title, content, page_type, published_at, "
            "       embedding <=> %s::vector AS dist, "
            "       page_type IN ('news', 'event') "
            "         AND published_at < now() - make_interval(days => %s) AS old_news "
            "FROM chunks ORDER BY dist LIMIT %s",
            (qvec, OLD_NEWS_DAYS, n),
        ).fetchall()
    chunks = [{"id": r[0], "url": r[1], "title": r[2], "content": r[3], "page_type": r[4],
               "published_at": r[5], "dist": r[6], "score": r[6] + (penalty if r[7] else 0.0)}
              for r in rows]
    chunks = sorted(chunks, key=lambda c: c["score"])[:k]
    if max_distance is not None:
        chunks = [c for c in chunks if c["dist"] <= max_distance]
    return chunks


def build_context(chunks: list[dict]) -> str:
    if not chunks:
        return "(brak pasujących informacji w bazie — uruchom najpierw ingestion/ingest.py)"
    return "\n\n".join(f"[Źródło: {c['url']}]\n{c['content']}" for c in chunks)


def build_query(history: list[dict], turns: int = 3) -> str:
    """Buduje zapytanie do wyszukiwarki z kilku ostatnich pytań użytkownika.

    Krótkie follow-upy ("a wnst?", "dziekanatu tam") same w sobie nic nie znaczą
    dla wyszukiwarki wektorowej — doklejamy poprzednie pytania, by zachować temat.
    """
    user_msgs = [m["content"] for m in history if m["role"] == "user"]
    return "\n".join(user_msgs[-turns:])


def retrieve_for_chat(history: list[dict], k: int = TOP_K, max_distance: float = MAX_DISTANCE) -> list[dict]:
    """Szuka zapytaniem z historii (build_query) i samym ostatnim pytaniem, łączy wyniki.

    Historia ratuje follow-upy ("a wnst?" samo jest daleko od wszystkiego), ale po
    zmianie tematu wcześniejsze pytania psują zapytanie — po "przepis na pierogi"
    pytanie o urlop dziekański z historią byłoby odrzucone, choć samo pasuje dobrze.
    Każdy fragment dostaje mniejszą z dwóch odległości; próg stosujemy po złączeniu.
    """
    last = next(m["content"] for m in reversed(history) if m["role"] == "user")
    best: dict[int, dict] = {}
    for query in dict.fromkeys([build_query(history), last]):
        for c in retrieve(query, k=k, max_distance=None):
            if c["id"] not in best or c["score"] < best[c["id"]]["score"]:
                best[c["id"]] = c
    chunks = sorted(best.values(), key=lambda c: c["score"])[:k]
    return [c for c in chunks if c["dist"] <= max_distance]


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
            chunks = retrieve_for_chat(history)
            if not chunks:
                # Nic w bazie nie jest dość bliskie — LLM dostałby same śmieci i zmyślał
                print(f"\nBot: {NO_INFO}\n")
                history.append({"role": "assistant", "content": NO_INFO})
                continue

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
