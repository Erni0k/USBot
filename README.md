# US Chatbot
Docs: https://erni0k.github.io/USBot/
Chatbot dla Uniwersytetu Śląskiego w Katowicach oparty o lokalnie hostowany model
**Qwen3** + **RAG** (treści ze stron `us.edu.pl`, `usnet.us.edu.pl`, `eduroam.us.edu.pl`
trzymane w pgvector).

```
Użytkownik: terminal (cli.py) albo strona WWW (API HTTP: uvicorn usbot.api.main:app)
       │
       ▼
    ChatService (usbot/core) — odpowiedź jako strumień zdarzeń (w API: Server-Sent Events)
       │
       ├─ zapytanie: ostatnie pytania rozmowy, skróty wydziałów („WNP”) rozwinięte do nazw
       ├─ retrieval: pgvector (embeddingi bge-m3, zapytanie z kontekstem rozmowy)
       │    opcjonalnie + pełnotekstowe Postgres FTS, łączone przez RRF (RETRIEVAL_MODE=hybrid)
       ├─ wybór TOP_K: bez powtórzonej treści, maks. 2 fragmenty z jednej strony
       │    opcjonalnie reranking top-20 cross-encoderem bge-reranker-v2-m3 (RERANK=true)
       ├─ próg odległości (z RERANK — wyniku rerankera): nic pasującego → stała odpowiedź, bez LLM-a
       ├─ dziekanat bez wydziału → dopytanie o wydział, bez LLM-a
       └─ generacja: Ollama / vLLM (Qwen3, API OpenAI-compatible, streaming)
```

## Struktura

```
us-chatbot/
├── cli.py              # aplikacja terminalowa — cienki adapter na ChatService
├── usbot/              # logika aplikacji (warstwy: docs/architektura.md, 2.1)
│   ├── config.py       # ustawienia z env i .env (pydantic-settings)
│   ├── ports.py        # interfejsy: Embedder, Retriever, Reranker, ChatLLM
│   ├── core/           # ChatService (strumień zdarzeń), próg, prompt, dopytywanie, ranking
│   ├── adapters/       # bge-m3, cross-encoder, pgvector (vector/hybrid, pula), API OpenAI
│   ├── api/            # FastAPI: POST /api/chat (SSE), /api/health, /api/faculties
│   └── bootstrap.py    # składanie adapterów według ustawień
├── tests/              # pytest: unit/ (atrapy portów), integration/ (testcontainers)
├── requirements.txt    # zależności Python
├── requirements-dev.txt # + pytest, testcontainers
├── ingestion/
│   ├── ingest.py       # pipeline: crawl → chunking → embeddingi (cache) → pgvector
│   └── scrape.py       # inkrementalny crawler BFS (stały pool żądań, limit na domenę)
├── evaluation/
│   ├── evaluate.py     # metryki retrievalu: Hit/Recall/Precision/nDCG@k, MRR, dobór progu (odległość / reranker)
│   ├── latency.py      # czas do pierwszego tokenu i całkowity przez API
│   └── queries.jsonl   # pytania testowe z oznaczonymi URL-ami
├── infra/              # init.sql (pgvector, unaccent, tabele chunks i pages)
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
ollama serve              # endpoint: http://127.0.0.1:11434/v1
```

W `.env`:
```
LLM_BASE_URL=http://127.0.0.1:11434/v1
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

Opcjonalny reranker (`RERANK=true`, model `BAAI/bge-reranker-v2-m3`, ~2.3 GB) pobierz
raz przed pierwszym użyciem — w trybie offline `cli.py` go nie ściągnie:

```bash
HF_HUB_OFFLINE=0 python -c "from sentence_transformers import CrossEncoder; CrossEncoder('BAAI/bge-reranker-v2-m3')"
```

Reranker ma sens na GPU (PyTorch z CUDA): 20 par ocenia w ~90 ms, na CPU w ~6 s.

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
Po zmianie sposobu ekstrakcji tekstu użyj `CRAWL_REFETCH=true` — pobiera wszystkie
strony od nowa (bez 304), ale embeddingi liczy tylko dla stron, których tekst się zmienił.

**Zmiana chunkingu wymaga podbicia `CHUNKER_VERSION`** (stała w `ingestion/ingest.py`).
Inny podział na fragmenty nie zmienia tekstu strony, więc hash się nie zmieni i ani
zwykły przebieg, ani `CRAWL_REFETCH` nie przeliczą embeddingów — w bazie zostałyby
stare fragmenty. Wersja, którą podzielono stronę, jest zapisana w `pages.chunker_version`.
Przy starcie ingestia wybiera strony z inną wersją, pomija dla nich nagłówki warunkowe
i hash, więc zwykłe `python ingestion/ingest.py` pobierze je, podzieli i zaembeduje od
nowa. Strony, do których przebieg nie dojdzie przed `CRAWL_LIMIT`, zostają ze starą
wersją i zostaną przeliczone w kolejnym przebiegu.

**Chunking po sekcjach.** Ekstrakcja zachowuje nagłówki strony (`#`–`######`): trafilatura
w trybie markdown, a gdy gubi treść — własny tekst z `<main>` z nagłówkami. Tekst jest
dzielony po nagłówkach; sekcje dłuższe niż 800 znaków tnie dalej
`RecursiveCharacterTextSplitter(800, 120)`. Każdy fragment zaczyna się od prefiksu
`<tytuł strony> > <nagłówek sekcji>`, np. `dziekanat | Wydział Nauk Przyrodniczych >
Godziny pracy:` — z niego też liczony jest embedding, więc „Godziny pracy: pon 10–14"
nie traci informacji, którego dziekanatu dotyczy. Tytuł to `<title>` bez nazwy serwisu
(„| Uniwersytet Śląski w Katowicach").

**Metadane fragmentów** (`chunks`): `title`, `page_type` (`student` — portal studenta,
`wydzial`, `news` — aktualności i komunikaty, `event` — wydarzenia, `static` — reszta) i
`published_at` (z `<meta property="article:published_time">`, `<time>`, adresu bloga usnet
albo daty na początku treści). Aktualności rozpoznawane są po typie wpisu WordPressa z
klasy `<body>` (`single-post`, `single-komunikaty`, `single-event` — te same typy, od
których pochodzą nazwy pod-sitemap), co działa też dla stron spoza sitemapy, np.
komunikatów portalu studenta. Nowe kolumny dodaje migracja w `_ensure_schema()` —
`init.sql` wykonuje się tylko przy pierwszym starcie kontenera.

**Hashe fragmentów i cache embeddingów.** Każdy fragment ma dwa hashe SHA-256 liczone po
ujednoliceniu białych znaków: `content_hash` — całej treści z prefiksem, czyli tekstu,
z którego liczony jest embedding, i `body_hash` — samej treści bez prefiksu. Przed
liczeniem embeddingów ingestia szuka `content_hash` w `chunks`: gdy taki tekst już jest
(ta sama treść na innej stronie albo niezmieniona sekcja edytowanej strony), bierze
gotowy embedding zamiast liczyć go ponownie; w paczce stron każdy tekst jest embedowany
raz. Podsumowanie przebiegu podaje, ile embeddingów policzono, ile wzięto z cache'u i
ile fragmentów pominięto jako boilerplate. Migracja w `_ensure_schema()` uzupełnia hashe
fragmentów zapisanych wcześniej.

**Boilerplate.** Fragment, którego treść (`body_hash`, bez prefiksu — prefiks z tytułem
jest na każdej stronie inny) występuje na **więcej niż `BOILERPLATE_URLS` (20) stronach**,
uznawany jest za boilerplate i nie trafia do `chunks`. Dziś są to dwa teksty na 67
fragmentach: przyciski „Drukuj stronę Udostępnij na Facebook…” pod aktualnościami
(31 stron) i sam nagłówek „Dostępność architektoniczna:” bez treści (36 stron deklaracji
dostępności). Próg 20 zostawia z zapasem treść wspólną dla kilku stron, która jest
merytoryczna: regulamin akademików powtarzający się na 9 stronach domów studenta.

- Liczy się liczba różnych stron, nie fragmentów. Ingestia trzyma w `pages.chunk_hashes`
  hashe **wszystkich** fragmentów strony, także pominiętych — licząc po `chunks`, licznik
  po wykluczeniu spadłby do zera i kolejna strona zapisałaby tekst z powrotem.
- Gdy tekst przekroczy próg w trakcie ingestii (np. 21. strona z tym samym akapitem),
  jego wcześniejsze kopie są usuwane z `chunks` w tej samej transakcji. Przy starcie
  ingestia usuwa też boilerplate według bieżącego progu (np. po jego obniżeniu).
- Reguła działa na całych fragmentach: wspólny akapit, który splitter skleił z inną
  treścią, nie zostanie rozpoznany. W wynikach wyszukiwania ten sam tekst z kilku stron zostaje
  dodatkowo tylko raz (patrz niżej).
- Tekst, który spadnie poniżej progu (strony go usunęły), wraca do `chunks` dopiero
  przy ponownym embedowaniu pozostałych stron, np. po `CRAWL_FRESH=true`.
- `BOILERPLATE_URLS=0` wyłącza regułę.

**Kolejność i filtrowanie:**

- Najpierw crawlowana jest **kolejka priorytetowa**: seedy (`FALLBACK_URLS` w
  `scrape.py`: usnet, eduroam, dziekanaty, strony ogólne) oraz wszystkie znalezione
  podstrony `PRIORITY_PREFIXES` — portalu studenta `us.edu.pl/student/`, usnet i
  eduroam. Dopiero potem reszta z sitemapy i BFS. Portalu studenta nie ma w
  `sitemap.xml`, więc bez tego crawler docierał do niego dopiero po ~10 tys. URL-i.
- Sitemapa jest **filtrowana** — pomijane są bezwartościowe pod-sitemapy
  (załączniki, multimedia, tagi, kategorie; ~73% sitemapy us.edu.pl), a `page`,
  `komunikaty` i `jednostki` mają pierwszeństwo przed `post`/`event`. Dzięki temu
  budżet `CRAWL_LIMIT` trafia najpierw na najcenniejszą treść.
- Linki z BFS też są filtrowane (`_SKIP_PATTERNS`: galerie `/multimedia/`, API i
  pliki WordPressa, RSS, kategorie, tagi). Strony, które reguły wykluczają, ingestia
  usuwa z bazy przy starcie. Pomijane są też strony załączników WordPressa (zdjęcie
  lub PDF z podpisem) — rozpoznawane po klasie `<body>`, bo ich adres tego nie zdradza.
- **Ekstrakcja tekstu** usuwa przed trafilaturą baner cookies, nagłówek z menu i
  stopkę, a na stronach z `<main>` analizuje tylko jego zawartość. Ukryty baner
  cookies był wcześniej w ~13% fragmentów, a na krótkich stronach zastępował treść.

Uruchamiaj okresowo (np. cron raz dziennie) gdy treści na stronie się zmieniają.

## 5. Uruchomienie

```bash
python cli.py
```

Wpisz pytanie i naciśnij Enter. Wyjście: `wyjdź` lub Ctrl+C.

> Ollama musi działać (`ollama serve`) i baza musi być wstała (`docker compose up -d db`)
> zanim odpalisz bota.

`cli.py` to tylko pętla terminala: odpowiedź liczy `ChatService` z `usbot/core/chat.py`
i zwraca ją jako strumień zdarzeń (`sources`, `token`, `refused`, `clarify`, `done`) —
ten sam, który API HTTP wysyła przeglądarce. Opis warstw i zdarzeń:
`docs/architektura.md` (2.1).

### API HTTP (strona WWW)

```bash
uvicorn usbot.api.main:app --host 127.0.0.1 --port 8000
```

- `POST /api/chat` — body `{"message": "...", "history": [{"role": "user", "content": "..."}, …]}`,
  odpowiedź jako Server-Sent Events: `sources`, `token`, `done`, `refused`, `clarify`, `error`.
  Serwer jest bezstanowy: historię przysyła klient, serwer bierze z niej `API_HISTORY_TURNS`
  ostatnich tur.
- `GET /api/health` — baza, model embeddingów (i reranker), LLM; `200` albo `503`.
- `GET /api/faculties` — wydziały (np. przyciski po `clarify`).

```bash
curl -N http://127.0.0.1:8000/api/chat -H 'Content-Type: application/json' \
  -d '{"message": "Jak połączyć się z siecią eduroam?", "history": []}'
```

Modele, pula połączeń z bazą i klient LLM-a ładują się raz, przy starcie (kilkadziesiąt
sekund). Rozłączenie klienta przerywa generowanie. Przy vLLM na porcie 8000 uruchom API
na innym (`--port 8080`). Kontrakt, kody błędów, przykład klienta w JS i czasy odpowiedzi:
`docs/api.md` (3.1–3.2).

## Testy

```bash
pip install -r requirements-dev.txt
pytest tests/unit          # bez Dockera, bazy i modeli
pytest tests/integration   # wymaga Dockera: testcontainers uruchamia pgvector/pgvector:pg16
```

Samo `pytest` uruchamia oba zestawy; bez Dockera testy integracyjne są pomijane (skip).
Testy integracyjne używają syntetycznych embeddingów — nie pobierają modelu ani nie
potrzebują bazy z `docker compose`. Plan testów i szczegóły: `docs/testy.md` (6.2–6.3).

## 6. Ewaluacja retrievalu

Mierzy samo wyszukiwanie (bez LLM-a) na zbiorze pytań z `evaluation/queries.jsonl`.
Wymaga tylko bazy, Ollama nie jest potrzebna. Korzysta z tych samych adapterów co
`cli.py` (`usbot.bootstrap.build_retriever()`), ale bez progu odcięcia.

```bash
python evaluation/evaluate.py
python evaluation/evaluate.py --k 1 3 5 10 20 --verbose   # + URL-e dla pudłowanych pytań
python evaluation/evaluate.py --out wyniki.json           # wyniki per pytanie
python evaluation/evaluate.py --threshold-sweep           # skutki progu MAX_DISTANCE 0.30–0.60
python evaluation/evaluate.py --mode hybrid               # tryb wyszukiwania (domyślnie RETRIEVAL_MODE)
python evaluation/evaluate.py --rerank --threshold-sweep  # reranking + próg na odległości i na wyniku rerankera
```

Format jednej linii `queries.jsonl`:

```json
{"id": "eduroam-konfiguracja", "question": "Jak połączyć się z siecią eduroam na uczelni?",
 "relevant": {"https://eduroam.us.edu.pl/konfiguracja/": 2, "https://eduroam.us.edu.pl/": 1}}
```

- `relevant` — strony, które powinny zostać zwrócone: `2` = odpowiada na pytanie,
  `1` = częściowo. Trafność liczona jest po URL-u, bo ID chunków zmieniają się przy re-crawlu.
- Oznaczone strony nie muszą być w bazie: skrypt raportuje pokrycie i dla każdego
  pudła podaje przyczynę — `crawl` (strony nie ma w bazie) albo `retrieval` (jest,
  ale nie została znaleziona).
- Puste `relevant` = pytanie spoza zakresu bazy. Dla takich pytań skrypt pokazuje
  tylko odległość najlepszego wyniku — porównanie z pytaniami „w zakresie" pomaga
  dobrać próg odcięcia.
- `--threshold-sweep` dla progów 0.30–0.60 (co 0.01) pokazuje, ile pytań spoza
  zakresu i w zakresie zostałoby odrzuconych oraz ile trafień by przepadło (w top-`TOP_K`,
  czyli tyle fragmentów, ile dostaje LLM). Uruchom po każdej zmianie bazy lub modelu
  embeddingów i w razie potrzeby popraw `MAX_DISTANCE` — uzasadnienie obecnej wartości
  jest w `docs/testy.md` (6.4). Z `--rerank` sweep pokazuje też próg na wyniku rerankera
  (`MIN_RERANK_SCORE`) oraz Hit@1 i Hit@`TOP_K` po progu, czyli to, co dostaje LLM.
- Odpalaj przed i po każdej zmianie chunkingu, modelu embeddingów czy sposobu
  wyszukiwania. Po re-crawlu sprawdź, czy oznaczone URL-e nadal są w bazie.

## Konfiguracja (`.env`)

Ustawienia wczytuje `usbot/config.py` (pydantic-settings): zmienna środowiskowa ma
pierwszeństwo przed `.env` z katalogu głównego repozytorium. Opcje logiczne włącza tylko
wartość `true`. Pozostałe klucze `.env` (np. `POSTGRES_*` dla Dockera) są ignorowane i
nie trafiają do `os.environ` — zmienne bibliotek (np. `HF_HOME`) ustaw w środowisku.

| Zmienna | Opis | Domyślnie |
|---|---|---|
| `DATABASE_URL` | połączenie do Postgresa | `...@localhost:5433/chatbot` |
| `LLM_BASE_URL` | endpoint modelu (OpenAI-compatible) | `http://127.0.0.1:11434/v1` |
| `LLM_MODEL` | nazwa modelu w Ollama/vLLM | `qwen3:8b` |
| `EMBED_MODEL` | model embeddingów | `BAAI/bge-m3` |
| `TOP_K` | ile fragmentów z bazy trafia do kontekstu | `8` |
| `MAX_DISTANCE` | maks. odległość kosinusowa fragmentu; gdy żaden się nie mieści, bot odpowiada „Nie mam tej informacji…" bez wywołania LLM-a | `0.43` |
| `OLD_NEWS_PENALTY` | kara dodawana do odległości (tylko przy sortowaniu) dla aktualności i wydarzeń starszych niż `OLD_NEWS_DAYS`; `0` = wyłączone | `0` |
| `OLD_NEWS_DAYS` | od ilu dni aktualność/wydarzenie jest „stare" | `730` |
| `MAX_PER_URL` | najwyżej tyle fragmentów z jednej strony w wynikach wyszukiwania; `0` = bez limitu | `2` |
| `RETRIEVAL_MODE` | `vector` — samo wyszukiwanie wektorowe; `hybrid` — wektorowe + pełnotekstowe (`chunks.content_tsv`) połączone przez RRF; porównanie w `docs/testy.md` (6.4) | `vector` |
| `RERANK` | `true` = 20 najlepszych fragmentów ocenia cross-encoder i zostaje `TOP_K`; wymaga GPU (na CPU ~6 s na pytanie) | `false` |
| `RERANK_MODEL` | model rerankera (cross-encoder) | `BAAI/bge-reranker-v2-m3` |
| `MIN_RERANK_SCORE` | przy `RERANK=true` próg zamiast `MAX_DISTANCE`: min. wynik rerankera (0–1) fragmentu | `0.01` |
| `BOILERPLATE_URLS` | fragment o tej samej treści na więcej stronach to boilerplate i nie trafia do bazy; `0` = wyłączone | `20` |
| `ENABLE_THINKING` | tryb rozumowania Qwen3 (wolniejszy); Ollama go ignoruje | `false` |
| `LLM_REASONING_EFFORT` | wysyłane jako `reasoning_effort`; na Ollamie `none` wyłącza rozumowanie Qwen3 (szybszy pierwszy token, `docs/api.md` 3.2); nie ustawiaj dla vLLM | puste |
| `LLM_TIMEOUT` | limit (s) na połączenie z LLM-em i każdy kolejny fragment odpowiedzi | `60` |
| `API_HISTORY_TURNS` | API: ile ostatnich tur historii klienta trafia do `ChatService` | `5` |
| `API_CORS_ORIGINS` | API: dozwolone originy CORS po przecinku; puste = bez CORS | puste |
| `DB_POOL_MIN` / `DB_POOL_MAX` | API: rozmiar puli połączeń z bazą | `1` / `4` |
| `DB_TIMEOUT` | API: limit (s) na połączenie z puli, nawiązanie połączenia i jedno zapytanie | `10` |
| `HEALTH_TIMEOUT` | API: limit (s) jednego sprawdzenia w `/api/health` | `3` |
| `CRAWL_LIMIT` | maks. liczba stron na jeden crawl | `6000` |
| `CRAWL_DEPTH` | głębokość BFS (0 = tylko sitemap + seedy) | `3` |
| `CRAWL_WORKERS` | równoległe requesty HTTP (usnet: maks. 2) | `6` |
| `EMBED_BATCH` | ile stron embedować naraz | `16` |
| `CRAWL_FRESH` | `true` = pełny re-crawl (czyści bazę) | `false` |
| `CRAWL_REFETCH` | `true` = pobierz wszystko od nowa, embeduj tylko zmienione | `false` |

## Uwagi

- `.env` **nie trafia do gita** — współdziel tylko `.env.example`.
- Retrieval bierze pod uwagę kilka ostatnich pytań użytkownika, więc krótkie
  follow-upy („a wnst?", „dziekanatu tam") trafiają we właściwy kontekst. Równolegle
  szuka samym ostatnim pytaniem i łączy wyniki — po zmianie tematu wcześniejsze
  pytania nie psują wyszukiwania.
- W wynikach wyszukiwania **ta sama treść występuje raz** (po `body_hash`, zostaje
  najbliższy fragment; przy remisie — krótszy URL, czyli zwykle oryginał, a nie kopia
  pod `/student/en/`), a jedna strona zajmuje najwyżej `MAX_PER_URL` (2) miejsc.
  Zwolnione miejsca zajmują kolejni kandydaci, więc nadal jest `TOP_K` wyników.
  Po aktualizacji kodu uruchom raz ingestię — migracja dodaje kolumny, z których
  korzysta `cli.py`.
- **Tryb wyszukiwania** wybiera `RETRIEVAL_MODE`. Domyślny `vector` to samo wyszukiwanie
  wektorowe. `hybrid` dokłada wyszukiwanie pełnotekstowe po `chunks.content_tsv`
  (konfiguracja `'simple'` + `unaccent` — Postgres nie ma polskiej; słowa pytania bez
  stop-słów połączone OR, ranking `ts_rank_cd`) i łączy obie listy przez Reciprocal Rank
  Fusion (`Σ 1/(60 + pozycja)`). Próg `MAX_DISTANCE` działa w obu trybach na odległości
  wektorowej. Na zbiorze testowym hybryda nie poprawia tego, co dostaje LLM (Hit@8 po
  progu bez zmian), a obniża Hit@1 — stąd domyślny `vector` (`docs/testy.md`, 6.4).
  Kolumnę `content_tsv` dodaje migracja w ingestii (za pierwszym razem ok. minuty —
  przepisuje tabelę); bez niej tryb `hybrid` nie zadziała.
- **Reranking** (`RERANK=true`): z kandydatów (po deduplikacji i limicie na stronę) 20
  ocenia cross-encoder `BAAI/bge-reranker-v2-m3` i do LLM-a trafia `TOP_K` najlepszych.
  Próg działa wtedy na wyniku rerankera (`MIN_RERANK_SCORE` = 0.01), nie na odległości.
  Na zbiorze testowym trafienie w kontekście LLM-a (top-8 po progu) ma 50/56 pytań zamiast
  45/56, żadne pytanie w zakresie nie jest odrzucane, a pytań spoza zakresu odrzuconych
  jest 10/18 zamiast 8/18 (`docs/testy.md`, 6.4). Reranking 20 par trwa ~90 ms na GPU
  (fp16), ale ~6 s na CPU — dlatego jest domyślnie wyłączony. Model (~2.3 GB) pobierz raz
  z `HF_HUB_OFFLINE=0`. Zalecane połączenie: `RERANK=true` z `RETRIEVAL_MODE=vector`.
- Gdy żaden fragment nie jest bliżej niż `MAX_DISTANCE`, bot od razu odpowiada
  „Nie mam tej informacji – sprawdź na us.edu.pl lub skontaktuj się z dziekanatem."
  i nie woła modelu — zamiast dawać mu niepasujący kontekst, na którym zmyśla. Próg
  odrzuca ok. połowę pytań spoza zakresu; najtrudniejsze są pytania o inne uczelnie
  (np. dziekanat Politechniki), które przechodzą i trafiają do LLM-a.
- Przy pytaniu o **dziekanat** bez podania wydziału (w 3 ostatnich pytaniach) bot
  dopytuje, o który wydział chodzi, i wymienia dostępne — zamiast zgadywać. Regułę
  sprawdza `ChatService` (zdarzenie `clarify`, bez wywołania LLM-a); ta sama instrukcja
  jest też w prompcie systemowym na sformułowania, których reguła nie rozpozna.
  Skrót wydziału w pytaniu („WNP”, „wnst”) jest w zapytaniu do wyszukiwarki rozwijany
  do pełnej nazwy (`expand_faculties()`), więc odpowiedź samym skrótem trafia we
  właściwy dziekanat.
- Jeśli strona zniknie z serwisu (404), jej stare fragmenty zostają w bazie — okresowo
  odpalaj `CRAWL_FRESH=true`, by wyczyścić nieaktualne treści. To samo dotyczy stron,
  dla których nowa ekstrakcja daje pusty tekst, i stron, do których przebieg nie
  doszedł przed `CRAWL_LIMIT`.
- Pełny `CRAWL_REFETCH` (~12 tys. stron) trwa ok. 1,5 h — ogranicza go czas
  odpowiedzi serwera (aktualności ~1 s, usnet ~11 s), nie GPU.
