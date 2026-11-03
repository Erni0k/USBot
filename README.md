# US Chatbot
Docs: https://erni0k.github.io/USBot/
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
       ├─ próg odległości: nic dość bliskiego → stała odpowiedź, bez LLM-a
       └─ generacja: Ollama / vLLM (Qwen3, API OpenAI-compatible)
```

## Struktura

```
us-chatbot/
├── cli.py              # aplikacja terminalowa (punkt wejścia)
├── requirements.txt    # zależności Python
├── ingestion/
│   ├── ingest.py       # pipeline: crawl → chunking → embeddingi → pgvector
│   └── scrape.py       # inkrementalny crawler BFS (stały pool żądań, limit na domenę)
├── evaluation/
│   ├── evaluate.py     # metryki retrievalu: Hit/Recall/Precision/nDCG@k, MRR, dobór progu
│   └── queries.jsonl   # pytania testowe z oznaczonymi URL-ami
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
Po zmianie sposobu ekstrakcji tekstu użyj `CRAWL_REFETCH=true` — pobiera wszystkie
strony od nowa (bez 304), ale embeddingi liczy tylko dla stron, których tekst się zmienił.

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

## 6. Ewaluacja retrievalu

Mierzy samo wyszukiwanie (bez LLM-a) na zbiorze pytań z `evaluation/queries.jsonl`.
Wymaga tylko bazy, Ollama nie jest potrzebna.

```bash
python evaluation/evaluate.py
python evaluation/evaluate.py --k 1 3 5 10 20 --verbose   # + URL-e dla pudłowanych pytań
python evaluation/evaluate.py --out wyniki.json           # wyniki per pytanie
python evaluation/evaluate.py --threshold-sweep           # skutki progu MAX_DISTANCE 0.30–0.60
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
  jest w `docs/testy.md` (6.4).
- Odpalaj przed i po każdej zmianie chunkingu, modelu embeddingów czy sposobu
  wyszukiwania. Po re-crawlu sprawdź, czy oznaczone URL-e nadal są w bazie.

## Konfiguracja (`.env`)

| Zmienna | Opis | Domyślnie |
|---|---|---|
| `DATABASE_URL` | połączenie do Postgresa | `...@localhost:5433/chatbot` |
| `LLM_BASE_URL` | endpoint modelu (OpenAI-compatible) | `http://localhost:11434/v1` |
| `LLM_MODEL` | nazwa modelu w Ollama/vLLM | `qwen3:8b` |
| `EMBED_MODEL` | model embeddingów | `BAAI/bge-m3` |
| `TOP_K` | ile fragmentów z bazy trafia do kontekstu | `8` |
| `MAX_DISTANCE` | maks. odległość kosinusowa fragmentu; gdy żaden się nie mieści, bot odpowiada „Nie mam tej informacji…" bez wywołania LLM-a | `0.43` |
| `ENABLE_THINKING` | tryb rozumowania Qwen3 (wolniejszy) | `false` |
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
- Gdy żaden fragment nie jest bliżej niż `MAX_DISTANCE`, bot od razu odpowiada
  „Nie mam tej informacji – sprawdź na us.edu.pl lub skontaktuj się z dziekanatem."
  i nie woła modelu — zamiast dawać mu niepasujący kontekst, na którym zmyśla. Próg
  odrzuca ok. połowę pytań spoza zakresu; najtrudniejsze są pytania o inne uczelnie
  (np. dziekanat Politechniki), które przechodzą i trafiają do LLM-a.
- Przy pytaniu o **dziekanat** (godziny, kontakt) bez podania wydziału bot dopytuje,
  o który wydział chodzi, i wymienia dostępne — zamiast zgadywać.
- Jeśli strona zniknie z serwisu (404), jej stare fragmenty zostają w bazie — okresowo
  odpalaj `CRAWL_FRESH=true`, by wyczyścić nieaktualne treści. To samo dotyczy stron,
  dla których nowa ekstrakcja daje pusty tekst, i stron, do których przebieg nie
  doszedł przed `CRAWL_LIMIT`.
- Pełny `CRAWL_REFETCH` (~12 tys. stron) trwa ok. 1,5 h — ogranicza go czas
  odpowiedzi serwera (aktualności ~1 s, usnet ~11 s), nie GPU.
