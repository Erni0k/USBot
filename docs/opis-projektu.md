# 1. Opis projektu

## 1.1 Cel aplikacji

USBot to chatbot stworzony dla społeczności akademickiej Uniwersytetu Śląskiego w
Katowicach — dostępny w terminalu i przez API HTTP, na którym powstaje strona WWW dla
studentów. Rozwiązuje problem trudnego dostępu do informacji
rozproszonych na stronie us.edu.pl — zamiast ręcznie przeszukiwać dziesiątki
podstron, użytkownik może zadać pytanie w języku naturalnym i otrzymać zwięzłą
odpowiedź wraz ze źródłowym adresem URL.

Grupą docelową są studenci i pracownicy uczelni szukający informacji o rekrutacji,
wydziałach, harmonogramach, dziekanatach oraz innych zagadnieniach
administracyjnych. Główną korzyścią jest natychmiastowy, kontekstowy dostęp do
treści uczelnianej bez konieczności przeglądania wielu stron internetowych.

## 1.2 Wymagania funkcjonalne

Każda funkcja opisana jest w formacie *User Story*:

- Jako **student** chcę zadać pytanie w języku polskim, aby uzyskać odpowiedź
  dotyczącą spraw uczelnianych bez przeglądania strony us.edu.pl.
- Jako **użytkownik** chcę, aby bot podawał adres URL źródła informacji, żebym mógł
  samodzielnie zweryfikować odpowiedź.
- Jako **użytkownik** chcę zadawać krótkie pytania uzupełniające (np. „a wnst?"),
  aby kontynuować temat — bot uwzględnia kontekst poprzednich pytań przy wyszukiwaniu.
- Jako **student** chcę, aby przy pytaniu o dziekanat bez podania wydziału bot
  dopytał o który wydział chodzi, aby nie dostać godzin niewłaściwego dziekanatu.
- Jako **administrator** chcę uruchamiać skrypt ingestii danych, aby baza wiedzy
  bota była aktualna po zmianach na stronie uczelni.
- Jako **administrator** chcę, aby ponowna ingestia pobierała tylko zmienione strony,
  aby aktualizacja była szybka (crawl inkrementalny).
- Jako **student** chcę korzystać z bota w przeglądarce i widzieć odpowiedź na bieżąco
  (token po tokenie) wraz z listą źródeł — API HTTP strumieniuje zdarzenia SSE.
- Jako **twórca strony WWW** chcę bezstanowego API z jasnym kontraktem zdarzeń
  (`sources`, `token`, `done`, `refused`, `clarify`, `error`), listą wydziałów do
  przycisków i sprawdzeniem stanu usług (`/api/health`).
- Jako **użytkownik zaawansowany** chcę włączyć tryb rozumowania
  (`ENABLE_THINKING`; na Ollamie sterowany przez `LLM_REASONING_EFFORT`), aby uzyskać
  bardziej przemyślane odpowiedzi kosztem czasu.
- Jako **użytkownik** chcę, aby bot odmówił odpowiedzi spoza zakresu wiedzy
  uczelnianej i wyraźnie poinformował o braku informacji.
- Jako **użytkownik** chcę zakończyć sesję wpisując `wyjdź` lub naciskając `Ctrl+C`.

## 1.3 Wymagania niefunkcjonalne

| Kategoria | Wymaganie | Wartość / parametr |
|---|---|---|
| Wydajność | Czas odpowiedzi (Ollama, GPU, `qwen3:8b`) | < 10 s dla typowego pytania; zmierzone przez API: pierwszy token 0.8 s, całość 6.4 s (mediana) z `LLM_REASONING_EFFORT=none` — bez tego 6.5–8 s i 14–16 s ([API](api.md), 3.2) |
| Wydajność | Wyszukiwanie (embedding pytania, SQL, próg) | ~0.1 s |
| Wydajność | Czas pierwszego uruchomienia (pobranie `BAAI/bge-m3`) | Jednorazowo ~5 min (~2 GB) |
| Wydajność | Reranking (opcjonalny, `RERANK=true`) | ~90 ms na pytanie na GPU (20 fragmentów, fp16); na CPU ~6 s — tylko z GPU |
| Wydajność | Crawl inkrementalny — kolejne przebiegi | Pobiera tylko zmienione strony |
| Bezpieczeństwo | Klucze i hasła | Wyłącznie w `.env` (nie trafia do gita) |
| Bezpieczeństwo | Zakres odpowiedzi | Bot odpowiada wyłącznie na podstawie pobranego kontekstu |
| Bezpieczeństwo | API HTTP | Walidacja i limity wielkości żądań; brak uwierzytelniania i limitu zapytań — w sieci tylko za reverse proxy |
| Niezawodność | API HTTP | Limity czasu na LLM (`LLM_TIMEOUT`) i bazę (`DB_TIMEOUT`); błąd kończy strumień zdarzeniem `error` |
| Kompatybilność | System operacyjny | Linux, macOS, Windows (Python 3.11+) |
| Kompatybilność | Interpreter | Python 3.11 lub nowszy |
| Środowisko | Baza danych | PostgreSQL 16 z rozszerzeniem pgvector |
| Środowisko | Konteneryzacja | Docker + Docker Compose |
| Interfejs | Kodowanie znaków | UTF-8 (obsługa polskich znaków na każdym systemie) |

## 1.4 Model danych

Baza danych PostgreSQL z rozszerzeniem pgvector przechowuje **dwie tabele**:
`chunks` (fragmenty wiedzy + embeddingi) oraz `pages` (stan crawlera dla
aktualizacji inkrementalnej).

### Tabela: `chunks`

| Kolumna | Typ | Ograniczenia | Opis |
|---|---|---|---|
| `id` | `BIGSERIAL` | PRIMARY KEY | Unikalny identyfikator fragmentu |
| `url` | `TEXT` | NOT NULL | Adres strony źródłowej |
| `title` | `TEXT` | | Tytuł strony (`<title>` bez „\| Uniwersytet Śląski w Katowicach") |
| `page_type` | `TEXT` | | Typ strony: `student`, `wydzial`, `static`, `news`, `event` |
| `published_at` | `TIMESTAMPTZ` | | Data publikacji (`article:published_time`, `<time>` lub data z treści) |
| `content` | `TEXT` | NOT NULL | Treść fragmentu z prefiksem `<tytuł strony> > <nagłówek sekcji>` |
| `content_hash` | `TEXT` | | SHA-256 `content` po ujednoliceniu białych znaków; klucz cache'u embeddingów |
| `body_hash` | `TEXT` | | To samo bez prefiksu; ta sama treść na różnych stronach (boilerplate, duplikaty w wynikach) |
| `embedding` | `VECTOR(1024)` | NOT NULL | Wektor embeddingu (`BAAI/bge-m3`) |
| `created_at` | `TIMESTAMPTZ` | DEFAULT now() | Data dodania rekordu |
| `content_tsv` | `TSVECTOR` | GENERATED ALWAYS … STORED | `to_tsvector('simple', immutable_unaccent(content))` — wyszukiwanie pełnotekstowe (`RETRIEVAL_MODE=hybrid`) |

Indeksy: `chunks_embedding_idx` (HNSW, `vector_cosine_ops`) pod wyszukiwanie
kosinusowe, `chunks_url_idx` (po kolumnie `url`) pod szybką aktualizację
fragmentów danej strony przy re-crawlu oraz `chunks_content_hash_idx` i
`chunks_body_hash_idx` (cache embeddingów, usuwanie boilerplate'u) oraz
`chunks_content_tsv_idx` (GIN, operator `@@`) pod wyszukiwanie pełnotekstowe.

### Tabela: `pages`

| Kolumna | Typ | Ograniczenia | Opis |
|---|---|---|---|
| `url` | `TEXT` | PRIMARY KEY | Adres strony |
| `etag` | `TEXT` | | Nagłówek `ETag` z ostatniego pobrania |
| `last_modified` | `TEXT` | | Nagłówek `Last-Modified` z ostatniego pobrania |
| `content_hash` | `TEXT` | | SHA-256 wyekstrahowanego tekstu i metadanych strony |
| `links` | `JSONB` | | Linki znalezione na stronie (do BFS przy 304) |
| `chunker_version` | `INT` | | `CHUNKER_VERSION`, którą podzielono stronę; inna wymusza ponowny podział i embedding |
| `chunk_hashes` | `TEXT[]` | | `body_hash` wszystkich fragmentów strony, także pominiętych jako boilerplate (indeks GIN `pages_chunk_hashes_idx`) |
| `last_crawled` | `TIMESTAMPTZ` | DEFAULT now() | Data ostatniego odwiedzenia |

**Relacje:** tabele nie mają kluczy obcych; powiązane są logicznie przez kolumnę
`url`. Wyszukiwanie semantyczne realizowane jest zapytaniem cosinus-podobieństwa
na kolumnie `embedding` przy użyciu operatora `<=>` (pgvector). Parametr `TOP_K`
(domyślnie 8) steruje liczbą zwracanych fragmentów. W trybie `hybrid` dochodzi
zapytanie pełnotekstowe na `content_tsv`, a wyniki obu łączy Reciprocal Rank Fusion.
Z `RERANK=true` 20 najlepszych fragmentów ocenia poza bazą cross-encoder
(`BAAI/bge-reranker-v2-m3`) i zostaje `TOP_K` z najwyższym wynikiem.

Kolumny `page_type`, `published_at`, `content_hash`, `body_hash`, `content_tsv`,
`chunker_version` i `chunk_hashes` (wraz z rozszerzeniem `unaccent` i funkcją
`immutable_unaccent()`) dodaje migracja w `_ensure_schema()` (`ingestion/ingest.py`, `ALTER TABLE
… ADD COLUMN IF NOT EXISTS`; hashe istniejących fragmentów uzupełnia przy pierwszym
uruchomieniu) — `infra/init.sql` wykonuje się tylko przy pierwszym starcie kontenera.

## 1.5 Makiety UI

Aplikacja posiada interfejs terminalowy (CLI) i API HTTP dla strony WWW (interfejs
przeglądarkowy jest w przygotowaniu — kontrakt zdarzeń: [API](api.md), 3.1). Schemat
sesji w terminalu:

```text
============================================================
   Bot Uniwersytetu Śląskiego w Katowicach
   Wpisz 'wyjdź' lub Ctrl+C aby zakończyć
============================================================

Ty: Jakie są godziny pracy dziekanatu WNS?

Bot: Dziekanat Wydziału Nauk Społecznych przyjmuje studentów...
Źródło: [https://us.edu.pl/wydzial/wns/dziekanat/]

Ty: wyjdź
Do widzenia!
```

Stany logiczne interfejsu: (a) powitanie, (b) oczekiwanie na pytanie,
(c) generowanie odpowiedzi (streaming), (d) błąd bazy danych, (e) brak informacji
w kontekście, (f) zamknięcie sesji.
