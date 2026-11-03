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
| T05 | Integracyjny | `retrieve()` | Pytanie z danymi w bazie | Do `TOP_K` rekordów, żaden dalszy niż `MAX_DISTANCE` |
| T06 | Integracyjny | `scrape.crawl()` | Strona zwraca 304 | Status `unchanged`, brak re-embeddingu |
| T07 | Integracyjny | `ingest._flush()` | Zmieniona strona | Podmiana fragmentów + wpis w `pages` |
| T08 | E2E | `cli.main()` | Wpisanie `wyjdź` | `sys.exit(0)`, brak wyjątku |
| T09 | E2E | `cli.main()` | Symulacja błędu bazy | Komunikat błędu, kontynuacja pętli |
| T10 | Manualny | API LLM | Pytanie o godziny dziekanatu | Odpowiedź po polsku z URL |
| T11 | E2E | `cli.main()` | Pytanie spoza zakresu (`retrieve_for_chat()` zwraca pustą listę) | Stały tekst „Nie mam tej informacji…”, brak wywołania LLM |
| T12 | Integracyjny | `retrieve_for_chat()` | Follow-up „a wnst?” po pytaniu o dziekanat; pytanie w zakresie po kilku spoza zakresu | Oba nieodrzucone, trafne strony w wynikach |

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
oraz 18 pytań spoza zakresu (pogoda, inne uczelnie, wiedza ogólna, small talk, prośby
o kod, prompt injection). Pierwsze 8 pytań spoza zakresu powstało razem ze zbiorem,
10 kolejnych dodano przy doborze progu odcięcia (patrz niżej).

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

**Próg odcięcia `MAX_DISTANCE`** (2026-11-03; baza jak w wyniku 1a, k = `TOP_K` = 8).
`retrieve()` zawsze zwracał `TOP_K` fragmentów, także gdy żaden nie pasował — LLM
dostawał wtedy niepasujący kontekst i zmyślał. Teraz fragmenty dalsze niż
`MAX_DISTANCE` są odrzucane (w Pythonie, po `ORDER BY … LIMIT` — przy indeksie HNSW
warunek `WHERE` na odległość może zwrócić za mało wyników), a gdy nie zostanie żaden,
bot odpowiada stałym tekstem bez wywołania LLM-a.

Do zbioru dodano 10 pytań spoza zakresu, głównie „trudnych” — tematycznie bliskich
uczelni: rekrutacja na UE Katowice, egzaminy na ASP, akademik AWF, Erasmus z UJ,
biblioteka Uniwersytetu Wrocławskiego, matura, płaca minimalna, PIT, zapytanie SQL
o średnią ocen, small talk. Przed dodaniem sprawdzono top-3 wyniki każdego z nich:
baza zawiera strony podobne tematycznie (akademiki UŚ, Erasmus na UŚ, porady
maturalne), ale żadna nie odpowiada na pytanie. Etykiet istniejących pytań nie
zmieniano.

Wynik `python evaluation/evaluate.py --threshold-sweep` (fragment): pytanie jest
odrzucane, gdy nawet najlepszy fragment jest dalej niż próg; „strata” = odrzucone
pytanie w zakresie, które miało trafną stronę w top-8; „utracone łącznie” dolicza
pytania nieodrzucone, którym próg odciął wszystkie trafne fragmenty.

| Próg | Spoza zakresu odrzucone | W zakresie odrzucone | W tym strata | Trafienia utracone łącznie |
|---|---|---|---|---|
| 0.36 | 15/18 | 6/48 | 3 | 5 |
| 0.38 | 15/18 | 3/48 | 1 | 2 |
| 0.39 | 14/18 | 1/48 | 0 | 2 |
| 0.40 | 11/18 | 1/48 | 0 | 1 |
| 0.41 | 10/18 | 0/48 | 0 | 1 |
| 0.42 | 10/18 | 0/48 | 0 | 1 |
| **0.43** | **9/18** | **0/48** | **0** | **0** |
| 0.44 | 6/18 | 0/48 | 0 | 0 |
| 0.46 | 4/18 | 0/48 | 0 | 0 |
| 0.55 | 0/18 | 0/48 | 0 | 0 |

Odległość top-1: w zakresie 0.191–0.406 (mediana 0.291), spoza zakresu 0.347–0.542
(mediana 0.426). Na starych 8 pytaniach spoza zakresu próg 0.42 nadal odrzuca 6/8 bez
utraty trafień — wcześniejszy pomiar się potwierdza.

**Wybrano 0.43:**

- To najniższy próg bez żadnych strat: 0/48 pytań w zakresie odrzuconych i żadne
  trafienie nie zostaje odcięte. Przy 0.41–0.42 przepada trafny fragment pytania
  `thunderbird` (odległość 0.4296; top-1 tego pytania, 0.387, jest nietrafny), przy
  0.39–0.40 odrzucane jest już pytanie `kampus-cieszyn` (top-1 0.406).
- Margines nad najdalszym pytaniem w zakresie (0.406) wynosi 0.024. Zbiór ma tylko 48
  pytań, więc prawdziwe pytania sformułowane inaczej mogą wypaść dalej — niższy próg
  dawałby zerowy margines.
- Follow-upy (patrz niżej) mają większe odległości niż pierwsze pytanie w rozmowie:
  „nie działa mi, co robić?” po dwóch pytaniach o eduroam ma 0.424 — próg 0.42 by go
  odrzucił.
- Koszt względem 0.42: jedno pytanie spoza zakresu mniej odrzucone (small talk
  „Cześć, jak się dzisiaj masz?”, 0.420) — na nie LLM i tak odpowie nieszkodliwie.

Próg odrzuca połowę pytań spoza zakresu (9/18). Przechodzą wszystkie 7 pytań o inne
uczelnie, pytanie o maturę (0.347–0.403) oraz small talk — rozkłady nakładają się z pytaniami w
zakresie (np. `kampus-cieszyn` 0.406), więc sam próg ich nie odfiltruje. Dla nich
zostaje instrukcja w prompcie systemowym („Jeśli w kontekście nie ma odpowiedzi…”).

**Follow-upy i `build_query()`.** Zapytanie do wyszukiwarki skleja 3 ostatnie pytania
użytkownika. Sprawdzono 13 rozmów testowych (top-1 z historią vs. samo pytanie):

- Historia ratuje krótkie follow-upy: „a wnst?” po pytaniu o dziekanat WH ma 0.336
  (samo: 0.484), „a socjalne?” 0.266 (0.462), „a co tam jest o warunku?” 0.331 (0.478),
  „a telefon?” 0.314 (0.451). Bez historii wszystkie byłyby odrzucone.
- Historia szkodzi po zmianie tematu: „przepis na pierogi” → „wybory” → „Jak uzyskać
  urlop dziekański?” dawało 0.512 (samo pytanie: 0.259) — fałszywe odrzucenie, a i bez
  progu top-1 był przypadkowy. Podobnie „pogoda” → „Jak zalogować się do USOSweb?”:
  0.404 z nietrafnym top-1 zamiast 0.291 i strony o USOSweb.
- Dlatego `retrieve_for_chat()` szuka dwoma zapytaniami — z historią i samym ostatnim
  pytaniem — i dla każdego fragmentu bierze mniejszą odległość. Po zmianie wszystkie
  follow-upy przechodzą próg, a pytania po zmianie tematu dostają właściwe strony
  (urlop 0.259 → `tok-studiow-2/urlopy`, USOSweb 0.291).
- Ograniczenie: pytanie spoza zakresu zadane po pytaniu w zakresie przechodzi dzięki
  historii („stypendium rektora” → „przepis na pierogi”: 0.378). LLM dostaje wtedy
  kontekst o stypendiach i musi sam odmówić.

**Uwagi:**

- Wyszukiwanie HNSW jest przybliżone. Dla 48 pytań w zakresie top-1 zgadza się z
  wyszukiwaniem dokładnym, ale dla części pytań spoza zakresu indeks zwraca dalszy
  top-1 niż pełny skan (pogoda 0.433 vs 0.395, prompt injection 0.517 vs 0.500,
  przepis 0.532 vs 0.481). Część odrzuceń zależy więc od stanu indeksu — po `REINDEX`
  lub większej ingestii trzeba powtórzyć sweep.
- Dla zapytania „nie działa mi, co robić?” indeks HNSW zwraca **0 wierszy** (pełny skan
  znajduje fragment w odległości 0.389). Bot odpowiada wtedy stałym tekstem — tak samo
  jak przy przekroczeniu progu.

!!! warning "Status"
    Katalog `tests/` jest **planowany** — testy zostaną zaimplementowane w kolejnych
    iteracjach. Powyższe komendy zakładają standardową strukturę pytest.
