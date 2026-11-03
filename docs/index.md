# USBot

**Chatbot Uniwersytetu Śląskiego w Katowicach**

!!! info "Metryka dokumentu"
    - **Wersja:** 0.2 (czerwiec 2026)
    - **Repozytorium:** [github.com/Erni0k/USBot](https://github.com/Erni0k/USBot)
    - **Status:** w aktywnym rozwoju

USBot to **terminalowy chatbot** oparty na technice **RAG** (Retrieval-Augmented
Generation), który odpowiada na pytania o sprawy uczelniane na podstawie treści
pobranych ze stron `us.edu.pl`, `usnet.us.edu.pl` i `eduroam.us.edu.pl`.
Zamiast ręcznie przeszukiwać dziesiątki podstron, użytkownik zadaje pytanie w
języku naturalnym i otrzymuje zwięzłą odpowiedź wraz ze źródłowym adresem URL.

## Jak to działa w skrócie

```
Użytkownik (terminal)
       │
       ▼
    cli.py
       │
       ├─ retrieval: pgvector (embeddingi bge-m3, zapytanie z kontekstem rozmowy)
       │    opcjonalnie + pełnotekstowe Postgres FTS, łączone przez RRF (RETRIEVAL_MODE=hybrid)
       ├─ wybór TOP_K: bez powtórzonej treści, maks. 2 fragmenty z jednej strony
       │    opcjonalnie reranking top-20 cross-encoderem bge-reranker-v2-m3 (RERANK=true)
       ├─ próg odległości (z RERANK — wyniku rerankera): nic pasującego → stała odpowiedź, bez LLM-a
       └─ generacja: Ollama / vLLM (Qwen3, API OpenAI-compatible, streaming)
```

1. Pytanie użytkownika (wraz z kilkoma poprzednimi turami) jest **embeddowane lokalnie**.
2. Zapytanie wektorowe do **PostgreSQL + pgvector** zwraca `TOP_K` najbliższych fragmentów —
   ta sama treść tylko raz, najwyżej `MAX_PER_URL` (2) fragmenty z jednej strony.
   Z `RETRIEVAL_MODE=hybrid` dochodzi wyszukiwanie pełnotekstowe (Postgres FTS), a obie
   listy łączy Reciprocal Rank Fusion. Z `RERANK=true` 20 kandydatów ocenia cross-encoder
   (`bge-reranker-v2-m3`) i zostaje `TOP_K` najlepszych.
3. Fragmenty trafiają do **system promptu** jako kontekst.
4. **Model LLM** (Qwen3 przez Ollama/vLLM) generuje odpowiedź strumieniowo.

Wiedza w bazie utrzymywana jest osobnym, **inkrementalnym crawlerem**
([`ingestion/ingest.py`](uruchomienie.md)).

## Najważniejsze cechy

- 🔍 **RAG** na bazie wektorowej pgvector (embeddingi `BAAI/bge-m3`, 1024 wymiary),
  opcjonalnie **hybrydowy** z wyszukiwaniem pełnotekstowym (`RETRIEVAL_MODE=hybrid`)
  i **rerankingiem** cross-encoderem (`RERANK=true`)
- 🧠 **Lokalny LLM** Qwen3 (domyślnie `qwen3:8b`) — bez wysyłania danych na zewnątrz
- 💬 **Rozmowa wieloturowa** z retrievalem uwzględniającym kontekst poprzednich pytań
- 🕸️ **Inkrementalny crawler** BFS z równoległym pobieraniem (limit żądań na domenę) i pomijaniem niezmienionych stron
- ♻️ **Cache embeddingów** (ta sama treść liczona raz) i pomijanie **boilerplate'u** powtarzanego na wielu stronach
- 🔗 Każda odpowiedź zawiera **źródłowy URL**
- 🖥️ Działa na **Linux / macOS / Windows** (Python 3.11+), pełna obsługa polskich znaków

## Szybki start

```bash
git clone https://github.com/Erni0k/USBot.git
cd USBot
pip install -r requirements.txt
cp .env.example .env            # uzupełnij LLM_BASE_URL, LLM_MODEL
ollama pull qwen3:8b && ollama serve
docker compose up -d db
python ingestion/ingest.py      # pierwsza ingestia (pobiera model embeddingów)
python cli.py
```

Szczegóły: [Instrukcja uruchomienia](uruchomienie.md).
