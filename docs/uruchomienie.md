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

!!! note "Bez GPU lub z GPU"
    Ollama działa też na CPU, ale model `qwen3:8b` jest wtedy wolny — rozważ
    `qwen3:4b` lub `qwen3:1.7b`. Z kartą NVIDIA odpowiedzi są szybkie.
    Alternatywą jest vLLM (wymaga `nvidia-container-toolkit`).

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
#   LLM_BASE_URL=http://localhost:11434/v1
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

Zachowanie crawlera dostroisz w `.env`: `CRAWL_LIMIT`, `CRAWL_DEPTH`,
`CRAWL_WORKERS`, `EMBED_BATCH` (patrz [Dokumentacja API](api.md)).
Uruchamiaj okresowo (np. cron raz dziennie), gdy treści na stronie się zmieniają.

## 4.4 Konfiguracja bazy danych

Inicjalizacja przebiega automatycznie przy pierwszym uruchomieniu kontenera — plik
`infra/init.sql` wykonywany jest przez mechanizm `docker-entrypoint-initdb.d`.
Tworzy on:

- rozszerzenie pgvector (`CREATE EXTENSION IF NOT EXISTS vector`),
- tabelę `chunks` z indeksami `chunks_embedding_idx` (HNSW) i `chunks_url_idx`,
- tabelę `pages` (stan crawlera).

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
