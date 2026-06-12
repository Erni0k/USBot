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
   │  build_context()──► string z TOP_K fragmentów          │
   │  LLM API call   ──► Ollama / vLLM (Qwen3, streaming)    │
   ▼                                                        │
 Odpowiedź (streaming stdout) ◄─────────────────────────────┘

 [Oddzielny proces — aktualizacja wiedzy]
 ingestion/ingest.py ──► scrape.crawl()  (BFS, równoległy fetch, 304)
                         chunking (langchain-text-splitters)
                         batch embed ──► UPSERT chunks + pages
```

**Przepływ przy każdym pytaniu:** pytanie (wraz z kontekstem kilku poprzednich tur)
jest embeddowane lokalnie → zapytanie wektorowe do PostgreSQL zwraca `TOP_K`
najbliższych fragmentów → kontekst wbudowywany jest w system prompt → model LLM
generuje odpowiedź strumieniowo.

## 2.2 Crawler — przepływ ingestii

Crawler (`ingestion/scrape.py`) robi **BFS** po dozwolonych domenach, startując od
sitemapy i listy seedów (m.in. dziekanaty, usnet, eduroam). Kluczowe mechanizmy:

- **Równoległe pobieranie** — `ThreadPoolExecutor` (`CRAWL_WORKERS`, domyślnie 6).
- **Requesty warunkowe** — nagłówki `If-None-Match` / `If-Modified-Since`; gdy
  serwer zwróci `304 Not Modified`, strona nie jest pobierana, a BFS kontynuuje z
  linków zapisanych w cache.
- **Deduplikacja po treści** — SHA-256 wyekstrahowanego tekstu; jeśli hash bez
  zmian, pomijany jest kosztowny re-embedding.
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
