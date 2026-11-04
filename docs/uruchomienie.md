# 4. Instrukcja uruchomienia

## 4.1 Wymagania wstępne

| Narzędzie | Minimalna wersja | Cel |
|---|---|---|
| Python | 3.11 | Uruchamianie aplikacji i skryptów |
| pip | 23+ | Instalacja zależności Python |
| Docker | 24+ | Kontener bazy danych PostgreSQL |
| Docker Compose | v2 | Orkiestracja kontenerów |
| Ollama | dowolna (zalecane) | Lokalny serwer LLM |
| Git | 2+ | Klonowanie repozytorium |
| ~2 GB miejsca | — | Model embeddingów `BAAI/bge-m3` |
| ~5 GB miejsca | — | Model LLM `qwen3:8b` (Ollama) |
| ~2.3 GB miejsca (opcjonalnie) | — | Reranker `BAAI/bge-reranker-v2-m3` (`RERANK=true`) |

!!! note "Bez GPU lub z GPU"
    Ollama działa też na CPU, ale model `qwen3:8b` jest wtedy wolny — rozważ
    `qwen3:4b` lub `qwen3:1.7b`. Z kartą NVIDIA odpowiedzi są szybkie.
    Alternatywą jest vLLM (wymaga `nvidia-container-toolkit`).

!!! note "Reranking (opcjonalnie, `RERANK=true`)"
    Reranker wymaga PyTorch z CUDA: na RTX 3080 ocena 20 fragmentów trwa ~90 ms (fp16),
    na CPU ~6 s na pytanie. Model pobierz raz, bo `cli.py` działa offline:
    `HF_HUB_OFFLINE=0 python -c "from sentence_transformers import CrossEncoder; CrossEncoder('BAAI/bge-reranker-v2-m3')"`.
    Przy `RERANK=true` próg odcięcia to `MIN_RERANK_SCORE` (domyślnie 0.01), a
    `MAX_DISTANCE` nie działa. Szczegóły: [Testy](testy.md) (6.4).

## 4.2 Instalacja i uruchomienie

### Krok 1 — Klonowanie repozytorium

```bash
git clone https://github.com/Erni0k/USBot.git
cd USBot
```

### Krok 2 — Konfiguracja środowiska

```bash
cp .env.example .env
# Otwórz .env i uzupełnij wartości (szczególnie LLM_BASE_URL i LLM_MODEL)
```

### Krok 3 — Instalacja zależności Python

```bash
pip install -r requirements.txt
```

### Krok 4 — Uruchomienie modelu językowego (Ollama, zalecane)

```bash
ollama pull qwen3:8b
ollama serve
# W .env ustaw:
#   LLM_BASE_URL=http://127.0.0.1:11434/v1
#   LLM_MODEL=qwen3:8b
```

### Krok 5 — Uruchomienie bazy danych

```bash
docker compose up -d db
```

### Krok 6 — Ingestia danych

```bash
python ingestion/ingest.py
# Pierwsze uruchomienie pobiera model embeddingów (~2 GB) z Hugging Face.
# Kolejne uruchomienia są inkrementalne — pobierają tylko zmienione strony.
```

### Krok 7 — Uruchomienie chatbota

```bash
python cli.py
```

Wpisz pytanie i naciśnij Enter. Wyjście: `wyjdź` lub `Ctrl+C`.

!!! warning "Kolejność startu"
    Zanim odpalisz bota, upewnij się, że **Ollama działa** (`ollama serve`) oraz
    **baza jest wstała** (`docker compose up -d db`).

### Krok 7b — API HTTP dla strony WWW (zamiast terminala)

```bash
uvicorn usbot.api.main:app --host 127.0.0.1 --port 8000
```

Serwer przy starcie ładuje model embeddingów (z `RERANK=true` także reranker), otwiera
pulę połączeń z bazą i tworzy klienta LLM-a — to trwa kilkanaście–kilkadziesiąt sekund;
gotowość sygnalizuje linia `Application startup complete`. Sprawdzenie:

```bash
curl http://127.0.0.1:8000/api/health        # "status": "ok" i 200, gdy baza, model i LLM działają
curl -N http://127.0.0.1:8000/api/chat -H 'Content-Type: application/json' \
  -d '{"message": "Jak połączyć się z siecią eduroam?", "history": []}'
```

- Przy vLLM na porcie 8000 uruchom API na innym porcie: `--port 8080`.
- Na Windows ustaw w `.env` `LLM_BASE_URL=http://127.0.0.1:11434/v1` — z `localhost`
  każde nowe połączenie z Ollamą czeka ~2–3 s na nieudaną próbę przez IPv6.
- Na Ollamie `ENABLE_THINKING=false` nie wyłącza rozumowania Qwen3; zrób to przez
  `LLM_REASONING_EFFORT=none` (pierwszy token kilka razy szybciej — [API](api.md), 3.2).
- `--host 0.0.0.0` udostępnia API w sieci. API nie ma uwierzytelniania ani limitu
  zapytań — wystaw je za reverse proxy (np. nginx; strumienia SSE nie buforuj, serwer
  wysyła `X-Accel-Buffering: no`).
- Frontend z innego originu (np. serwer deweloperski na `:5173`) wymaga
  `API_CORS_ORIGINS=http://localhost:5173`.
- `--workers N` uruchamia N procesów, każdy z własną kopią modeli (bge-m3 ~2 GB pamięci GPU
  na proces). W jednym procesie żądania obsługują wątki: embedding i reranking idą po
  kolei (blokada na modelu), a równoległe odpowiedzi LLM-a obsługuje serwer modelu.

Kontrakt endpointów i format zdarzeń SSE: [Dokumentacja API](api.md) (3.1).

### Krok 8 — Testy (opcjonalnie)

```bash
pip install -r requirements-dev.txt
pytest tests/unit          # bez Dockera, bazy i modeli
pytest tests/integration   # wymaga działającego Dockera (testcontainers)
```

Testy API (`tests/unit/test_api.py`) działają na atrapach — bez serwera, bazy i LLM-a.
Testy integracyjne same uruchamiają tymczasowy kontener `pgvector/pgvector:pg16` —
baza z kroku 5 nie jest potrzebna. Szczegóły: [Testy](testy.md) (6.3).

## 4.3 Ingestia — szczegóły

Pasek postępu pokazuje `+` (strona nowa/zmieniona) oraz `=` (bez zmian).
Crawl jest inkrementalny dzięki tabeli `pages` (ETag/Last-Modified + hash treści).

Pełne odświeżenie od zera (czyści `chunks` i `pages`):

```bash
# Linux / macOS
CRAWL_FRESH=true python ingestion/ingest.py

# Windows PowerShell
$env:CRAWL_FRESH="true"; python ingestion/ingest.py
```

Po zmianie ekstrakcji tekstu (`scrape.py`) pobierz wszystkie strony od nowa, ale bez
liczenia embeddingów dla stron, których tekst się nie zmienił:

```bash
# Linux / macOS
CRAWL_REFETCH=true python ingestion/ingest.py

# Windows PowerShell
$env:CRAWL_REFETCH="true"; python ingestion/ingest.py
```

`CRAWL_LIMIT` musi objąć całą bazę — strony, do których przebieg nie dojdzie,
zachowują starą ekstrakcję. Pełny przebieg `CRAWL_REFETCH=true CRAWL_LIMIT=12000`
trwa ok. 1,5 h: strony z cache serwera idą ~9 stron/s, a aktualności i wydarzenia
(`post`/`event`, odpowiedź ~1 s) ~2 strony/s. Ogranicza to serwer, nie GPU — CPU, GPU
i baza są w tym czasie w większości bezczynne. Log warto zapisać do pliku
(`*.log` jest w `.gitignore`):

```bash
CRAWL_REFETCH=true CRAWL_LIMIT=12000 python ingestion/ingest.py > ingest-refetch.log 2>&1
```

**Po zmianie chunkingu** (`_sections()` / `_chunks()` w `ingest.py`, rozmiar fragmentów,
prefiks) podbij stałą `CHUNKER_VERSION` w `ingestion/ingest.py`. Inny podział nie zmienia
tekstu strony, więc hash zostaje ten sam i `CRAWL_REFETCH` nie przeliczyłby embeddingów.
Wersja podziału jest zapisana w `pages.chunker_version`; przy starcie ingestia pobiera
(bez 304) i embeduje od nowa wszystkie strony z inną wersją — wystarczy zwykłe
`python ingestion/ingest.py`. Strony, do których przebieg nie dojdzie przed
`CRAWL_LIMIT`, zostaną przeliczone w kolejnym przebiegu. Pełne przeliczenie bazy
(~12 tys. stron) trwa, jak `CRAWL_REFETCH`, ok. 1,5 h.

Zachowanie crawlera dostroisz w `.env`: `CRAWL_LIMIT`, `CRAWL_DEPTH`,
`CRAWL_WORKERS`, `EMBED_BATCH` (patrz [Dokumentacja API](api.md)).
Uruchamiaj okresowo (np. cron raz dziennie), gdy treści na stronie się zmieniają.

## 4.4 Konfiguracja bazy danych

Inicjalizacja przebiega automatycznie przy pierwszym uruchomieniu kontenera — plik
`infra/init.sql` wykonywany jest przez mechanizm `docker-entrypoint-initdb.d`.
Tworzy on:

- rozszerzenia pgvector i unaccent (`CREATE EXTENSION IF NOT EXISTS vector`, `unaccent`)
  oraz funkcję `immutable_unaccent()`,
- tabelę `chunks` z indeksami `chunks_embedding_idx` (HNSW), `chunks_content_tsv_idx`
  (GIN, wyszukiwanie pełnotekstowe) i `chunks_url_idx`,
- tabelę `pages` (stan crawlera).

`init.sql` nie wykona się ponownie na istniejącym wolumenie, dlatego kolumny dodane
później (`chunks.page_type`, `chunks.published_at`, `chunks.content_hash`,
`chunks.body_hash`, `chunks.content_tsv`, `pages.chunker_version`, `pages.chunk_hashes`)
dokłada migracja w `_ensure_schema()` przy każdym starcie `ingestion/ingest.py`. `cli.py`
korzysta z `body_hash`, a tryb `RETRIEVAL_MODE=hybrid` z `content_tsv`, więc po
aktualizacji kodu uruchom ingestię przed botem. Dodanie `content_tsv` przepisuje całą
tabelę `chunks` (z indeksem HNSW) — pierwsza migracja trwa ok. minuty.

!!! info "Port bazy danych: 5433"
    Baza mapowana jest na port **5433** (`5433:5432` w `docker-compose.yml`), aby
    uniknąć konfliktu z lokalnym PostgreSQL na domyślnym porcie 5432. Zgodnie z tym
    `DATABASE_URL` w `.env` używa `localhost:5433`.

Dane persystowane są w wolumenie Docker `pgdata`. Reset bazy:

```bash
docker compose down -v        # usuwa wolumen pgdata
docker compose up -d db        # tworzy bazę od nowa
python ingestion/ingest.py     # ponowna ingestia
```

!!! danger "Nie commituj `.env`"
    Plik `.env` zawiera poufne dane dostępowe i nie należy go commitować do
    repozytorium (jest w `.gitignore`). Współdziel tylko `.env.example`.
