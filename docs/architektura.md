# 2. Architektura systemu

## 2.1 Schemat blokowy

System składa się z warstw komunikujących się sekwencyjnie. Aplikacja czatu —
terminalowa (`cli.py`) albo API HTTP dla strony WWW (`usbot/api/`) — oraz pipeline
ingestii (`ingestion/`) to **niezależne procesy** korzystające z tej samej bazy i tych
samych adapterów z pakietu `usbot/`.

```text
Użytkownik (terminal)                       Przeglądarka (strona WWW)
   │                                                   │ POST /api/chat {message, history}
   ▼                                                   ▼
 cli.py  (input/print, historia w pamięci)   usbot/api/ (FastAPI: walidacja, SSE, lifespan)
   │                                                   │ wątek + kolejka → event: … data: {…}
   │  ChatService.stream(pytanie, historia)  ◄─────────┘ (historia od klienta, N ostatnich tur)
   ▼
 usbot/core/chat.py — ChatService ───────────────────────────────────────────┐
   │  search_queries()  ──► build_query() (3 ostatnie pytania) + samo pytanie  │
   │  Retriever.search() ──────────────────────────────┐ (port)               │
   │  cutoff()          ──► próg MAX_DISTANCE / MIN_RERANK_SCORE               │
   │     └─ nic nie zostało ──► {"type": "refused"} (bez LLM-a)                │
   │  needs_faculty()   ──► dziekanat bez wydziału ──► {"type": "clarify"}     │
   │  {"type": "sources"} — strony z wyników wyszukiwania                      │
   │  build_system_prompt() ──► prompt z kontekstem                            │
   │  ChatLLM.stream() ───────────────────────────┐ (port)                     │
   │  {"type": "token"}… {"type": "done"}         │                            │
   ▼                                              │                            │
 adaptery (usbot/adapters/)                       │                            │
   pgvector.py  PgvectorRetriever ◄───────────────┼──── Retriever              │
     embed(zapytanie) ──► Embedder (embeddings.py: bge-m3, 1024 dim)           │
     SQL: <=> (HNSW)  [hybrid: + content_tsv @@, rrf()]                        │
     merge_best() + select() z usbot/core/ranking.py                           │
   [RERANK] RerankingRetriever (core/ranking.py) ──► Reranker                  │
            (reranker.py: CrossEncoder bge-reranker-v2-m3, 20 → TOP_K)         │
   llm.py       OpenAICompatibleLLM ◄─────────────┴──── ChatLLM                │
                (Ollama / vLLM, Qwen3, streaming)                              │
                                                                               │
 Odpowiedź (streaming stdout) ◄────────────────────────────────────────────────┘

 [Oddzielne procesy — te same adaptery i ustawienia]
 ingestion/ingest.py ──► scrape.crawl()  (BFS, równoległy fetch, 304)
                         chunking po nagłówkach + prefiks tytułu/sekcji
                         pomijanie boilerplate'u (ta sama treść na >20 stronach)
                         Embedder.embed_batch (cache po content_hash) ──► UPSERT chunks + pages
 evaluation/evaluate.py ──► build_retriever() ──► Retriever.search() bez progu ──► metryki
```

**Przepływ przy każdym pytaniu:** pytanie (wraz z kontekstem kilku poprzednich tur)
jest embeddowane lokalnie → zapytanie wektorowe do PostgreSQL zwraca kandydatów (w trybie
`hybrid` także zapytanie pełnotekstowe; obie listy łączy RRF, a każdy kandydat ma
odległość wektorową) →
`select()` wybiera `TOP_K`: ta sama treść tylko raz, najwyżej `MAX_PER_URL` fragmentów
z jednej strony (z `RERANK=true`: 20 kandydatów ocenia cross-encoder i zostaje `TOP_K`
najlepszych) → fragmenty dalsze niż `MAX_DISTANCE` (z rerankingiem: z wynikiem poniżej
`MIN_RERANK_SCORE`) są odrzucane → przy pytaniu o dziekanat bez wydziału bot dopytuje →
kontekst wbudowywany jest w system prompt → model LLM generuje odpowiedź strumieniowo.

### Warstwy (architektura heksagonalna)

Kod aplikacji jest w pakiecie `usbot/`, podzielonym według kierunku zależności:
rdzeń nie zna technologii, adaptery znają rdzeń, a składa je jedno miejsce.

| Warstwa | Pliki | Odpowiedzialność | Importuje |
|---|---|---|---|
| Rdzeń | `usbot/core/` | `chat.py`: `ChatService` — tura rozmowy jako strumień zdarzeń, próg odcięcia (`cutoff()`); `prompts.py`: prompt systemowy, `build_context()`, `build_query()`; `faculties.py`: dopytywanie o wydział; `ranking.py`: kolejność, `select()`, `rrf()`, `merge_best()`, reranking (`RerankingRetriever`); `models.py`: `Chunk`, `Message` | tylko bibliotekę standardową i `usbot.ports` |
| Porty | `usbot/ports.py` | Protokoły `Embedder`, `Retriever`, `Reranker`, `ChatLLM` — jedyny sposób, w jaki rdzeń korzysta z otoczenia | `usbot.core.models` |
| Adaptery | `usbot/adapters/` | `embeddings.py` (SentenceTransformer, bge-m3), `reranker.py` (CrossEncoder), `pgvector.py` (SQL: wektorowe i pełnotekstowe), `llm.py` (API zgodne z OpenAI) | rdzeń, porty, biblioteki zewnętrzne |
| Konfiguracja | `usbot/config.py` | `Settings` (pydantic-settings): zmienne środowiskowe i `.env`, te same nazwy co wcześniej | pydantic |
| Składanie | `usbot/bootstrap.py` | `build_retriever()`, `build_chat_service()`… — adaptery według ustawień | wszystko |
| Wejście | `cli.py`, `usbot/api/`, `evaluation/evaluate.py`, `ingestion/ingest.py` | terminal / HTTP (SSE) / metryki / crawl; logikę biorą z `usbot` | `usbot.bootstrap`, `usbot.config` (API także `usbot.core` — typy zdarzeń, lista wydziałów) |

Dzięki temu `ChatService` testuje się na atrapach portów, bez bazy, modelu i serwera
LLM (`tests/unit/`), a adapter pgvector — na prawdziwym Postgresie w kontenerze
(`tests/integration/`, testcontainers). API HTTP (`usbot/api/`) jest kolejnym adapterem
wejścia obok `cli.py` i nie zawiera logiki rozmowy — opis niżej.

Ingestia korzysta z `Settings`, adaptera embeddingów (`embed_batch`) i połączenia z
`usbot/adapters/pgvector.py`; zapis do `chunks`/`pages` (SQL ingestii) został w
`ingestion/ingest.py`, bo używa go tylko ten proces.

### Strumień zdarzeń `ChatService.stream()`

`ChatService.stream(pytanie, historia)` nie trzyma stanu — historię rozmowy prowadzi
klient. Każde zdarzenie to słownik gotowy do serializacji JSON:

| Zdarzenie | Kiedy | Pola |
|---|---|---|
| `sources` | fragmenty przeszły próg, zaraz zostanie wywołany LLM | `sources`: strony z wyników wyszukiwania (każdy URL raz, z najlepszego fragmentu): `url`, `title`, `page_type`, `published_at`, `dist`, `rerank` |
| `token` | kolejny kawałek odpowiedzi LLM-a | `text` |
| `refused` | żaden fragment nie przeszedł progu — bez wywołania LLM-a | `message` (stały tekst „Nie mam tej informacji…”) |
| `clarify` | pytanie o dziekanat, a w 3 ostatnich pytaniach nie padł wydział — bez wywołania LLM-a | `message`, `options`: `[{"code": "WNP", "name": "Wydział Nauk Przyrodniczych"}, …]` |
| `done` | zawsze na końcu | `answer`: pełny tekst odpowiedzi, który klient dopisuje do historii |

Źródła są danymi z retrievalu, a nie tekstem wygenerowanym przez LLM — interfejs może
je pokazać jako linki niezależnie od tego, czy model podał URL w treści. `cli.py`
wypisuje odpowiedź tak jak przed wydzieleniem warstw (źródeł osobno nie pokazuje).

### API HTTP (`usbot/api/`)

| Plik | Rola |
|---|---|
| `main.py` | `create_app()`: endpointy `POST /api/chat`, `GET /api/health`, `GET /api/faculties`, CORS, lifespan; `app` dla `uvicorn usbot.api.main:app` |
| `resources.py` | `build_resources()`: przy starcie ładuje model embeddingów (i reranker), otwiera pulę `psycopg_pool`, tworzy klienta LLM-a i `ChatService`; sprawdzenia dla `/api/health` |
| `sse.py` | `stream_events()`: zdarzenia `ChatService` → Server-Sent Events; błędy adapterów → zdarzenie `error` z kodem |
| `schemas.py` | modele żądań i odpowiedzi (pydantic) i limity wielkości żądania |

Serwer jest bezstanowy: historię przysyła klient, a `ChatService` (parametr
`max_history_turns`, zmienna `API_HISTORY_TURNS`) bierze z niej tylko ostatnie tury —
przycinanie jest w rdzeniu (`recent_turns()`), API przekazuje historię bez zmian.
`ChatService.stream()` jest zwykłym generatorem, a adaptery blokują (SQL, model na GPU,
HTTP do LLM-a), więc `stream_events()` uruchamia go w wątku i przekazuje zdarzenia do
pętli asyncio przez kolejkę. Po rozłączeniu klienta ustawia flagę zatrzymania: wątek
kończy pętlę przy najbliższym zdarzeniu i zamyka generator, co zamyka strumień HTTP do
LLM-a (`OpenAICompatibleLLM.stream()` zamyka go w `finally`) — serwer modelu przestaje
generować. Modele są współdzielone przez wątki i chronione blokadą (embedding pytania
trwa ~20 ms).

Limity czasu: `LLM_TIMEOUT` (połączenie z LLM-em i każdy kolejny fragment odpowiedzi; API
nie ponawia zapytań), `DB_TIMEOUT` (połączenie z puli, nawiązanie połączenia,
`statement_timeout`), `HEALTH_TIMEOUT` (każde sprawdzenie w `/api/health`). Połączenia z
puli są wielokrotnego użytku, więc `PgvectorRetriever` ustawia `hnsw.ef_search` przez
`SET LOCAL` — tylko w transakcji jednego wyszukiwania. Kontrakt: [API](api.md) (3.1).

**Dopytywanie o wydział** (`usbot/core/faculties.py`) rozpoznaje „dziekanat” w ostatnim
pytaniu (po zamianie na małe litery i usunięciu polskich znaków; „dziekan” i „urlop
dziekański” się nie liczą) i szuka wydziału — skrótu (`WNP`, `WNŚiT`/`WNST`…) albo
rdzenia nazwy („przyrodnicz”, „teolog”…) — w 3 ostatnich pytaniach użytkownika.
Wcześniej dopytywał wyłącznie LLM na podstawie instrukcji w prompcie; instrukcja
została w prompcie na sformułowania, których reguła nie rozpozna. Próg ma
pierwszeństwo: pytanie spoza zakresu dostaje `refused`, nie `clarify`.

Na dopytanie użytkownik często odpowiada samym skrótem („WNP”), który embedding słabo
rozumie. Dlatego `ChatService.retrieve()` przepuszcza zapytania przez `expand_faculties()`:
przed każdym skrótem wydziału (całe słowo, dowolna wielkość liter, z polskimi znakami lub
bez) dopisuje pełną nazwę — „WNP” → „Wydział Nauk Przyrodniczych (WNP)”. Dotyczy to
każdego zapytania czatu ze skrótem, nie tylko po `clarify`; `evaluate.py` szuka
retrieverem bezpośrednio, więc jej wyniki się nie zmieniają. Pomiar: [Testy](testy.md) (6.4).

## 2.2 Crawler — przepływ ingestii

Crawler (`ingestion/scrape.py`) robi **BFS** po dozwolonych domenach, startując od
priorytetowych seedów i sitemapy. Kluczowe mechanizmy:

- **Kolejka priorytetowa** — najpierw pobierane są strony z `FALLBACK_URLS` (usnet,
  eduroam, dziekanaty, strony ogólne) oraz każda znaleziona podstrona z
  `PRIORITY_PREFIXES` (portal studenta `us.edu.pl/student/`, usnet, eduroam), dopiero
  potem URL-e z sitemapy i reszta BFS. Portalu studenta nie ma w `sitemap.xml` —
  w zwykłej kolejce jego podstrony trafiały za ~10 tys. URL-i z sitemapy i nie
  mieściły się w `CRAWL_LIMIT`.
- **Filtrowanie linków** — `_SKIP_PATTERNS` odrzuca galerie zdjęć (`/multimedia/`),
  API i pliki WordPressa, RSS, kategorie i tagi. Ingestia przy starcie usuwa z bazy
  strony wykluczone przez obecne reguły. Strony załączników WordPressa (np.
  `…/sale-wykladowe/dsc00582/`) nie mają w adresie `/attachment/`, więc crawler
  rozpoznaje je po klasie `attachment` w `<body>` i pomija.
- **Ekstrakcja tekstu** — przed trafilaturą usuwany jest ukryty baner cookies
  (Complianz), nagłówek z menu i stopka; jeśli strona ma `<main>`, analizowana jest
  tylko jego zawartość. Gdy trafilatura zwróci mniej niż połowę tekstu `<main>`
  (strony-kafelki portalu studenta), brany jest cały `<main>`.
- **Filtrowanie sitemapy** — pomijane są bezwartościowe pod-sitemapy (`attachment`,
  `multimedia`, tagi, kategorie itp. — ~73% sitemapy us.edu.pl), a `page`,
  `komunikaty` i `jednostki` mają pierwszeństwo przed `post`/`event`. Sitemapa
  schodzi z ~45 800 do ~10 700 wartościowych URL-i.
- **Równoległe pobieranie** — `ThreadPoolExecutor` (`CRAWL_WORKERS`, domyślnie 6)
  ze wspólnym klientem HTTP (ponowne użycie połączeń). W locie jest stale do
  `CRAWL_WORKERS` żądań — gdy jedno się skończy, od razu wysyłane jest następne.
  Wcześniej strony szły paczkami i jedna wolna odpowiedź `usnet.us.edu.pl` (~11 s,
  `us.edu.pl` ~0.2 s) blokowała całą paczkę. Grzeczność wobec serwerów: najwyżej
  2 równoległe żądania do usnet (`HOST_LIMITS`) i co najmniej 0.1 s między startami
  żądań do tej samej domeny (`HOST_DELAY`). Kolejki są osobne dla każdej domeny,
  więc czekające URL-e usnet nie wstrzymują reszty; zwykła kolejka jest używana
  w tym czasie tylko wtedy, gdy budżet `CRAWL_LIMIT` wystarczy jeszcze na wszystkie
  czekające URL-e priorytetowe.
- **Requesty warunkowe** — nagłówki `If-None-Match` / `If-Modified-Since`; gdy
  serwer zwróci `304 Not Modified`, strona nie jest pobierana, a BFS kontynuuje z
  linków zapisanych w cache.
- **Struktura i metadane** — ekstrakcja zachowuje nagłówki h1–h6 (markdown z
  trafilatury, a przy fallbacku na `<main>` — własny), zapisuje tytuł strony (`<title>`
  bez nazwy serwisu), typ (`student`, `wydzial`, `static`, `news`, `event`; aktualności
  po klasie `<body>` WordPressa) i datę publikacji.
- **Chunking po sekcjach** — tekst dzielony po nagłówkach, długie sekcje dalej przez
  `RecursiveCharacterTextSplitter(800, 120)`. Każdy fragment zaczyna się prefiksem
  `<tytuł strony> > <nagłówek sekcji>`, z którego liczony jest też embedding.
- **Deduplikacja po treści** — SHA-256 wyekstrahowanego tekstu i metadanych; jeśli hash
  bez zmian, pomijany jest kosztowny re-embedding. Zmiana chunkingu nie zmienia hasha,
  dlatego `pages.chunker_version` pamięta wersję podziału (`CHUNKER_VERSION`), a strony
  z inną wersją są pobierane i embedowane od nowa.
- **Cache embeddingów** — każdy fragment ma `content_hash` (treść z prefiksem). Przed
  liczeniem embeddingów ingestia bierze gotowe wektory dla treści, które już są w
  `chunks`: ten sam fragment na innej stronie i niezmienione sekcje edytowanej strony
  nie są embedowane ponownie.
- **Boilerplate** — `body_hash` (treść bez prefiksu) rozpoznaje ten sam tekst na różnych
  stronach. Treść obecna na więcej niż `BOILERPLATE_URLS` (20) stronach — przyciski
  „Udostępnij”, puste nagłówki — nie trafia do `chunks`. Liczba stron liczona jest po
  `pages.chunk_hashes`, czyli hashach wszystkich fragmentów strony, także pominiętych.
- **Aktualizacja per-URL** — zamiast `TRUNCATE` całej tabeli, zmienione strony mają
  podmieniane fragmenty (`DELETE` + `INSERT`), a stan zapisywany w tabeli `pages`.

Dozwolone domeny: `us.edu.pl`, `usnet.us.edu.pl`, `eduroam.us.edu.pl`. Pomijane są
pliki binarne (PDF, obrazy, archiwa, dokumenty Office, audio/wideo).

## 2.3 Opis technologii

| Technologia | Wersja / szczegóły | Rola w projekcie |
|---|---|---|
| Python | 3.11+ | Język aplikacji i skryptów ingestii |
| PostgreSQL | 16 (`pgvector/pgvector:pg16`) | Baza danych wektorowych i tekstowych |
| pgvector | rozszerzenie PostgreSQL | Przechowywanie i wyszukiwanie wektorów |
| unaccent + FTS | rozszerzenie i wbudowane wyszukiwanie PostgreSQL | Wyszukiwanie pełnotekstowe (`tsvector`, GIN) w trybie `hybrid` |
| BAAI/bge-m3 | ~2 GB, sentence-transformers | Model embeddingów wielojęzycznych (1024 dim) |
| BAAI/bge-reranker-v2-m3 | ~2.3 GB, sentence-transformers `CrossEncoder`, fp16 na GPU | Opcjonalny reranking kandydatów (`RERANK=true`) |
| Qwen3 | domyślnie `qwen3:8b` | Model językowy (LLM) generujący odpowiedzi |
| Ollama | zalecane | Lokalny serwer modelu z API zgodnym z OpenAI |
| vLLM | ≥ 0.9.0, wymaga GPU NVIDIA | Alternatywny serwer modelu dla środowisk z GPU |
| Docker Compose | v2+ | Konteneryzacja bazy danych PostgreSQL |
| FastAPI + uvicorn | ASGI, Server-Sent Events | API HTTP dla strony WWW (`usbot/api/`) |
| psycopg_pool | pula połączeń psycopg 3 | Połączenia z bazą współdzielone przez żądania API |

### Zależności Python (`requirements.txt`)

| Pakiet | Rola |
|---|---|
| `openai` | Klient HTTP do API modelu językowego |
| `sentence-transformers` | Generowanie embeddingów (`BAAI/bge-m3`) i reranking (`CrossEncoder`, `BAAI/bge-reranker-v2-m3`) |
| `psycopg[binary]` | Sterownik PostgreSQL dla Pythona |
| `pgvector` | Integracja typu `vector` z psycopg |
| `langchain-text-splitters` | Dzielenie tekstu na fragmenty (chunking) |
| `trafilatura` | Ekstrakcja treści ze stron HTML |
| `httpx` | Pobieranie stron i sitemap (crawler) |
| `lxml` | Parsowanie XML sitemap i HTML (ekstrakcja linków) |
| `tqdm` | Pasek postępu ingestii |
| `pydantic-settings` | Ustawienia (`usbot/config.py`) ze zmiennych środowiskowych i pliku `.env` |
| `fastapi` | API HTTP (`usbot/api/`): endpointy, walidacja żądań, strumień SSE |
| `uvicorn` | Serwer ASGI uruchamiający API |
| `psycopg[pool]` (`psycopg-pool`) | Pula połączeń z bazą dla API |
| `python-dotenv` | Parsowanie pliku `.env` (używane przez pydantic-settings) |

### Zależności testów (`requirements-dev.txt`)

| Pakiet | Rola |
|---|---|
| `pytest` | Testy jednostkowe i integracyjne (`tests/`) |
| `testcontainers[postgres]` | Kontener `pgvector/pgvector:pg16` dla testów integracyjnych |
| `httpx` (z `requirements.txt`) | `fastapi.testclient.TestClient` w testach API |
