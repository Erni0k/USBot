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
| T13 | Jednostkowy | `_select()` | Kandydaci z powtórzonym `body_hash` i 4 fragmentami jednej strony | Każda treść raz (najbliższa, przy remisie krótszy URL), ≤ `MAX_PER_URL` na stronę, nadal `k` wyników |
| T14 | Integracyjny | `ingest._flush()` | Strona z niezmienionymi i jednym nowym fragmentem | Embedding liczony tylko dla nowego, reszta z cache'u (te same wektory) |
| T15 | Integracyjny | `ingest._flush()` | Fragment, którego treść ma już ponad `BOILERPLATE_URLS` stron | Nie zapisany; wcześniejsze kopie usunięte z `chunks`, hash zostaje w `pages.chunk_hashes` |

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
| różnych stron | średnia liczba różnych URL-i w top-k (ile stron naprawdę dostaje LLM) |

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

**Chunking po sekcjach, prefiks i kara za stare aktualności** (2026-11-03; `CHUNKER_VERSION = 2`).
Plaski podział `RecursiveCharacterTextSplitter(800, 120)` gubił kontekst: fragment
„Godziny pracy: pon 10–14" nie mówił, którego dziekanatu dotyczy. Zmiany:

- **Ekstrakcja z nagłówkami.** Sprawdzono `trafilatura.extract(output_format="markdown")`
  na dziekanatach WH, WNP i WNST, portalu studenta (akademiki), usnet (`/siec/serwer-vpn/`)
  i `/kontakt/`. Nagłówki są, ale niepełne: na WH jedyny nagłówek to h5 „GODZINY PRACY
  DZIEKANATU" (resztę strona oznacza pogrubieniem), na WNST trafilatura daje puste `#####`
  z ozdobnych kafelków, w komunikatach hashtagi `#FunduszeUE` wyglądają jak nagłówki,
  a na akademikach markdown ma tylko 737 z ~3 800 znaków treści. Dlatego: markdown z
  trafilatury po czyszczeniu (bez `**`, pustych nagłówków i hashtagów), a gdy gubi ponad
  połowę treści `<main>` — własny tekst z `<main>` z nagłówkami h1–h6 (`_main_text()`).
  usnet nie ma `<main>`, ale markdown z trafilatury ma tam pełną strukturę (h1/h2).
- **Podział po nagłówkach**, za długie sekcje dalej splitterem 800/120. Nagłówek bez
  treści (h2, a zaraz po nim h3) łączy się z następną sekcją.
- **Prefiks** `<tytuł strony> > <nagłówek sekcji>` na początku każdego fragmentu (z niego
  liczony jest embedding), np. `dziekanat | Wydział Nauk Przyrodniczych > Pok. 111 – Godziny
  pracy:`. Tytuł z `<title>` bez „| Uniwersytet Śląski w Katowicach".
- **Metadane** `title`, `page_type`, `published_at` w `chunks` (wypełnione dla wszystkich
  42 397 fragmentów). 25 316 fragmentów (60%) to aktualności i wydarzenia starsze niż 2 lata.
- **Kara** w `retrieve()`: przy sortowaniu `dist + OLD_NEWS_PENALTY` dla `news`/`event`
  starszych niż `OLD_NEWS_DAYS` (730). Próg `MAX_DISTANCE` dotyczy nadal samej odległości.
- Przy okazji: treść strony polityki prywatności (`div.cmplz-document`) była wycinana
  razem z banerem cookies — reguła usuwania banera ją teraz pomija.

Baza po pełnym przebiegu: **42 397 fragmentów z 12 055 stron** (1A: 39 003 z 12 020;
nowe strony to głównie aktualności dodane od 2 listopada). Wariant „sam chunking" to kopia
tej bazy z usuniętym prefiksem (pierwsza linia fragmentu) i embeddingami policzonymi od
nowa — splitter tnie samą sekcję, więc to dokładnie te same fragmenty bez prefiksu. Etykiet
nie zmieniano. Pokrycie: bez zmian, 94/95 oznaczonych stron, 48/48 pytań ma trafną stronę
w bazie.

| Wariant | Hit@1 | Hit@3 | Hit@5 | Hit@8 | Hit@10 | nDCG@8 | nDCG@10 | Recall@10 | MRR@10 | Bez trafienia w top-10 |
|---|---|---|---|---|---|---|---|---|---|---|
| 1A (płaski tekst 800/120) | 0.500 | 0.729 | 0.792 | 0.854 | **0.917** | 0.503 | 0.519 | **0.646** | 0.630 | 4 |
| sam chunking po sekcjach | 0.479 | 0.708 | 0.792 | 0.812 | 0.854 | 0.480 | 0.490 | 0.585 | 0.605 | 7 |
| **chunking + prefiks** (włączone) | **0.542** | 0.729 | 0.812 | 0.854 | 0.854 | 0.511 | 0.513 | 0.580 | **0.643** | 7 |
| + kara 0.02 | **0.542** | 0.708 | 0.812 | 0.875 | 0.875 | 0.527 | 0.536 | 0.618 | 0.648 | 6 |
| + kara 0.05 | 0.521 | 0.708 | 0.812 | **0.875** | 0.875 | **0.530** | **0.541** | 0.637 | 0.633 | 6 |
| + kara 0.10 | 0.500 | 0.688 | 0.792 | 0.812 | 0.833 | 0.508 | 0.526 | 0.601 | 0.607 | 8 |

Jedno pytanie to 0.021 w kolumnach Hit@k. `TOP_K = 8`, więc Hit@8 i nDCG@8 opisują to,
co dostaje LLM.

- **Sam chunking pogarsza wynik** we wszystkich metrykach. Krótkie sekcje bez tytułu
  strony są jeszcze mniej samodzielne niż fragmenty 800-znakowe: „Godziny pracy:" z tabelką
  pasuje do każdego dziekanatu.
- **Prefiks to odwraca.** Względem samego chunkingu zysk jest we wszystkich metrykach
  (Hit@1 +0.06, MRR +0.04), względem 1A — na górze listy: Hit@1 0.500 → 0.542, MRR
  0.630 → 0.643, Hit@8 bez zmian, nDCG@8 +0.008. Zyskują pytania o dziekanaty, dla których
  prefiks wnosi nazwę wydziału: `dziekanat-wh` (trafienie z pozycji 4 na 1),
  `dziekanat-wnst` (7 → 1), a także `rekrutacja-terminy` (8 → 1), `samorzad`, `wf`.
- **Koszt prefiksu: mniej różnych stron w top-10** (średnio 6.7 zamiast 7.9). Fragmenty
  jednej strony mają wspólny tytuł, więc są do siebie bardziej podobne i zajmują kilka
  miejsc naraz. Dlatego Hit@10 spada 0.917 → 0.854: `erasmus`, `haslo-konto` i `wladze`
  miały w 1A trafienie na pozycji 2–10, a teraz wypadają z top-10 (polskie i angielskie
  wersje tej samej strony portalu, kilka fragmentów „Kalendarium"). Pomogłoby
  ograniczenie liczby fragmentów z jednej strony w wynikach — wprowadzone niżej
  (`MAX_PER_URL`).
- **Kara za stare aktualności — zostawiona wyłączona** (`OLD_NEWS_PENALTY=0`). Robi to,
  do czego jest: wyników z rokiem 2016–2021 w adresie (bez portalu studenta) jest w top-8
  pytań w zakresie 27 bez kary, 17 przy 0.02 i 6 przy 0.05, a list rektora z 2020 r.
  znika z top-10 pytań o stypendia (przy 0.05). Na etykietach zysk mieści się jednak w
  szumie: przy 0.02 poprawia się 6 pytań i pogarsza 3 (`wf` 1 → 2, `irk-rejestracja`,
  `it-incydent`), przy 0.05 Hit@1 spada o jedno pytanie (`szkola-doktorska` 1 → 2), a
  0.10 jest wyraźnie gorsze. `wydzialy` nie pomaga żadna wartość — wpisy o rekrutacji z
  2017 r. są bliżej pytania o ponad 0.05. Kandydat do włączenia: 0.02–0.05, gdy zbiór
  testowy będzie większy albo gdy priorytetem będzie aktualność kontekstu, nie metryki.
- **Próg `MAX_DISTANCE` bez zmian (0.43).** Odległość top-1: w zakresie 0.180–0.408
  (mediana 0.273; było 0.191–0.406), spoza zakresu 0.356–0.577 (mediana 0.407). Przy 0.43
  odrzucone 0/48 pytań w zakresie i 8/18 spoza zakresu (było 9/18). Utracone trafienia
  łącznie: 1 — `szkolenia-obowiazkowe` nie jest odrzucane (top-1 0.371, nietrafny), ale
  jego jedyny trafny fragment w top-8 (`student/studia/obowiazkowe-szkolenia`, pozycja 7)
  ma 0.465 i próg go odcina. Przy obecnej kolejności wyników w 1A to pytanie też nie
  dostawało trafnego fragmentu przed progiem (pozycja 10), więc to nie jest regresja progu.

**Duplikaty treści, boilerplate i limit fragmentów z jednej strony** (2026-11-03; baza jak
wyżej, 42 397 fragmentów z 12 055 stron, k = `TOP_K` = 8).

Skala — ta sama treść po ujednoliceniu białych znaków:

- **Cała treść fragmentu** (z prefiksem `<tytuł> > <nagłówek>`): 41 431 unikalnych, 820
  grup z powtórzeniami, 966 nadmiarowych kopii (2,3% fragmentów). 722 grupy występują na
  więcej niż jednej stronie; 776 URL-i dzieli fragment z inną stroną. Żadna treść nie ma
  więcej niż 9 URL-i — prefiks z tytułem jest na każdej stronie inny.
- **Treść bez prefiksu**: 40 814 unikalnych, 1 126 grup, 1 583 nadmiarowe kopie (3,7%);
  1 036 grup na więcej niż jednej stronie, 1 182 URL-e dzielące fragment. Na ponad 5
  stronach: 42 treści, ponad 10: 3, ponad 20: 2.
- 138 par stron ma co najmniej 3 identyczne fragmenty — to ta sama strona pod dwoma
  adresami: nieprzetłumaczone kopie pod `/student/en/` (do 19 wspólnych fragmentów),
  `/student` i `/student/pl`, aktualność i jej kopia z `-2` w adresie.
- „Kontakt dla mediów” jest w jednym fragmencie (`/kontakt`) — stopkę aktualności usuwa
  już ekstrakcja.

10 najczęstszych treści (bez prefiksu):

| Stron | Fragmentów | Treść |
|---|---|---|
| 36 | 36 | „Dostępność architektoniczna:” — sam nagłówek bez treści (deklaracje dostępności budynków) |
| 31 | 31 | „Drukuj stronę Udostępnij na Facebook…” — przyciski pod wydarzeniami i aktualnościami |
| 13 | 13 | „Sfinansowane ze środków UE. Wyrażone poglądy…” — stopka projektu (cykl „Tydzień … w Mieście Nauki”) |
| 9 | 9 | „Funded by the European Union. Views and opinions…” — to samo po angielsku |
| 9 | 9 | „Zabierz ze sobą – naczynia, sztućce…” — akademiki (8 domów studenta + kopia `/en/`) |
| 9 | 9 | „Informacje dodatkowe. Zwierzęta: za zgodą administratora…” — akademiki |
| 9 | 9 | „Ogólne – zakaz palenia papierosów” — akademiki |
| 9 | 9 | „Electro-Intrusion. Simultaneous transformation…” — opis projektu w 9 komunikatach |
| 9 | 9 | „Greenhouse gas emissions, pollution…” — ten sam opis, kolejny fragment |
| 9 | 9 | „teams specializing in physics, chemistry…” — ten sam opis, kolejny fragment |

W top-8 pytań w zakresie kopie tej samej treści zajmowały 7 miejsc (4 z 48 pytań).
Większym problemem jest koncentracja: średnio tylko 5,5 różnej strony w top-8, w 12
pytaniach jedna strona zajmowała co najmniej 4 z 8 miejsc, w 3 pytaniach — 7.

Zmiany (opis w README):

- `chunks.content_hash` (cała treść, z której liczony jest embedding) — cache
  embeddingów przy ingestii; `chunks.body_hash` (bez prefiksu) — ten sam tekst na
  różnych stronach. Migracja w `_ensure_schema()` uzupełniła hashe istniejących fragmentów.
- Boilerplate: treść na więcej niż `BOILERPLATE_URLS` = 20 stronach nie trafia do bazy.
  Usunięto 67 fragmentów (dwie pierwsze pozycje tabeli).
- `retrieve()`: ta sama treść (`body_hash`) zostaje raz — najbliższy fragment; jedna
  strona zajmuje najwyżej `MAX_PER_URL` miejsc; zwolnione miejsca zajmują kolejni
  kandydaci (pobieranych jest 5·k, min. 40), więc nadal jest `TOP_K` wyników. To samo
  w `retrieve_for_chat()` po złączeniu obu zapytań.

Wyniki (etykiet nie zmieniano; „przed” = stan po chunkingu z prefiksem, tabela wyżej):

| Wariant | Hit@1 | Hit@3 | Hit@8 | Hit@10 | nDCG@8 | nDCG@10 | Recall@8 | Recall@10 | MRR@10 | Różnych stron w top-8 | Kopie w top-8 | Bez trafienia w top-10 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| przed | 0.542 | 0.729 | 0.854 | 0.854 | 0.511 | 0.513 | 0.568 | 0.580 | 0.643 | 5.52 | 7 | 7 |
| bez duplikatów, bez limitu | 0.542 | 0.729 | 0.854 | 0.875 | 0.512 | 0.517 | 0.568 | 0.585 | 0.645 | 5.54 | 0 | 6 |
| + `MAX_PER_URL` = 3 | 0.542 | 0.729 | 0.854 | 0.875 | 0.514 | 0.525 | 0.575 | 0.611 | 0.645 | 5.85 | 0 | 6 |
| **+ `MAX_PER_URL` = 2** (włączone) | 0.542 | **0.750** | 0.854 | **0.896** | 0.525 | 0.541 | 0.599 | 0.632 | 0.649 | 6.35 | 0 | 5 |
| + `MAX_PER_URL` = 1 | 0.542 | **0.750** | **0.896** | **0.896** | **0.556** | **0.571** | **0.627** | **0.679** | **0.662** | 8.00 | 0 | 5 |

Hit@5 = 0.812 we wszystkich wariantach. Wariant `MAX_PER_URL` = 3 zmierzono przed
usunięciem boilerplate'u; dla pozostałych wyniki przed i po usunięciu są identyczne
(żadne top-10 się nie zmieniło — usunięte fragmenty nie trafiały do wyników pytań testowych).

- **Duplikaty nie zajmują już miejsc w top-k** (0 kopii we wszystkich wariantach). Na
  metryki wpływa to mało, bo kopii było niewiele: `erasmus` wchodzi na pozycję 10.
- **Remisy.** Pierwszy pomiar bez duplikatów dał Hit@1 0.521 (−1 pytanie):
  w `egzamin-komisyjny` oryginał z portalu studenta i jego kopia pod `/student/en/` mają
  identyczną treść i odległość (0.3793), a deduplikacja zostawiła przypadkiem kopię
  `/en/`. Teraz przy równej odległości wygrywa krótszy URL — zwykle oryginał, i to jego
  adres powinien cytować LLM. Bez zmian etykiet wynik wraca do 0.542.
- **Limit 2 na stronę:** Hit@10 0.854 → 0.896 (`haslo-konto` z poza top-10 na 9,
  `erasmus` na 10), nDCG@8 +0.014, Recall@8 +0.031, różnych stron w top-8 5.5 → 6.4.
  Hit@8 bez zmian. Żadne pytanie nie pogorszyło się w żadnym wariancie (pozycja
  pierwszego trafienia w top-10).
- **Dlaczego 2, a nie 1.** Limit 1 wygrywa we wszystkich metrykach (Hit@8 +0.04, MRR
  0.662; dodatkowo `thunderbird` 4 → 2, `stypendium-rektora` i `stypendium-socjalne`
  3 → 2, `alarm` 5 → 4). Ale trafność liczymy po URL-u, więc metryki z definicji
  nagradzają różne strony i nie widzą, że odpowiedź bywa w drugim fragmencie tej samej
  strony (np. godziny i kontakt dziekanatu w osobnych sekcjach). Limit 1 to kandydat,
  gdy będzie ewaluacja samych odpowiedzi; 3 prawie nic nie zmienia względem braku limitu.
- **Boilerplate.** Próg 20 łapie tylko przyciski „Udostępnij” i pusty nagłówek. Kolejna
  treść ma 13 stron (stopka UE) — to też boilerplate, ale zostaje; próg 10 dodałby tylko
  ją. Regulamin akademików (9 stron) jest merytoryczny i musi zostać, stąd zapas.
- **Cache embeddingów** sprawdzony testem `_flush()` w transakcji z rollbackiem: strona
  z 3 fragmentami + 1 nowy → 1 embedding policzony, 2 z cache'u (wektory identyczne
  z zapisanymi), 1 boilerplate pominięty, a jego 31 kopii z innych stron usuniętych.
  Przy ingestii od zera cache oszczędza ok. 970 embeddingów (kopie całej treści, 2,3%);
  główny zysk to edytowane strony — liczone są tylko zmienione sekcje. Pełnego przebiegu
  ingestii po zmianie nie uruchamiano.
- **Próg `MAX_DISTANCE` bez zmian (0.43).** Odległości top-1 te same co wyżej; przy 0.43
  odrzucone 0/48 pytań w zakresie i 8/18 spoza zakresu, utracone trafienia: 1 (nadal
  `szkolenia-obowiazkowe`).
- **Uwaga do T12.** Follow-up „a wnst?” po pytaniu o dziekanat WH nie zwraca w top-8
  dziekanatu WNST — ani przed tą zmianą, ani po niej; zapytanie z historią jest
  zdominowane przez WH. Po zmianie zamiast 5 fragmentów jednej strony
  `/student/en/…/dziekanat-wydzialu-humanistycznego/contact` są 2 i trzy inne strony.
  Do zbadania osobno.

!!! warning "Status"
    Katalog `tests/` jest **planowany** — testy zostaną zaimplementowane w kolejnych
    iteracjach. Powyższe komendy zakładają standardową strukturę pytest.
