# US Chatbot

Chatbot dla Uniwersytetu Śląskiego w Katowicach oparty o lokalnie hostowany model
**Qwen3** + **RAG** (treści ze stron `us.edu.pl`, `usnet.us.edu.pl`, `eduroam.us.edu.pl`
trzymane w pgvector).

```
Użytkownik (terminal)
       │
       ▼
    cli.py
       │
       ├─ retrieval: pgvector (embeddingi bge-m3, zapytanie z kontekstem rozmowy)
       └─ generacja: Ollama / vLLM (Qwen3, API OpenAI-compatible)
```

## Struktura

```
us-chatbot/
├── cli.py              # aplikacja terminalowa (punkt wejścia)
├── requirements.txt    # zależności Python
├── ingestion/
│   ├── ingest.py       # pipeline: crawl → chunking → embeddingi → pgvector
│   └── scrape.py       # inkrementalny crawler BFS (równoległy fetch)
├── infra/              # init.sql (pgvector, tabele chunks i pages)
├── docker-compose.yml  # baza Postgres + opcjonalnie vLLM
└── .env.example
```

## Wymagania

- Python 3.11+
- Docker + Docker Compose (dla Postgres z pgvector)
- Ollama (zalecane) lub vLLM (wymaga karty NVIDIA)

## 1. Konfiguracja

```bash
cp .env.example .env
# uzupełnij LLM_BASE_URL, LLM_MODEL i pozostałe zmienne
pip install -r requirements.txt
```

## 2. Model językowy

**Ollama (zalecane):**
```bash
ollama pull qwen3:8b      # mocniejszy; dla słabszego sprzętu: qwen3:4b lub qwen3:1.7b
ollama serve              # endpoint: http://localhost:11434/v1
```

W `.env`:
```
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen3:8b
```

> Rozmiar modelu dobierz do GPU: `qwen3:8b` (~5 GB, dobry balans), `qwen3:14b`
> (~9 GB, najlepsze odpowiedzi), `qwen3:4b`/`qwen3:1.7b` na słaby sprzęt.

**vLLM (wymaga karty NVIDIA, vLLM ≥ 0.9.0):**
```bash
vllm serve Qwen/Qwen3-8B \
  --served-model-name qwen3 \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.85 \
  --reasoning-parser qwen3 \
  --default-chat-template-kwargs '{"enable_thinking": false}' \
  --port 8000
```

W `.env`:
```
LLM_BASE_URL=http://localhost:8000/v1
LLM_MODEL=qwen3
```

Model embeddingów (`BAAI/bge-m3`, ~2 GB) pobiera się automatycznie z Hugging Face
przy pierwszym uruchomieniu. Potem aplikacja działa offline (`HF_HUB_OFFLINE=1`
ustawiane automatycznie) — by wymusić ponowne pobranie ustaw `HF_HUB_OFFLINE=0`.

## 3. Baza danych

```bash
docker compose up -d db
```

Przy pierwszym starcie tworzone są rozszerzenie `vector` oraz tabele `chunks`
(fragmenty + embeddingi) i `pages` (stan crawlera) — patrz `infra/init.sql`.

> Baza mapowana jest na port **5433** (by nie kolidować z lokalną instalacją
> Postgresa na 5432). Zgodnie z tym `DATABASE_URL` w `.env` używa `localhost:5433`.

## 4. Ingestia danych

```bash
python ingestion/ingest.py
```

Crawler robi BFS po `us.edu.pl` (sitemap + linki) oraz subdomenach usnet/eduroam,
ekstrahuje tekst, dzieli na fragmenty, liczy embeddingi i zapisuje do pgvector.
Pasek postępu pokazuje `+` (strona nowa/zmieniona) i `=` (bez zmian).

**Crawl jest inkrementalny** — kolejne uruchomienia pobierają tylko to, co się
zmieniło (nagłówki `If-None-Match` / `If-Modified-Since` + hash treści). Pełne
odświeżenie od zera: `CRAWL_FRESH=true` (czyści `chunks` i `pages`).

Uruchamiaj okresowo (np. cron raz dziennie) gdy treści na stronie się zmieniają.

## 5. Uruchomienie

```bash
python cli.py
```

Wpisz pytanie i naciśnij Enter. Wyjście: `wyjdź` lub Ctrl+C.

> Ollama musi działać (`ollama serve`) i baza musi być wstała (`docker compose up -d db`)
> zanim odpalisz bota.

## Konfiguracja (`.env`)

| Zmienna | Opis | Domyślnie |
|---|---|---|
| `DATABASE_URL` | połączenie do Postgresa | `...@localhost:5433/chatbot` |
| `LLM_BASE_URL` | endpoint modelu (OpenAI-compatible) | `http://localhost:11434/v1` |
| `LLM_MODEL` | nazwa modelu w Ollama/vLLM | `qwen3:8b` |
| `EMBED_MODEL` | model embeddingów | `BAAI/bge-m3` |
| `TOP_K` | ile fragmentów z bazy trafia do kontekstu | `8` |
| `ENABLE_THINKING` | tryb rozumowania Qwen3 (wolniejszy) | `false` |
| `CRAWL_LIMIT` | maks. liczba stron na jeden crawl | `6000` |
| `CRAWL_DEPTH` | głębokość BFS (0 = tylko sitemap + seedy) | `3` |
| `CRAWL_WORKERS` | równoległe requesty HTTP | `6` |
| `EMBED_BATCH` | ile stron embedować naraz | `16` |
| `CRAWL_FRESH` | `true` = pełny re-crawl (czyści bazę) | `false` |

## Uwagi

- `.env` **nie trafia do gita** — współdziel tylko `.env.example`.
- Retrieval bierze pod uwagę kilka ostatnich pytań użytkownika, więc krótkie
  follow-upy („a wnst?", „dziekanatu tam") trafiają we właściwy kontekst.
- Jeśli strona zniknie z serwisu, jej stare fragmenty zostają w bazie — okresowo
  odpalaj `CRAWL_FRESH=true`, by wyczyścić nieaktualne treści.
