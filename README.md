# US Chatbot

Chatbot dla Uniwersytetu Śląskiego w Katowicach oparty o lokalnie hostowany model
**Qwen3-1.7B** + **RAG** (treści ze strony us.edu.pl trzymane w pgvector).

```
Użytkownik (terminal)
       │
       ▼
    cli.py
       │
       ├─ retrieval: pgvector (embeddingi bge-m3)
       └─ generacja: Ollama / vLLM (Qwen3-1.7B, API OpenAI-compatible)
```

## Struktura

```
us-chatbot/
├── cli.py              # aplikacja terminalowa (punkt wejścia)
├── requirements.txt    # zależności Python
├── ingestion/          # scraper us.edu.pl + chunking + embeddingi → pgvector
├── infra/              # init.sql (pgvector + tabela chunks)
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
# uzupełnij LLM_BASE_URL i pozostałe zmienne
```

## 2. Model językowy

**Ollama (zalecane — działa bez GPU):**
```bash
ollama pull qwen3:1.7b
ollama serve        # endpoint: http://localhost:11434/v1
```

W `.env`:
```
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen3:1.7b
```

**vLLM (wymaga karty NVIDIA, vLLM ≥ 0.9.0):**
```bash
vllm serve Qwen/Qwen3-1.7B \
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
przy pierwszym uruchomieniu.

## 3. Baza danych

```bash
docker compose up -d db
```

Przy pierwszym starcie tworzone są: rozszerzenie `vector` i tabela `chunks` (patrz `infra/init.sql`).

## 4. Ingestia danych

```bash
python ingestion/ingest.py    # scrape us.edu.pl → chunking → embeddingi → pgvector
```

Uruchamiaj okresowo (np. raz w tygodniu) gdy treści na stronie się zmienią.

## 5. Uruchomienie

```bash
pip install -r requirements.txt
python cli.py
```

Wpisz pytanie i naciśnij Enter. Wyjście: `wyjdź` lub Ctrl+C.

## Uwagi

- `.env` **nie trafia do gita** — współdziel tylko `.env.example`.
- Zmienna `ENABLE_THINKING=true` włącza tryb rozumowania Qwen3 (wolniejszy, dokładniejszy).
- `TOP_K` kontroluje ile fragmentów z bazy trafia do kontekstu (domyślnie 5).
