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

- **Pokrycie bazy jest głównym problemem** (stan przed poprawkami crawlera — patrz niżej). Tylko 27 z 64 najlepszych odpowiedzi jest
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

**Po poprawkach crawlera** (2026-11-02): kolejka priorytetowa dla portalu studenta,
usnet i eduroam; ekstrakcja bez banera cookies, menu i stopki; usunięcie 3 421 stron
galerii `/multimedia/` (~25% fragmentów). Pomiar na **częściowej** ingestii — ponownie
pobrano tylko kolejkę priorytetową (~230 stron), reszta bazy ma jeszcze starą ekstrakcję
(ok. 2 tys. fragmentów z banerem cookies). 32 290 fragmentów z 6 846 stron.

| k | Hit@k | Recall@k | Prec@k | nDCG@k | Hit@k (odp. w bazie) |
|---|---|---|---|---|---|
| 1 | 0.500 (+0.19) | 0.221 | 0.500 | 0.444 (+0.22) | 0.545 |
| 3 | 0.646 (+0.23) | 0.342 | 0.382 | 0.368 | 0.705 |
| 5 | 0.688 (+0.21) | 0.423 | 0.325 | 0.403 (+0.21) | 0.750 |
| 10 | 0.771 (+0.23) | 0.527 | 0.235 | 0.432 | 0.841 |

MRR@10 = 0.581 (było 0.384). Pokrycie: 57/67 najlepszych odpowiedzi w bazie (było
27/64); brakujące to podstrony `student/studia/tok-studiow-2/` (urlopy, przeniesienie,
zmiana formy), do których częściowa ingestia nie doszła. Wzrosła też kolumna
„odp. w bazie" (Hit@5 0.62 → 0.75) — mniej szumu z galerii i banera pomaga samemu
wyszukiwaniu, nie tylko pokryciu. Etykiety uzupełniono o strony znalezione po
poprawce (WPiA/WTL kontakt, usnet `siec-bezprzewodowa`); stron tych nie było w
starej bazie, więc wynik bazowy się nie zmienia. Próg 0.42 nadal odrzuca 6/8 pytań
spoza zakresu bez utraty trafień.

**Wynik bazowy 1a — pełna ingestia** (2026-11-02; bge-m3, chunk 800/120, samo
wyszukiwanie wektorowe, **39 003 fragmentów z 12 020 stron**). Punkt odniesienia dla
kolejnych zmian; wyniki per pytanie w `baseline-1a.json`. Cała baza ma już nową
ekstrakcję: pełny przebieg `CRAWL_REFETCH=true CRAWL_LIMIT=12000` (9 001 stron
zmienionych, 2 998 bez zmian), po nim usunięcie 554 stron, do których przebieg nie
doszedł, a które były stronami załączników WordPressa (492) albo zwracały już 404 (62),
i ponowne pobranie 16 pozostałych. W bazie nie ma fragmentów z banerem cookies — trzy
trafienia frazy „przywiązuje dużą wagę do poszanowania prywatności" to treść stron
polityki prywatności.

| k | Hit@k | Recall@k | Prec@k | nDCG@k | Hit@k (odp. w bazie) |
|---|---|---|---|---|---|
| 1 | 0.500 | 0.247 | 0.500 | 0.444 | 0.500 |
| 3 | 0.729 (+0.08) | 0.409 | 0.382 | 0.422 (+0.05) | 0.729 |
| 5 | 0.792 (+0.10) | 0.541 | 0.333 | 0.485 (+0.08) | 0.792 |
| 10 | 0.917 (+0.15) | 0.646 | 0.250 | 0.519 (+0.09) | 0.917 |

Zmiany w nawiasach względem pomiaru na częściowej ingestii (tabela wyżej).
MRR@10 = 0.630 (było 0.581). Pokrycie: 94/95 oznaczonych stron i 67/67 najlepszych
odpowiedzi; 48/48 pytań ma w bazie co najmniej jedną trafną stronę, dlatego obie
kolumny Hit@k są równe. Brakuje tylko martwego adresu `…/dziekanat-2/` (404).

- **Pudła są już tylko po stronie wyszukiwania.** Bez trafienia w top-10 zostały
  4 pytania: `niepelnosprawnosc`, `rzecznik-praw-studenta`, `wydzialy`,
  `kampus-cieszyn` — trafne strony są w bazie, ale przegrywają z podobnymi.
- **Angielskie wersje portalu.** Pod `/student/en/` jest 579 stron z angielskim
  tłumaczeniem portalu studenta. bge-m3 jest wielojęzyczny, więc tłumaczenie trafia
  tuż obok polskiego oryginału i w top-10 zajmuje miejsca innym stronom (np. pytanie
  o niepełnosprawność: 3 z 9 różnych stron w top-10 to `/student/en/`).
- **Stare aktualności.** Przy ogólnych pytaniach (`wydzialy`) top-10 wypełniają
  wpisy o rekrutacji z lat 2017–2021 zamiast stron struktury uczelni.
- **Próg odcięcia.** Odległość top-1: w zakresie 0.191–0.406 (mediana 0.291), spoza
  zakresu 0.360–0.542 (mediana 0.477). Przy progu 0.42 odrzucone zostaje 6/8 pytań
  spoza zakresu i żadne w zakresie; przy 0.40 — 6/8 i jedno w zakresie (bez trafienia
  w top-10). Najbliżej bazy nadal są pytania o inne uczelnie (Politechnika 0.360,
  SUM 0.398).

!!! warning "Status"
    Katalog `tests/` jest **planowany** — testy zostaną zaimplementowane w kolejnych
    iteracjach. Powyższe komendy zakładają standardową strukturę pytest.
