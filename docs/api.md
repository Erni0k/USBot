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

DOPYTYWANIE O DZIEKANAT:
(... gdy pytanie o dziekanat nie wskazuje wydziału, bot dopytuje i wymienia
dostępne wydziały, zamiast zgadywać ...)

KONTEKST:
{context}
```

!!! tip "Dopytywanie o dziekanat"
    Każdy wydział ma osobny dziekanat z innymi godzinami. Gdy użytkownik pyta o
    godziny/kontakt dziekanatu, ale nie poda wydziału, bot nie zgaduje — prosi o
    doprecyzowanie i wymienia wydziały (WH, WNP, WNS, WNŚiT, WPiA, WSNE, WTL).

## 3.2 Interfejs pgvector (SQL)

### Zapytanie wektorowe (retrieval)

```sql
SELECT id, url, title, content, page_type, published_at,
       embedding <=> %s::vector AS dist,
       page_type IN ('news', 'event')
         AND published_at < now() - make_interval(days => %s) AS old_news
FROM chunks
ORDER BY dist
LIMIT %s;
-- %s[0] = wektor zapytania (lista float, dim=1024)
-- %s[1] = OLD_NEWS_DAYS (domyślnie 730)
-- %s[2] = TOP_K (domyślnie 8); przy OLD_NEWS_PENALTY > 0: max(4·TOP_K, 40) kandydatów
```

Operator `<=>` realizuje wyszukiwanie według odległości kosinusowej. Wyniki
zwracane są w kolejności rosnącej odległości (najlepsze dopasowanie pierwsze).
Fragmenty dalsze niż `MAX_DISTANCE` są odrzucane w Pythonie (przy indeksie HNSW
warunek `WHERE` na odległość mógłby zwrócić za mało wyników).

Przy `OLD_NEWS_PENALTY > 0` fragmenty starych aktualności i wydarzeń (`old_news`)
dostają przy sortowaniu `dist + OLD_NEWS_PENALTY` i z kandydatów zostaje `TOP_K`
najlepszych. Próg nadal dotyczy samej odległości. Domyślnie kara jest wyłączona —
uzasadnienie w [Testy](testy.md) (6.4).

!!! tip "Retrieval z kontekstem rozmowy"
    Zapytanie nie jest budowane z samego ostatniego komunikatu — funkcja
    `build_query()` skleja kilka ostatnich pytań użytkownika. Dzięki temu krótkie
    follow-upy (np. „a wnst?", „dziekanatu tam") trafiają we właściwy kontekst.

### Zapis przy ingestii (per-URL, inkrementalnie)

```sql
-- podmiana fragmentów zmienionej strony
DELETE FROM chunks WHERE url = %s;
INSERT INTO chunks (url, title, page_type, published_at, content, embedding)
VALUES (%s, %s, %s, %s, %s, %s);

-- zapis stanu strony (cache crawlera)
INSERT INTO pages (url, etag, last_modified, content_hash, links, chunker_version, last_crawled)
VALUES (%s, %s, %s, %s, %s, %s, now())
ON CONFLICT (url) DO UPDATE SET ...;
```

`content` każdego fragmentu zaczyna się od prefiksu `<tytuł strony> > <nagłówek
sekcji>` — z tej treści liczony jest embedding.

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
| `MAX_DISTANCE` | `0.43` | Maks. odległość kosinusowa fragmentu; gdy żaden się nie mieści, bot odpowiada bez LLM-a |
| `OLD_NEWS_PENALTY` | `0` | Kara do odległości (tylko przy sortowaniu) dla `news`/`event` starszych niż `OLD_NEWS_DAYS`; `0` = wyłączone |
| `OLD_NEWS_DAYS` | `730` | Od ilu dni aktualność/wydarzenie jest „stare" |
| `ENABLE_THINKING` | `false` | Tryb rozumowania Qwen3 (wolniejszy, dokładniejszy) |
| `CRAWL_LIMIT` | `6000` | Maks. liczba stron na jeden crawl |
| `CRAWL_DEPTH` | `3` | Głębokość BFS (0 = tylko sitemap + seedy) |
| `CRAWL_WORKERS` | `6` | Równoległe requesty HTTP (usnet: maks. 2 — `HOST_LIMITS` w `scrape.py`) |
| `EMBED_BATCH` | `16` | Ile stron embedować naraz |
| `CRAWL_FRESH` | `false` | `true` = pełny re-crawl (czyści `chunks` i `pages`) |
| `CRAWL_REFETCH` | `false` | `true` = pobiera wszystkie strony od nowa (bez 304), embeduje tylko te ze zmienionym tekstem |
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
