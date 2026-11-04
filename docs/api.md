# 3. Dokumentacja API

USBot udostępnia **API HTTP** (FastAPI, `usbot/api/`) dla aplikacji webowej: rozmowa jako
strumień Server-Sent Events, stan usług i lista wydziałów. Sam korzysta z dwóch interfejsów
zewnętrznych: **API modelu LLM** (3.3) i **bazy pgvector** (3.4). API HTTP, tak jak
`cli.py`, jest tylko adapterem na `ChatService` — warstwy opisuje
[Architektura](architektura.md) (2.1).

## 3.1 API HTTP USBot

```bash
uvicorn usbot.api.main:app --host 127.0.0.1 --port 8000
```

Szczegóły uruchomienia (baza, Ollama, port przy vLLM): [Instrukcja uruchomienia](uruchomienie.md)
(krok 7b). Opis OpenAPI generuje FastAPI: `http://127.0.0.1:8000/docs`.

| Metoda | Ścieżka | Odpowiedź | Opis |
|---|---|---|---|
| `POST` | `/api/chat` | `text/event-stream` | Odpowiedź na pytanie jako strumień zdarzeń SSE |
| `GET` | `/api/health` | JSON, `200` / `503` | Stan bazy, modelu embeddingów (i rerankera) oraz LLM-a |
| `GET` | `/api/faculties` | JSON | Wydziały — np. przyciski po zdarzeniu `clarify` |

**Serwer jest bezstanowy.** Historię rozmowy trzyma klient i przysyła ją w każdym żądaniu;
serwer bierze z niej tylko `API_HISTORY_TURNS` (domyślnie 5) ostatnich tur (tura = pytanie i
odpowiedź). Do historii klient dopisuje pytanie i `done.answer` (przy `refused`/`clarify` —
ich `message`); po `error` — samo pytanie albo nic.

Zasoby ładowane są raz, przy starcie serwera (lifespan): model embeddingów (z `RERANK=true`
także reranker), pula połączeń z bazą (`psycopg_pool`, `DB_POOL_MIN`–`DB_POOL_MAX`) i klient
LLM-a. Start trwa kilkanaście–kilkadziesiąt sekund (ładowanie bge-m3); żadne żądanie nie
ładuje modelu. Serwer wstaje także przy niedostępnej bazie lub LLM-ie — pokaże to
`/api/health`, a czat zwróci zdarzenie `error`.

### POST /api/chat

Żądanie (`Content-Type: application/json`):

```json
{
  "message": "A godziny dziekanatu WNP?",
  "history": [
    {"role": "user", "content": "Gdzie jest dziekanat?"},
    {"role": "assistant", "content": "Każdy wydział ma własny dziekanat…"}
  ]
}
```

| Pole | Typ | Ograniczenia |
|---|---|---|
| `message` | string | 1–2000 znaków, nie same spacje (spacje na końcach są obcinane) |
| `history` | lista, domyślnie `[]` | najwyżej 100 wiadomości |
| `history[].role` | `"user"` / `"assistant"` | inne role (np. `system`) są odrzucane |
| `history[].content` | string | najwyżej 20 000 znaków |

Niepoprawne żądanie dostaje **`422`** z opisem błędów w JSON (format FastAPI), bez strumienia.

Odpowiedź: **`200`, `text/event-stream`**. Każde zdarzenie to dwie linie — `event:` z typem
i `data:` z całym zdarzeniem jako JSON w jednej linii (UTF-8; `type` w danych powtarza
typ zdarzenia):

```text
event: sources
data: {"type":"sources","sources":[{"url":"https://eduroam.us.edu.pl/konfiguracja","title":"KONFIGURACJA – eduroam","page_type":"static","published_at":null,"dist":0.3586,"rerank":null}]}

event: token
data: {"type":"token","text":"Aby"}

event: token
data: {"type":"token","text":" połączyć"}

event: done
data: {"type":"done","answer":"Aby połączyć się z siecią eduroam…"}
```

| Zdarzenie | Kiedy | Pola |
|---|---|---|
| `sources` | fragmenty przeszły próg; zaraz popłyną tokeny | `sources`: lista stron (każda raz, w kolejności wyników) — `url`, `title`, `page_type`, `published_at` (ISO 8601 albo `null`), `dist` (odległość kosinusowa), `rerank` (wynik rerankera albo `null`) |
| `token` | kolejny kawałek odpowiedzi LLM-a | `text` |
| `refused` | nic nie przeszło progu — bez wywołania LLM-a | `message` (stały tekst „Nie mam tej informacji…”) |
| `clarify` | pytanie o dziekanat bez wydziału — bez wywołania LLM-a | `message` (pytanie z listą wydziałów), `options`: `[{"code", "name"}]` jak w `/api/faculties` |
| `done` | koniec odpowiedzi | `answer`: pełny tekst (sklejone tokeny albo `message` z `refused`/`clarify`) |
| `error` | błąd w trakcie (baza, LLM, timeout) | `code`, `message` (dla użytkownika; szczegóły tylko w logu serwera) |

Kolejność jest jedna z czterech: `sources → token… → done`, `refused → done`,
`clarify → done` albo w dowolnym miejscu `error` jako ostatnie zdarzenie (bez `done`).
Źródła pochodzą z wyników wyszukiwania, nie z tekstu LLM-a. Gdy przez 10 s nie ma
zdarzenia (wyszukiwanie, czekanie na pierwszy token), serwer wysyła komentarz SSE
`: ping` — klient go pomija.

| `error.code` | Przyczyna |
|---|---|
| `db_unavailable` | brak połączenia z bazą albo brak wolnego połączenia w puli w `DB_TIMEOUT` |
| `db_timeout` | zapytanie dłuższe niż `DB_TIMEOUT` (`statement_timeout`) |
| `llm_unavailable` | brak połączenia z serwerem modelu |
| `llm_timeout` | brak odpowiedzi albo kolejnego fragmentu odpowiedzi LLM-a w `LLM_TIMEOUT` |
| `llm_error` | serwer modelu zwrócił błąd HTTP (np. nieznany model) |
| `internal` | inny błąd serwera |

**Rozłączenie klienta przerywa generowanie.** Gdy klient zamknie połączenie (np. przerwie
`fetch` albo zamknie kartę), serwer przestaje czytać odpowiedź LLM-a przy najbliższym
tokenie i zamyka strumień HTTP do modelu — Ollama/vLLM kończy generowanie. Wyjątek: przy
włączonym rozumowaniu Qwen3 (domyślnie na Ollamie, patrz 3.2) tokeny „myślenia” nie
docierają do ChatService, więc przerwanie następuje dopiero po ich końcu. Czekania na
pierwszy token nie da się przerwać — ogranicza je `LLM_TIMEOUT`.

Przykład (`-N` wyłącza buforowanie curla, więc tokeny widać na bieżąco):

```bash
curl -N http://127.0.0.1:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message": "Jak połączyć się z siecią eduroam?", "history": []}'
```

Kontynuacja rozmowy — historia z poprzedniej tury:

```bash
curl -N http://127.0.0.1:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message": "WNP", "history": [
        {"role": "user", "content": "Gdzie jest dziekanat?"},
        {"role": "assistant", "content": "Każdy wydział ma własny dziekanat… O który wydział chodzi?"}]}'
```

!!! warning "Polskie znaki w curl na Windows"
    W Git Bash i PowerShellu argument `-d` z polskimi znakami bywa wysyłany w kodowaniu
    systemu (cp1252) zamiast UTF-8 — serwer odpowiada wtedy `400`. Zapisz treść żądania
    do pliku w UTF-8 i wyślij `--data-binary @pytanie.json`.

Przeglądarkowy `EventSource` obsługuje tylko `GET`, więc klient czyta strumień przez
`fetch`:

```js
const res = await fetch("/api/chat", {
  method: "POST",
  headers: {"Content-Type": "application/json"},
  body: JSON.stringify({message, history}),
  signal: controller.signal,          // controller.abort() przerywa generowanie
});
const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
let buffer = "";
for (;;) {
  const {value, done} = await reader.read();
  if (done) break;
  buffer += value;
  let end;
  while ((end = buffer.indexOf("\n\n")) >= 0) {
    const block = buffer.slice(0, end);
    buffer = buffer.slice(end + 2);
    const data = block.split("\n").find(l => l.startsWith("data: "));
    if (data) handle(JSON.parse(data.slice(6)));   // {type: "token", text: "..."} itd.
  }
}
```

### GET /api/health

Sprawdzenia działają równolegle, każde z limitem `HEALTH_TIMEOUT` (domyślnie 3 s):
`database` — połączenie z puli i niepusta tabela `chunks`; `embeddings` — embedding
słowa testowego; `reranker` (tylko przy `RERANK=true`); `llm` — lista modeli serwera
(`GET /v1/models`) zawiera `LLM_MODEL`. Wszystkie w porządku → `200` i `"status": "ok"`,
inaczej `503` i `"status": "error"`.

```bash
curl http://127.0.0.1:8000/api/health
```

```json
{
  "status": "ok",
  "checks": {
    "database":   {"ok": true, "ms": 7.7,   "error": null, "detail": {"chunks": true}},
    "embeddings": {"ok": true, "ms": 361.8, "error": null, "detail": {"model": "BAAI/bge-m3", "dim": 1024}},
    "llm":        {"ok": true, "ms": 12.4,  "error": null, "detail": {"model": "qwen3:8b"}}
  }
}
```

Przy problemie `ok` jest `false`, a `error` opisuje przyczynę, np. `"brak odpowiedzi w 3 s"`
albo `"RuntimeError: serwer nie ma modelu 'qwen3:8b' (są: qwen3:1.7b)"`.

### GET /api/faculties

```bash
curl http://127.0.0.1:8000/api/faculties
```

```json
[{"code": "WH", "name": "Wydział Humanistyczny"},
 {"code": "WNP", "name": "Wydział Nauk Przyrodniczych"},
 {"code": "WNS", "name": "Wydział Nauk Społecznych"},
 {"code": "WNŚiT", "name": "Wydział Nauk Ścisłych i Technicznych"},
 {"code": "WPiA", "name": "Wydział Prawa i Administracji"},
 {"code": "WSNE", "name": "Wydział Sztuki i Nauk o Edukacji"},
 {"code": "WTL", "name": "Wydział Teologiczny"}]
```

Ta sama lista co `clarify.options`. Odpowiedź skrótem („WNP”) wystarczy — w zapytaniu do
wyszukiwarki skrót jest rozwijany do pełnej nazwy.

## 3.2 Czasy odpowiedzi API

Pomiar z 2026-11-04: `python evaluation/latency.py --runs 2` — 5 pytań studenckich, każde
2 razy (10 odpowiedzi na konfigurację), po jednym pytaniu rozgrzewkowym (nie liczone).
Czas liczony po stronie klienta od wysłania `POST /api/chat`: do zdarzenia `sources`, do
pierwszego `token` i do `done`. Sprzęt: Windows 11, RTX 3080 10 GB (wspólna dla bge-m3 i
LLM-a), Ollama 0.24.0, `qwen3:8b` (100% GPU), `RETRIEVAL_MODE=vector`, bez rerankingu,
`TOP_K=8`, baza 42 330 fragmentów.

| Konfiguracja | Źródła (mediana) | Pierwszy token (mediana) | Pierwszy token (zakres) | Całość (mediana) | Całość (zakres) |
|---|---|---|---|---|---|
| `LLM_BASE_URL=http://localhost:11434/v1` (`.env` sprzed zmian) | 0.11 s | 8.06 s | 7.16–10.45 s | 16.12 s | 13.09–18.29 s |
| `127.0.0.1` zamiast `localhost` | 0.11 s | 6.51 s | 4.44–9.40 s | 13.89 s | 10.10–16.43 s |
| `127.0.0.1` + `LLM_REASONING_EFFORT=none` | 0.11 s | **0.82 s** | 0.71–0.94 s | **6.40 s** | 3.55–11.57 s |

Ostatnia konfiguracja, pytanie po pytaniu (2 przebiegi):

| Pytanie | Pierwszy token [s] | Całość [s] | Długość odpowiedzi [znaki] |
|---|---|---|---|
| Jak połączyć się z siecią eduroam na uczelni? | 0.76 / 0.71 | 11.57 / 9.08 | 1467 / 1262 |
| Jak ubiegać się o stypendium socjalne? | 0.94 / 0.90 | 6.95 / 5.88 | 864 / 864 |
| Jak zalogować się do USOSweb? | 0.89 / 0.75 | 6.39 / 5.09 | 833 / 701 |
| Godziny pracy dziekanatu Wydziału Nauk Przyrodniczych | 0.86 / 0.83 | 3.92 / 3.55 | 321 / 321 |
| Jak wyrobić elektroniczną legitymację studencką? | 0.81 / 0.81 | 6.41 / 6.71 | 884 / 880 |

Wnioski:

- **Wyszukiwanie to ~0.1 s** (embedding pytania, dwa zapytania SQL, próg) — prawie cały czas
  to LLM. Zimny start modelu w Ollamie (pierwsze pytanie po załadowaniu) dokłada kilka sekund.
- **Ollama ignoruje `ENABLE_THINKING=false`** (`chat_template_kwargs`): Qwen3 przed odpowiedzią
  „myśli” 300–500 tokenów, które przychodzą w osobnym polu `reasoning` i nie trafiają do
  odpowiedzi — użytkownik czeka na nie 5–10 s. `LLM_REASONING_EFFORT=none` (parametr
  `reasoning_effort`) naprawdę wyłącza rozumowanie: pierwszy token po ~0.8 s, całość ~2×
  krócej. Przy włączonym rozumowaniu przerwanie połączenia działa dopiero po jego końcu
  (tokeny rozumowania nie docierają do `ChatService`). Dla vLLM parametru nie ustawiaj —
  tam działa `chat_template_kwargs`, a vLLM może odrzucić `reasoning_effort=none`.
- **`localhost` na Windows kosztuje ~2 s na każde pytanie**: klient najpierw próbuje IPv6
  (`::1`), na którym Ollama nie nasłuchuje, a połączenia ze strumieniem nie są ponownie
  używane, więc każde pytanie łączy się od nowa (osobny pomiar samego LLM-a: pierwszy token
  2.1 s z `localhost`, 0.1 s z `127.0.0.1`). Z `localhost` także sprawdzenie `llm` w
  `/api/health` (nowe połączenie, ~2–3 s) potrafi przekroczyć `HEALTH_TIMEOUT` = 3 s.
  Domyślny `LLM_BASE_URL` to teraz `http://127.0.0.1:11434/v1`.
- Odpowiedź generuje się z prędkością ~45–50 tokenów (fragmentów) na sekundę; całkowity czas
  zależy głównie od długości odpowiedzi (321 znaków — 3.6 s, 1467 znaków — 11.6 s).

## 3.3 API modelu językowego (OpenAI-compatible)

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

## 3.4 Interfejs pgvector (SQL)

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

## 3.5 Zmienne środowiskowe (`.env`)

| Zmienna | Domyślna wartość | Opis |
|---|---|---|
| `DATABASE_URL` | `postgresql://chatbot:changeme@localhost:5433/chatbot` | Connection string do PostgreSQL |
| `LLM_BASE_URL` | `http://127.0.0.1:11434/v1` (Ollama; nie `localhost` — 3.2) | Adres serwera modelu językowego |
| `LLM_API_KEY` | `not-needed` | Klucz API (Ollama nie wymaga) |
| `LLM_MODEL` | `qwen3:8b` | Nazwa modelu do użycia |
| `EMBED_MODEL` | `BAAI/bge-m3` | Model embeddingów (Hugging Face) |
| `EMBED_DIM` | `1024` | Tylko informacyjnie (w `.env.example`) — kod go nie czyta; wymiar wynika z modelu i kolumny `vector(1024)` w `infra/init.sql` |
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
| `ENABLE_THINKING` | `false` | Tryb rozumowania Qwen3 (wolniejszy, dokładniejszy); Ollama go ignoruje — patrz `LLM_REASONING_EFFORT` |
| `LLM_REASONING_EFFORT` | puste | `reasoning_effort` wysyłany do API LLM-a; na Ollamie `none` naprawdę wyłącza rozumowanie Qwen3 (Ollama ignoruje `ENABLE_THINKING`), pomiar w 3.2; puste = nie wysyłany |
| `LLM_TIMEOUT` | `60` | Limit (s) na połączenie z LLM-em (maks. 5 s) i na każdy kolejny fragment odpowiedzi |
| `API_HISTORY_TURNS` | `5` | API: ile ostatnich tur historii klienta trafia do `ChatService` |
| `API_CORS_ORIGINS` | puste | API: dozwolone originy CORS po przecinku (np. `http://localhost:5173`); puste = bez CORS |
| `DB_POOL_MIN` / `DB_POOL_MAX` | `1` / `4` | API: rozmiar puli połączeń z bazą |
| `DB_TIMEOUT` | `10` | API: limit (s) na połączenie z puli, nawiązanie połączenia i jedno zapytanie (`statement_timeout`) |
| `HEALTH_TIMEOUT` | `3` | API: limit (s) jednego sprawdzenia w `/api/health` |
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

## 3.6 Narzędzia testowe API modelu

Serwer modelu eksponuje API zgodne z OpenAI, więc można go testować standardowo:

- **Postman / Insomnia** — import kolekcji OpenAI, zmiana base URL na
  `http://127.0.0.1:11434/v1` (Ollama) lub `http://localhost:8000/v1` (vLLM).
- **curl** — szybkie testy z wiersza poleceń (przykład poniżej).
- **Python `openai` SDK** — interaktywne testowanie w REPL lub Jupyterze.

```bash
curl http://127.0.0.1:11434/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "qwen3:8b",
    "messages": [{"role": "user", "content": "test"}],
    "temperature": 0.3
  }'
```
