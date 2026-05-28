# US Chatbot

Chatbot dla strony Uniwersytetu Śląskiego oparty o lokalnie hostowany model
**Qwen3-1.7B** + **RAG** (treści ze strony us.edu.pl trzymane w pgvector).

```
Użytkownik → Next.js (frontend) → FastAPI /chat (backend)
                                       │
                                       ├─ retrieval: pgvector (embeddingi bge-m3)
                                       └─ generacja: vLLM / Ollama (Qwen3-1.7B, API OpenAI-compatible)
```

## Struktura

```
us-chatbot/
├── backend/        # FastAPI + retrieval + klient LLM
├── ingestion/      # scraper us.edu.pl + chunking + embeddingi → pgvector
├── infra/          # init.sql (pgvector + tabela chunks)
├── frontend/       # Next.js + czat (instrukcja w frontend/README.md)
├── docker-compose.yml
└── .env.example
```

## 0. Wymagania

- Docker + Docker Compose
- Python 3.11+ (dla skryptu ingestii, jeśli odpalasz lokalnie)
- Do vLLM: karta NVIDIA + sterowniki + `nvidia-container-toolkit`.
  **Bez GPU → użyj Ollama** (patrz niżej), reszta kodu się nie zmienia.

## 1. Konfiguracja

```bash
cp .env.example .env
# uzupełnij hasła i adres modelu (LLM_BASE_URL)
```

## 2. Skąd pobrać model

Nie trzeba pobierać ręcznie — serwer modelu ściąga go sam przy pierwszym starcie.

**Opcja A — vLLM (zalecane, gdy masz GPU).** vLLM pobiera wagi z Hugging Face
automatycznie. Ręcznie (opcjonalnie):
```bash
pip install -U "huggingface_hub[cli]"
huggingface-cli download Qwen/Qwen3-1.7B
```

**Opcja B — Ollama (zalecane na słaby serwer / CPU).** Pobiera skwantyzowany
GGUF (~1 GB):
```bash
ollama pull qwen3:1.7b
ollama serve              # endpoint OpenAI-compatible: http://localhost:11434/v1
```
Wtedy w `.env`:
```
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen3:1.7b
```

Model embeddingów (`BAAI/bge-m3`, ~2 GB) pobiera się sam przy pierwszym
uruchomieniu ingestii/backendu z Hugging Face.

## 3. Konfiguracja i uruchomienie vLLM

vLLM ≥ 0.9.0 (wymagany dla poprawnego wyłączenia trybu „thinking" w Qwen3):

```bash
pip install "vllm>=0.9.0"

vllm serve Qwen/Qwen3-1.7B \
  --served-model-name qwen3 \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.85 \
  --reasoning-parser qwen3 \
  --default-chat-template-kwargs '{"enable_thinking": false}' \
  --port 8000
```

Co robią flagi:
- `--max-model-len 8192` — krótszy kontekst = mniej VRAM (do FAQ wystarczy).
- `--gpu-memory-utilization 0.85` — zostaw zapas, by serwer nie padał przy peaku.
- `--reasoning-parser qwen3 --default-chat-template-kwargs '{"enable_thinking": false}'`
  — **wyłącza tryb rozumowania**. Bot FAQ ma odpowiadać od razu, bez długich
  łańcuchów myśli (szybciej i taniej w tokenach).

Mała karta (np. 4–6 GB VRAM)? Dołóż kwantyzację, np. wariant AWQ:
```bash
vllm serve Qwen/Qwen3-1.7B-AWQ --quantization awq ...
```

Test, że działa:
```bash
curl http://localhost:8000/v1/chat/completions -H "Content-Type: application/json" -d '{
  "model": "qwen3",
  "messages": [{"role":"user","content":"Cześć"}]
}'
```

## 4. Baza + backend

```bash
docker compose up -d db backend
```

Pierwszy start utworzy rozszerzenie `vector` i tabelę `chunks` (patrz `infra/init.sql`).

## 5. Załadowanie wiedzy ze strony (ingestia)

```bash
cd ingestion
pip install -r requirements.txt
python ingest.py            # scrape us.edu.pl → chunking → embeddingi → pgvector
```

Uruchamiaj okresowo (np. cron raz na tydzień), gdy treści na stronie się zmienią.

## 6. Frontend

Patrz `frontend/README.md`.

## Uwagi dla zespołu

- `.env` **nie trafia do gita** (jest w `.gitignore`). Współdziel tylko `.env.example`.
- Podział: Frontend (Next.js) / Backend+RAG (FastAPI) / Dane+Infra (ingestia, vLLM, compose).
- Kontrakt między frontendem a backendem to format `/chat` — nie zmieniajcie go bez ustalenia.
