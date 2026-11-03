# 2. Architektura systemu

## 2.1 Schemat blokowy

System składa się z warstw komunikujących się sekwencyjnie. Aplikacja czatu
(`cli.py`) oraz pipeline ingestii (`ingestion/`) to **dwa niezależne procesy**
korzystające z tej samej bazy.

```text
Użytkownik (terminal)
   │
   ▼
 cli.py ───────────────────────────────────────────────────┐
   │                                                        │
   │  build_query()  ──► sklejenie kilku ostatnich pytań    │
   │  embed(zapytanie) ──► SentenceTransformer              │
   │                       (BAAI/bge-m3, 1024 dim)          │
   │  retrieve()     ──► PostgreSQL + pgvector (operator <=>)│
   │  _select()      ──► TOP_K bez duplikatów, ≤2 na stronę │
   │  build_context()──► string z TOP_K fragmentów          │
   │  LLM API call   ──► Ollama / vLLM (Qwen3, streaming)    │
   ▼                                                        │
 Odpowiedź (streaming stdout) ◄─────────────────────────────┘

 [Oddzielny proces — aktualizacja wiedzy]
 ingestion/ingest.py ──► scrape.crawl()  (BFS, równoległy fetch, 304)
                         chunking po nagłówkach + prefiks tytułu/sekcji
                         pomijanie boilerplate'u (ta sama treść na >20 stronach)
                         batch embed (cache po content_hash) ──► UPSERT chunks + pages
```

**Przepływ przy każdym pytaniu:** pytanie (wraz z kontekstem kilku poprzednich tur)
jest embeddowane lokalnie → zapytanie wektorowe do PostgreSQL zwraca kandydatów →
`_select()` wybiera `TOP_K`: ta sama treść tylko raz, najwyżej `MAX_PER_URL` fragmentów
z jednej strony → fragmenty dalsze niż `MAX_DISTANCE` są odrzucane → kontekst
wbudowywany jest w system prompt → model LLM generuje odpowiedź strumieniowo.

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
| BAAI/bge-m3 | ~2 GB, sentence-transformers | Model embeddingów wielojęzycznych (1024 dim) |
| Qwen3 | domyślnie `qwen3:8b` | Model językowy (LLM) generujący odpowiedzi |
| Ollama | zalecane | Lokalny serwer modelu z API zgodnym z OpenAI |
| vLLM | ≥ 0.9.0, wymaga GPU NVIDIA | Alternatywny serwer modelu dla środowisk z GPU |
| Docker Compose | v2+ | Konteneryzacja bazy danych PostgreSQL |

### Zależności Python (`requirements.txt`)

| Pakiet | Rola |
|---|---|
| `openai` | Klient HTTP do API modelu językowego |
| `sentence-transformers` | Generowanie embeddingów (`BAAI/bge-m3`) |
| `psycopg[binary]` | Sterownik PostgreSQL dla Pythona |
| `pgvector` | Integracja typu `vector` z psycopg |
| `langchain-text-splitters` | Dzielenie tekstu na fragmenty (chunking) |
| `trafilatura` | Ekstrakcja treści ze stron HTML |
| `httpx` | Pobieranie stron i sitemap (crawler) |
| `lxml` | Parsowanie XML sitemap i HTML (ekstrakcja linków) |
| `tqdm` | Pasek postępu ingestii |
| `python-dotenv` | Wczytywanie konfiguracji z pliku `.env` |
