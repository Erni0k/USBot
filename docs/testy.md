# 6. Testy

## 6.1 Narzędzia testowe

- **pytest** — framework do testów jednostkowych i integracyjnych w Pythonie.
- **pytest-mock / unittest.mock** — mockowanie wywołań API i połączeń z bazą danych.
- **Postman / curl** — manualne testy API modelu językowego.
- **Docker Compose** — izolowana baza danych do testów integracyjnych.

## 6.2 Plan testów

| ID | Typ | Testowany moduł | Opis | Oczekiwany wynik |
|---|---|---|---|---|
| T01 | Jednostkowy | `embed()` | Weryfikacja kształtu wektora | Lista 1024 floatów |
| T02 | Jednostkowy | `build_context()` | Pusta lista chunks | String informujący o braku danych |
| T03 | Jednostkowy | `build_context()` | Lista 2 chunks z URL | Połączony tekst z nagłówkami `[Źródło: ...]` |
| T04 | Jednostkowy | `build_query()` | Historia z kilkoma turami | Sklejone ostatnie pytania użytkownika |
| T05 | Integracyjny | `retrieve()` | Pytanie z danymi w bazie | Zwrócenie `TOP_K` rekordów |
| T06 | Integracyjny | `scrape.crawl()` | Strona zwraca 304 | Status `unchanged`, brak re-embeddingu |
| T07 | Integracyjny | `ingest._flush()` | Zmieniona strona | Podmiana fragmentów + wpis w `pages` |
| T08 | E2E | `cli.main()` | Wpisanie `wyjdź` | `sys.exit(0)`, brak wyjątku |
| T09 | E2E | `cli.main()` | Symulacja błędu bazy | Komunikat błędu, kontynuacja pętli |
| T10 | Manualny | API LLM | Pytanie o godziny dziekanatu | Odpowiedź po polsku z URL |

## 6.3 Instrukcja uruchomienia testów

```bash
# Instalacja zależności testowych
pip install pytest pytest-mock

# Wszystkie testy
pytest tests/ -v

# Testy jednostkowe (bez bazy)
pytest tests/unit/ -v

# Testy integracyjne (wymaga uruchomionego Dockera)
docker compose up -d db
pytest tests/integration/ -v
docker compose down
```

## 6.4 Ewaluacja retrievalu

Niezależnie od testów kodu mierzymy jakość samego wyszukiwania (bez LLM-a) skryptem
`evaluation/evaluate.py` na zbiorze `evaluation/queries.jsonl`: 48 pytań studenckich
z ręcznie oznaczonymi stronami (ocena `2` = odpowiada na pytanie, `1` = częściowo)
oraz 8 pytań spoza zakresu (pogoda, inna uczelnia, prompt injection).

Oznaczanie łączyło kandydatów z wyszukiwania wektorowego, wyszukiwania po słowach
kluczowych i ręcznego przejrzenia serwisu `us.edu.pl/student/` — żeby etykiety nie
faworyzowały ocenianego retrievera. Trafność liczona jest po URL-u.

| Metryka | Znaczenie |
|---|---|
| Hit@k | odsetek pytań z co najmniej jedną trafną stroną w top-k |
| Recall@k | jaka część oznaczonych stron trafiła do top-k |
| Precision@k | jaka część z k zwróconych fragmentów jest trafna |
| nDCG@k | jakość kolejności z uwzględnieniem ocen 2/1 (duplikaty strony liczone raz) |
| MRR | średnia z 1/pozycja pierwszego trafienia |

**Wyniki bazowe** (2026-11-02; bge-m3, chunk 800/120, samo wyszukiwanie wektorowe,
41 826 fragmentów z 10 053 stron):

| k | Hit@k | Recall@k | Prec@k | nDCG@k | Hit@k (odp. w bazie) |
|---|---|---|---|---|---|
| 1 | 0.312 | 0.139 | 0.312 | 0.229 | 0.405 |
| 3 | 0.417 | 0.209 | 0.208 | 0.188 | 0.541 |
| 5 | 0.479 | 0.232 | 0.163 | 0.195 | 0.622 |
| 10 | 0.542 | 0.265 | 0.113 | 0.205 | 0.703 |

MRR@10 = 0.384. Odległość kosinusowa top-1: pytania w zakresie 0.214–0.514
(mediana 0.344), spoza zakresu 0.378–0.553 (mediana 0.460).

Wnioski:

- **Pokrycie bazy jest głównym problemem.** Tylko 27 z 64 najlepszych odpowiedzi jest
  w bazie, a 11 z 48 pytań nie ma w niej żadnej trafnej strony. Portal studenta
  `us.edu.pl/student/` (stypendia, akademiki, urlopy, ELS) nie występuje w głównej
  sitemapie i crawler go nie odwiedza. Brakuje też `eduroam.us.edu.pl/konfiguracja`
  i podstron `usnet.us.edu.pl` z kontami studenckimi.
- **Część stron jest w bazie, ale bez treści.** Strony dziekanatów WPiA i WTL mają
  w bazie wyłącznie tekst banera cookies — ekstrakcja treści zawiodła.
- **Martwe seedy:** `/wydzialy/` i stary adres dziekanatu WNS (`…/dziekanat-2/`)
  zwracają dziś 404.
- **Próg odcięcia:** rozkłady odległości dla pytań w zakresie i spoza zakresu się
  nakładają, więc sam próg nie odfiltruje wszystkiego. Przy progu 0.42 odrzucone
  zostałoby 6/8 pytań spoza zakresu i 5/48 w zakresie — żadne z tych 5 nie miało
  trafnej strony w top-10, więc nic nie tracimy. Przy 0.40 tracimy już jedno trafienie.
  Pytania najbliższe bazie to te o innych uczelniach (Politechnika 0.378, SUM 0.397).
  Próbka jest mała — przed wdrożeniem progu warto rozszerzyć zbiór.

!!! warning "Status"
    Katalog `tests/` jest **planowany** — testy zostaną zaimplementowane w kolejnych
    iteracjach. Powyższe komendy zakładają standardową strukturę pytest.
