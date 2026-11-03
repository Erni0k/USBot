"""Bot Uniwersytetu Śląskiego — aplikacja terminalowa."""
import os
import sys
from collections import Counter
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
from sentence_transformers import CrossEncoder, SentenceTransformer
import torch

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
# Najwyżej tyle fragmentów z jednej strony (URL-a) w wynikach; 0 = bez limitu.
# Wybór wartości: docs/testy.md (6.4).
MAX_PER_URL = int(os.getenv("MAX_PER_URL", "2"))
# vector = samo wyszukiwanie wektorowe; hybrid = wektorowe + pełnotekstowe połączone
# przez RRF (retrieve_hybrid). Porównanie trybów: docs/testy.md (6.4).
RETRIEVAL_MODE = os.getenv("RETRIEVAL_MODE", "vector").strip().lower()
if RETRIEVAL_MODE not in ("vector", "hybrid"):
    raise ValueError(f"RETRIEVAL_MODE musi być 'vector' albo 'hybrid', jest {RETRIEVAL_MODE!r}")
RRF_K = 60  # stała w RRF: score = Σ 1/(RRF_K + pozycja)
# Reranking: RERANK_CANDIDATES najlepszych fragmentów (z trybu RETRIEVAL_MODE) ocenia
# cross-encoder i zostaje TOP_K z najwyższym wynikiem. Porównanie i czasy: docs/testy.md (6.4).
RERANK = os.getenv("RERANK", "false").lower() == "true"
RERANK_MODEL = os.getenv("RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
RERANK_CANDIDATES = 20
# Przy RERANK=true próg dotyczy wyniku rerankera (0–1, większy = lepszy), nie odległości:
# fragmenty z wynikiem niższym niż MIN_RERANK_SCORE są odrzucane. Wybór: docs/testy.md (6.4).
MIN_RERANK_SCORE = float(os.getenv("MIN_RERANK_SCORE", "0.01"))
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


@lru_cache(maxsize=1)
def get_reranker() -> CrossEncoder:
    print("Ładowanie modelu rerankera (pierwsze uruchomienie może chwilę potrwać)...", flush=True)
    # 512 tokenów jak w FlagEmbedding; najdłuższy fragment (prefiks + 800 znaków) ma mniej.
    # Na GPU fp16: ~3× szybciej niż fp32, wyniki ewaluacji praktycznie te same (docs/testy.md, 6.4).
    fp16 = {"torch_dtype": torch.float16} if torch.cuda.is_available() else None
    return CrossEncoder(RERANK_MODEL, max_length=512, model_kwargs=fp16)


def rerank(queries: list[str], chunks: list[dict]) -> list[dict]:
    """Sortuje fragmenty malejąco według wyniku cross-encodera (`rerank`, 0–1).

    Przy kilku zapytaniach (retrieve_for_chat) fragment dostaje najlepszy z wyników —
    tak jak `score` z dwóch wyszukiwań. Przy remisie decyduje dotychczasowa kolejność.
    """
    if not chunks:
        return []
    pairs = [(q, c["content"]) for q in queries for c in chunks]
    scores = get_reranker().predict(pairs).reshape(len(queries), len(chunks)).max(axis=0)
    for c, s in zip(chunks, scores):
        c["rerank"] = float(s)
    return sorted(chunks, key=lambda c: (-c["rerank"], *_rank(c)))


# Słowa pytań, które nic nie mówią wyszukiwarce pełnotekstowej (po unaccent). Konfiguracja
# 'simple' nie ma stop-słów, a ts_rank_cd nie zna IDF — bez tej listy „jak”, „się”, „do”
# liczyłyby się tak samo jak „USOS”.
FTS_STOPWORDS = frozenset("""
    a aby albo ale bo by byc czy co czym czego dla do gdy gdzie i ich ile jak jaka jaki
    jakie jakich jakim jakiej jako jest juz kiedy kto ktora ktore ktory ktorych lub ma mam
    mi mnie moge mozna moj moja moje na nad nie o od oraz po pod przez przy sa sie sobie
    ta tak te tego tej ten to tu u w we z za ze
    an and are can does how in is my of on or the what when where which who
""".split())

_COLUMNS = (
    "id, url, title, content, page_type, published_at, body_hash, "
    "embedding <=> %(qvec)s::vector AS dist, "
    "page_type IN ('news', 'event') "
    "  AND published_at < now() - make_interval(days => %(old_days)s) AS old_news"
)


def _to_chunk(r: tuple, penalty: float) -> dict:
    return {"id": r[0], "url": r[1], "title": r[2], "content": r[3], "page_type": r[4],
            "published_at": r[5], "body_hash": r[6], "dist": r[7],
            "score": r[7] + (penalty if r[8] else 0.0)}


def _vector_candidates(conn: psycopg.Connection, qvec: list[float], n: int,
                       penalty: float) -> list[dict]:
    """n najbliższych fragmentów z indeksu HNSW, posortowanych według `score`."""
    if n > 40:  # domyślny hnsw.ef_search = 40 to też limit liczby wyników
        conn.execute(f"SET hnsw.ef_search = {int(n)}")
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM chunks ORDER BY dist LIMIT %(n)s",
        {"qvec": qvec, "old_days": OLD_NEWS_DAYS, "n": n},
    ).fetchall()
    return sorted((_to_chunk(r, penalty) for r in rows), key=_rank)


def _fulltext_query(conn: psycopg.Connection, question: str) -> str | None:
    """tsquery z pytania: jego leksemy połączone OR, bez FTS_STOPWORDS; None, gdy pusto.

    Leksemy liczy Postgres tą samą konfiguracją co chunks.content_tsv ('simple' +
    unaccent), więc zgadzają się z dokumentami. OR zamiast AND (plainto_tsquery,
    websearch_to_tsquery): bez stemmingu „dziekanatu” w pytaniu nie pasuje do
    „dziekanat” w treści, a AND wymagałoby każdego słowa w dokładnie tej formie.
    Porównanie wariantów: docs/testy.md (6.4).
    """
    lexemes = conn.execute(
        "SELECT unnest(tsvector_to_array(to_tsvector('simple', immutable_unaccent(%s))))",
        (question,),
    ).fetchall()
    terms = [lex for (lex,) in lexemes if lex not in FTS_STOPWORDS]
    if not terms:
        return None
    return " | ".join("'" + t.replace("\\", "\\\\").replace("'", "''") + "'" for t in terms)


def _fulltext_candidates(conn: psycopg.Connection, question: str, qvec: list[float], n: int,
                         penalty: float) -> list[dict]:
    """n fragmentów najlepiej pasujących słowami (ts_rank_cd), w kolejności rankingu.

    Odległość wektorową (`dist`) liczymy tu osobno dla każdego fragmentu — potrzebuje
    jej próg MAX_DISTANCE, także dla fragmentów, których nie znalazło HNSW. Przy remisie
    ts_rank_cd (częsty: fragmenty z jednym wystąpieniem słowa) wygrywa bliższy wektorowo.
    """
    query = _fulltext_query(conn, question)
    if query is None:
        return []
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM chunks, CAST(%(q)s AS tsquery) AS q "
        "WHERE content_tsv @@ q ORDER BY ts_rank_cd(content_tsv, q) DESC, dist LIMIT %(n)s",
        {"qvec": qvec, "old_days": OLD_NEWS_DAYS, "q": query, "n": n},
    ).fetchall()
    return [_to_chunk(r, penalty) for r in rows]


def _rrf(*rankings: list[dict]) -> list[dict]:
    """Reciprocal Rank Fusion: score fragmentu = −Σ 1/(RRF_K + pozycja) po listach.

    RRF, a nie ważona suma wyników, bo wyniki obu wyszukiwarek mają różne skale:
    odległość kosinusowa to 0–2 (w praktyce 0.2–0.6, mniejsza = lepsza), a ts_rank_cd
    jest nieograniczony (zależy od liczby wystąpień słów i długości fragmentu, większy =
    lepszy) i jego rozkład zmienia się z każdym pytaniem. Wagi sumy trzeba by stroić
    pod te rozkłady, a i tak nie byłyby porównywalne między pytaniami. RRF patrzy tylko
    na pozycje, więc nie wymaga normalizacji ani strojenia; RRF_K = 60 tłumi różnice
    między czołowymi pozycjami, przez co fragment wysoko na obu listach wygrywa z
    fragmentem pierwszym tylko na jednej (Cormack i in., 2009).

    `score` jest ujemny, bo _rank() i retrieve_for_chat() sortują rosnąco.
    """
    fused: dict[int, dict] = {}
    for ranking in rankings:
        for pos, c in enumerate(ranking, 1):
            f = fused.setdefault(c["id"], {**c, "score": 0.0})
            f["score"] -= 1 / (RRF_K + pos)
    return sorted(fused.values(), key=_rank)


def _candidates(question: str, k: int, penalty: float = OLD_NEWS_PENALTY,
                mode: str = RETRIEVAL_MODE) -> list[dict]:
    """Kandydaci z zapasem (5·k, min. 40), posortowani według `score`.

    Zapas jest potrzebny, bo _select() pomija duplikaty i nadmiar fragmentów z jednej
    strony, a kara za stare aktualności zmienia kolejność.
    W trybie hybrid z zapasem pobieramy obie listy, a `score` to wynik _rrf(); kara za
    stare aktualności działa wtedy na kolejność listy wektorowej przed fuzją.
    """
    qvec = embed(question)
    n = max(5 * k, 40)
    with psycopg.connect(DATABASE_URL) as conn:
        register_vector(conn)
        chunks = _vector_candidates(conn, qvec, n, penalty)
        if mode == "hybrid":
            chunks = _rrf(chunks, _fulltext_candidates(conn, question, qvec, n, penalty))
    return chunks


def _rank(c: dict) -> tuple:
    """Kolejność wyników: `score`, przy remisie bliższy wektorowo, potem krótszy URL.

    Remisy odległości to kopie tej samej treści (ten sam embedding), np. strona portalu
    i jej nieprzetłumaczona kopia pod /student/en/ albo /student i /student/pl. _select()
    zostawia pierwszą kopię — krótszy adres to zwykle oryginał, lepszy do cytowania.
    W trybie hybrid remis RRF jest częsty (ta sama pozycja na różnych listach).
    """
    return c["score"], c["dist"], len(c["url"]), c["url"]


def _select(chunks: list[dict], k: int, max_per_url: int = MAX_PER_URL) -> list[dict]:
    """Pierwsze k fragmentów z posortowanej listy, bez powtórzeń treści.

    Ta sama treść (`body_hash` — bez prefiksu z tytułem) z kilku stron zostaje raz,
    z najbliższego fragmentu. Z `max_per_url` jedna strona dostaje najwyżej tyle
    miejsc, żeby długa strona nie zajęła całego kontekstu. Zwolnione miejsca
    zajmują kolejni kandydaci.
    """
    seen: set[str] = set()
    per_url: Counter[str] = Counter()
    out = []
    for c in chunks:
        key = c["body_hash"] or c["content"]
        if key in seen or (max_per_url and per_url[c["url"]] >= max_per_url):
            continue
        seen.add(key)
        per_url[c["url"]] += 1
        out.append(c)
        if len(out) == k:
            break
    return out


def retrieve(question: str, k: int = TOP_K, max_distance: float | None = MAX_DISTANCE,
             penalty: float = OLD_NEWS_PENALTY, max_per_url: int = MAX_PER_URL,
             mode: str = RETRIEVAL_MODE, use_rerank: bool = RERANK,
             min_score: float | None = MIN_RERANK_SCORE) -> list[dict]:
    """Zwraca do k najlepszych fragmentów, bez tych poniżej progu.

    `mode` (domyślnie RETRIEVAL_MODE): "vector" — według odległości wektorowej,
    "hybrid" — patrz retrieve_hybrid(). Z `use_rerank` z RERANK_CANDIDATES najlepszych
    zostaje k z najwyższym wynikiem rerankera (rerank()).

    Próg (_cutoff): bez rerankingu `max_distance` na odległości, z rerankingiem
    `min_score` na wyniku rerankera; None = bez progu. Sprawdzamy go w Pythonie, nie w
    SQL: przy indeksie HNSW warunek WHERE na odległość filtruje dopiero kandydatów z
    indeksu i może zwrócić za mało wyników.

    Z `penalty` > 0 fragmenty starych aktualności/wydarzeń dostają `score` = dist +
    penalty i według niego są sortowane. Próg nadal dotyczy samej odległości (`dist`).
    Duplikaty treści i limit fragmentów z jednej strony: _select() — przed
    rerankingiem, więc wynik rerankera ich nie przywraca.
    """
    n = max(k, RERANK_CANDIDATES) if use_rerank else k
    chunks = _select(_candidates(question, n, penalty, mode), n, max_per_url)
    if use_rerank:
        chunks = rerank([question], chunks)[:k]
    return _cutoff(chunks, max_distance, min_score, use_rerank)


def _cutoff(chunks: list[dict], max_distance: float | None, min_score: float | None,
            use_rerank: bool) -> list[dict]:
    """Próg na jednej skali: z rerankingiem `min_score` (wynik rerankera), bez — `max_distance`.

    Z rerankingiem próg odległości nie działa: odcinałby fragmenty dalekie wektorowo,
    które reranker ocenił wysoko (docs/testy.md, 6.4). None = bez progu.
    """
    if use_rerank:
        return chunks if min_score is None else [c for c in chunks if c["rerank"] >= min_score]
    return chunks if max_distance is None else [c for c in chunks if c["dist"] <= max_distance]


def retrieve_hybrid(question: str, k: int = TOP_K, max_distance: float | None = MAX_DISTANCE,
                    penalty: float = OLD_NEWS_PENALTY,
                    max_per_url: int = MAX_PER_URL) -> list[dict]:
    """Wyszukiwanie hybrydowe: top-N wektorowo i top-N pełnotekstowo, złączone przez RRF.

    Embeddingi słabo radzą sobie ze skrótami i nazwami własnymi (USOS, IRK, ELS,
    WNŚiT, CINiBA) — część pełnotekstowa znajduje je po dokładnym słowie. Kolejność
    według _rrf(); każdy wynik ma `dist` (odległość wektorową) i próg `max_distance`
    działa jak w retrieve(), także dla fragmentów znalezionych tylko pełnotekstowo.
    """
    return retrieve(question, k, max_distance, penalty, max_per_url, mode="hybrid")


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


def retrieve_for_chat(history: list[dict], k: int = TOP_K, max_distance: float = MAX_DISTANCE,
                      mode: str = RETRIEVAL_MODE, use_rerank: bool = RERANK,
                      min_score: float = MIN_RERANK_SCORE) -> list[dict]:
    """Szuka zapytaniem z historii (build_query) i samym ostatnim pytaniem, łączy wyniki.

    Historia ratuje follow-upy ("a wnst?" samo jest daleko od wszystkiego), ale po
    zmianie tematu wcześniejsze pytania psują zapytanie — po "przepis na pierogi"
    pytanie o urlop dziekański z historią byłoby odrzucone, choć samo pasuje dobrze.
    Każdy fragment dostaje lepszy z dwóch wyników (`score`: odległość albo −RRF);
    duplikaty, limit na stronę (_select) i próg stosujemy po złączeniu. Reranker
    ocenia złączone fragmenty oboma zapytaniami i też bierze lepszy wynik.
    """
    last = next(m["content"] for m in reversed(history) if m["role"] == "user")
    queries = list(dict.fromkeys([build_query(history), last]))
    n = max(k, RERANK_CANDIDATES) if use_rerank else k
    best: dict[int, dict] = {}
    for query in queries:
        for c in _candidates(query, n, mode=mode):
            if c["id"] not in best or c["score"] < best[c["id"]]["score"]:
                best[c["id"]] = c
    chunks = _select(sorted(best.values(), key=_rank), n)
    if use_rerank:
        chunks = rerank(queries, chunks)[:k]
    return _cutoff(chunks, max_distance, min_score, use_rerank)


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
