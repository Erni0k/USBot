# 3. Dokumentacja API

USBot nie eksponuje jeszcze własnego API HTTP — aplikacja jest terminalowa. Poniżej
opisano dwa interfejsy zewnętrzne, z których korzysta: **API modelu LLM** oraz
**interfejs pgvector**. Wewnętrzny interfejs dla przyszłej aplikacji webowej —
`ChatService.stream()` i jego strumień zdarzeń — opisuje [Architektura](architektura.md) (2.1).

## 3.1 API modelu językowego (OpenAI-compatible)

**Endpoint:** `POST /v1/chat/completions`

Wywoływany przez `openai.OpenAI.chat.completions.create()` w adapterze
`usbot/adapters/llm.py` (`OpenAICompatibleLLM`, port `ChatLLM`).

| Parametr | Typ | Wartość | Opis |
|---|---|---|---|
| `model` | string | `qwen3:8b` / `qwen3` | Nazwa załadowanego modelu |
| `messages` | array | `[system, ...history]` | Pełna historia rozmowy + system prompt z kontekstem |
| `temperature` | float | `0.3` | Niska temperatura = deterministyczne odpowiedzi |
| `stream` | boolean | `true` | Strumieniowe generowanie (token po tokenie) |
| `extra_body.chat_template_kwargs` | object | `{enable_thinking: false}` | Parametr vLLM/Ollama — tryb rozumowania |

### System prompt

System prompt ogranicza odpowiedzi do dostarczonego kontekstu i zawiera placeholder
`{context}` wypełniany wynikami wyszukiwania (`build_system_prompt()` w
`usbot/core/prompts.py`):

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
SELECT id, url, title, content, page_type, published_at, body_hash,
       embedding <=> %s::vector AS dist,
       page_type IN ('news', 'event')
         AND published_at < now() - make_interval(days => %s) AS old_news
FROM chunks
ORDER BY dist
LIMIT %s;
-- %s[0] = wektor zapytania (lista float, dim=1024)
-- %s[1] = OLD_NEWS_DAYS (domyślnie 730)
-- %s[2] = liczba kandydatów: max(5·TOP_K, 40)
```

Zapytania wykonuje adapter `PgvectorRetriever` (`usbot/adapters/pgvector.py`). Z
kandydatów zostaje `TOP_K` wyników (`select()` w `usbot/core/ranking.py`): ta sama treść
(`body_hash`) tylko raz — najbliższy fragment, przy równej odległości ten z krótszym
URL-em — i najwyżej `MAX_PER_URL` fragmentów z jednej strony. Wyniki pomiaru:
[Testy](testy.md) (6.4).

Operator `<=>` realizuje wyszukiwanie według odległości kosinusowej. Wyniki
zwracane są w kolejności rosnącej odległości (najlepsze dopasowanie pierwsze).
Fragmenty dalsze niż `MAX_DISTANCE` są odrzucane w Pythonie (przy indeksie HNSW
warunek `WHERE` na odległość mógłby zwrócić za mało wyników).

Przy `OLD_NEWS_PENALTY > 0` fragmenty starych aktualności i wydarzeń (`old_news`)
dostają przy sortowaniu `dist + OLD_NEWS_PENALTY` i z kandydatów zostaje `TOP_K`
najlepszych. Próg nadal dotyczy samej odległości. Domyślnie kara jest wyłączona —
uzasadnienie w [Testy](testy.md) (6.4).

Przy `RETRIEVAL_MODE=hybrid` (`PgvectorRetriever(mode="hybrid")`) obok tego zapytania wykonywane jest
wyszukiwanie pełnotekstowe po kolumnie `chunks.content_tsv` (`'simple'` + `unaccent`,
indeks GIN): słowa pytania bez stop-słów połączone OR, ranking `ts_rank_cd`, ta sama
liczba kandydatów. Obie listy łączy Reciprocal Rank Fusion (`score = Σ 1/(60 + pozycja)`).
Każdy wynik ma `dist` liczony z embeddingu, więc próg `MAX_DISTANCE` działa tak samo.
Domyślnie tryb jest wyłączony — uzasadnienie w [Testy](testy.md) (6.4).

Przy `RERANK=true` z kandydatów `select()` wybiera 20 (`RERANK_CANDIDATES`), cross-encoder
`BAAI/bge-reranker-v2-m3` ocenia pary (pytanie, fragment) i zostaje `TOP_K` z najwyższym
wynikiem (`RerankingRetriever` i `rerank()` w `usbot/core/ranking.py`; przy dwóch
zapytaniach czatu lepszy z wyników dla obu). Próg
działa wtedy na wyniku rerankera (`MIN_RERANK_SCORE`, 0–1), a `MAX_DISTANCE` jest
pomijany (`cutoff()` w `usbot/core/chat.py`). Model ładowany raz, przy pierwszym
użyciu (`CrossEncoderReranker`, `usbot/adapters/reranker.py`), na GPU w fp16. Wyniki i czasy:
[Testy](testy.md) (6.4).

!!! tip "Retrieval z kontekstem rozmowy"
    Zapytanie nie jest budowane z samego ostatniego komunikatu — funkcja
    `build_query()` skleja kilka ostatnich pytań użytkownika. Dzięki temu krótkie
    follow-upy (np. „a wnst?", „dziekanatu tam") trafiają we właściwy kontekst.

### Zapis przy ingestii (per-URL, inkrementalnie)

```sql
-- zapis stanu strony (cache crawlera) z hashami wszystkich fragmentów
INSERT INTO pages (url, etag, last_modified, content_hash, links, chunker_version,
                   chunk_hashes, last_crawled)
VALUES (%s, %s, %s, %s, %s, %s, %s, now())
ON CONFLICT (url) DO UPDATE SET ...;

-- cache embeddingów: gotowe wektory dla treści, które już są w bazie
SELECT DISTINCT ON (content_hash) content_hash, embedding
FROM chunks WHERE content_hash = ANY(%s);

-- podmiana fragmentów zmienionej strony (bez boilerplate'u)
DELETE FROM chunks WHERE url = %s;
INSERT INTO chunks (url, title, page_type, published_at, content, content_hash, body_hash, embedding)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
```

`content` każdego fragmentu zaczyna się od prefiksu `<tytuł strony> > <nagłówek
sekcji>` — z tej treści liczony jest embedding. Boilerplate to treść (`body_hash`)
występująca w `pages.chunk_hashes` więcej niż `BOILERPLATE_URLS` stron; jej
wcześniejsze kopie są usuwane w tej samej transakcji (opis reguły w README).

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
| `MAX_PER_URL` | `2` | Najwyżej tyle fragmentów z jednej strony w wynikach; `0` = bez limitu |
| `RETRIEVAL_MODE` | `vector` | `vector` albo `hybrid` (wektorowe + pełnotekstowe, RRF) |
| `RERANK` | `false` | `true` = reranking 20 kandydatów cross-encoderem, zostaje `TOP_K`; wymaga GPU |
| `RERANK_MODEL` | `BAAI/bge-reranker-v2-m3` | Model rerankera (cross-encoder, Hugging Face) |
| `MIN_RERANK_SCORE` | `0.01` | Przy `RERANK=true` próg zamiast `MAX_DISTANCE`: min. wynik rerankera fragmentu |
| `BOILERPLATE_URLS` | `20` | Treść fragmentu na większej liczbie stron to boilerplate i nie trafia do bazy; `0` = wyłączone |
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
