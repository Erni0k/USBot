# 1. Opis projektu

## 1.1 Cel aplikacji

USBot to chatbot terminalowy stworzony dla społeczności akademickiej Uniwersytetu
Śląskiego w Katowicach. Rozwiązuje problem trudnego dostępu do informacji
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
- Jako **użytkownik zaawansowany** chcę włączyć tryb rozumowania
  (`ENABLE_THINKING`), aby uzyskać bardziej przemyślane odpowiedzi kosztem czasu.
- Jako **użytkownik** chcę, aby bot odmówił odpowiedzi spoza zakresu wiedzy
  uczelnianej i wyraźnie poinformował o braku informacji.
- Jako **użytkownik** chcę zakończyć sesję wpisując `wyjdź` lub naciskając `Ctrl+C`.

## 1.3 Wymagania niefunkcjonalne

| Kategoria | Wymaganie | Wartość / parametr |
|---|---|---|
| Wydajność | Czas odpowiedzi modelu (Ollama, GPU) | < 10 s dla typowego pytania |
| Wydajność | Czas pierwszego uruchomienia (pobranie `BAAI/bge-m3`) | Jednorazowo ~5 min (~2 GB) |
| Wydajność | Crawl inkrementalny — kolejne przebiegi | Pobiera tylko zmienione strony |
| Bezpieczeństwo | Klucze i hasła | Wyłącznie w `.env` (nie trafia do gita) |
| Bezpieczeństwo | Zakres odpowiedzi | Bot odpowiada wyłącznie na podstawie pobranego kontekstu |
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
| `title` | `TEXT` | | Tytuł strony lub sekcji |
| `content` | `TEXT` | NOT NULL | Treść fragmentu tekstowego |
| `embedding` | `VECTOR(1024)` | NOT NULL | Wektor embeddingu (`BAAI/bge-m3`) |
| `created_at` | `TIMESTAMPTZ` | DEFAULT now() | Data dodania rekordu |

Indeksy: `chunks_embedding_idx` (HNSW, `vector_cosine_ops`) pod wyszukiwanie
kosinusowe oraz `chunks_url_idx` (po kolumnie `url`) pod szybką aktualizację
fragmentów danej strony przy re-crawlu.

### Tabela: `pages`

| Kolumna | Typ | Ograniczenia | Opis |
|---|---|---|---|
| `url` | `TEXT` | PRIMARY KEY | Adres strony |
| `etag` | `TEXT` | | Nagłówek `ETag` z ostatniego pobrania |
| `last_modified` | `TEXT` | | Nagłówek `Last-Modified` z ostatniego pobrania |
| `content_hash` | `TEXT` | | SHA-256 wyekstrahowanego tekstu |
| `links` | `JSONB` | | Linki znalezione na stronie (do BFS przy 304) |
| `last_crawled` | `TIMESTAMPTZ` | DEFAULT now() | Data ostatniego odwiedzenia |

**Relacje:** tabele nie mają kluczy obcych; powiązane są logicznie przez kolumnę
`url`. Wyszukiwanie semantyczne realizowane jest zapytaniem cosinus-podobieństwa
na kolumnie `embedding` przy użyciu operatora `<=>` (pgvector). Parametr `TOP_K`
(domyślnie 8) steruje liczbą zwracanych fragmentów.

## 1.5 Makiety UI

Aplikacja posiada interfejs terminalowy (CLI). Schemat sesji:

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
