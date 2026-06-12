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

!!! warning "Status"
    Katalog `tests/` jest **planowany** — testy zostaną zaimplementowane w kolejnych
    iteracjach. Powyższe komendy zakładają standardową strukturę pytest.
