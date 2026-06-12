# 3. Dokumentacja API

USBot nie eksponuje własnego API HTTP — aplikacja jest terminalowa. Poniżej opisano
dwa interfejsy zewnętrzne, z których korzysta: **API modelu LLM** oraz **interfejs
pgvector**.

## 3.1 API modelu językowego (OpenAI-compatible)

**Endpoint:** `POST /v1/chat/completions`

Wywoływany przez `openai.OpenAI.chat.completions.create()` w `cli.py`.

| Parametr | Typ | Wartość | Opis |
|---|---|---|---|
| `model` | string | `qwen3:8b` / `qwen3` | Nazwa załadowanego modelu |
| `messages` | array | `[system, ...history]` | Pełna historia rozmowy + system prompt z kontekstem |
| `temperature` | float | `0.3` | Niska temperatura = deterministyczne odpowiedzi |
| `stream` | boolean | `true` | Strumieniowe generowanie (token po tokenie) |
| `extra_body.chat_template_kwargs` | object | `{enable_thinking: false}` | Parametr vLLM/Ollama — tryb rozumowania |

### System prompt

System prompt ogranicza odpowiedzi do dostarczonego kontekstu i zawiera placeholder
`{context}` wypełniany wynikami `retrieve()`:

```text
Jesteś asystentem Uniwersytetu Śląskiego w Katowicach.
Odpowiadaj WYŁĄCZNIE na podstawie poniższego kontekstu.
Jeśli w kontekście nie ma odpowiedzi, powiedz:
"Nie mam tej informacji - sprawdź na us.edu.pl lub skontaktuj się z dziekanatem."
Podawaj źródłowy adres URL, z którego pochodzi informacja.
Nie zmyślaj dat, nazwisk, godzin ani kwot. Odpowiadaj po polsku, zwięźle i uprzejmie.

KONTEKST:
{context}
```

## 3.2 Interfejs pgvector (SQL)

### Zapytanie wektorowe (retrieval)

```sql
SELECT url, title, content
FROM chunks
ORDER BY embedding <=> %s::vector
LIMIT %s;
-- %s[0] = wektor zapytania (lista float, dim=1024)
-- %s[1] = TOP_K (domyślnie 8)
```

Operator `<=>` realizuje wyszukiwanie według odległości kosinusowej. Wyniki
zwracane są w kolejności rosnącej odległości (najlepsze dopasowanie pierwsze).

!!! tip "Retrieval z kontekstem rozmowy"
    Zapytanie nie jest budowane z samego ostatniego komunikatu — funkcja
    `build_query()` skleja kilka ostatnich pytań użytkownika. Dzięki temu krótkie
    follow-upy (np. „a wnst?", „dziekanatu tam") trafiają we właściwy kontekst.

### Zapis przy ingestii (per-URL, inkrementalnie)

```sql
-- podmiana fragmentów zmienionej strony
DELETE FROM chunks WHERE url = %s;
INSERT INTO chunks (url, content, embedding) VALUES (%s, %s, %s);

-- zapis stanu strony (cache crawlera)
INSERT INTO pages (url, etag, last_modified, content_hash, links, last_crawled)
VALUES (%s, %s, %s, %s, %s, now())
ON CONFLICT (url) DO UPDATE SET ...;
```

## 3.3 Zmienne środowiskowe (`.env`)

| Zmienna | Domyślna wartość | Opis |
|---|---|---|
| `DATABASE_URL` | `postgresql://chatbot:changeme@localhost:5433/chatbot` | Connection string do PostgreSQL |
| `LLM_BASE_URL` | `http://localhost:11434/v1` (Ollama) | Adres serwera modelu językowego |
| `LLM_API_KEY` | `not-needed` | Klucz API (Ollama nie wymaga) |
| `LLM_MODEL` | `qwen3:8b` | Nazwa modelu do użycia |
| `EMBED_MODEL` | `BAAI/bge-m3` | Model embeddingów (Hugging Face) |
| `EMBED_DIM` | `1024` | Wymiar wektora embeddingu |
| `TOP_K` | `8` | Liczba fragmentów z bazy przekazywanych do LLM |
| `ENABLE_THINKING` | `false` | Tryb rozumowania Qwen3 (wolniejszy, dokładniejszy) |
| `CRAWL_LIMIT` | `6000` | Maks. liczba stron na jeden crawl |
| `CRAWL_DEPTH` | `3` | Głębokość BFS (0 = tylko sitemap + seedy) |
| `CRAWL_WORKERS` | `6` | Równoległe requesty HTTP |
| `EMBED_BATCH` | `16` | Ile stron embedować naraz |
| `CRAWL_FRESH` | `false` | `true` = pełny re-crawl (czyści `chunks` i `pages`) |
| `HF_HUB_OFFLINE` | `1` (ustawiane automatycznie) | Tryb offline Hugging Face; `0` wymusza pobranie modelu |
| `POSTGRES_USER` | `chatbot` | Użytkownik bazy (Docker) |
| `POSTGRES_PASSWORD` | `changeme` | Hasło bazy (Docker) |
| `POSTGRES_DB` | `chatbot` | Nazwa bazy danych (Docker) |

## 3.4 Narzędzia testowe API

Serwer modelu eksponuje API zgodne z OpenAI, więc można go testować standardowo:

- **Postman / Insomnia** — import kolekcji OpenAI, zmiana base URL na
  `http://localhost:11434/v1` (Ollama) lub `http://localhost:8000/v1` (vLLM).
- **curl** — szybkie testy z wiersza poleceń (przykład poniżej).
- **Python `openai` SDK** — interaktywne testowanie w REPL lub Jupyterze.

```bash
curl http://localhost:11434/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "qwen3:8b",
    "messages": [{"role": "user", "content": "test"}],
    "temperature": 0.3
  }'
```
