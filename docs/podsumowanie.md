# 7. Podsumowanie techniczne

## 7.1 Największe wyzwania

- **Dobór modelu embeddingów** — `BAAI/bge-m3` (1024 dim) był konieczny dla wysokiej
  jakości embeddingów polskojęzycznych. Modele anglojęzyczne dawały znacznie gorsze
  wyniki semantyczne dla treści uczelnianej.
- **Środowisko Windows** — aplikacja wymagała jawnego ustawienia kodowania UTF-8
  (`sys.stdout.reconfigure`) ze względu na domyślne `cp1252`, które nie obsługuje
  polskich znaków. Dodatkowo wymuszono tryb offline Hugging Face
  (`HF_HUB_OFFLINE=1`), by uniknąć zawieszania startu na odpytywaniu HF Hub.
- **Jakość odpowiedzi a wielkość modelu** — `qwen3:1.7b` gubił kontekst i skróty;
  przejście na `qwen3:8b` znacząco poprawiło rozumowanie (przy dostępnym GPU).
- **Retrieval na follow-upach** — krótkie pytania uzupełniające („a wnst?") nie
  niosły sensu dla wyszukiwarki wektorowej. Rozwiązano to budowaniem zapytania z
  kilku ostatnich tur użytkownika (`build_query`).
- **Pokrycie i wydajność crawla** — pełne odświeżanie bazy przy każdym uruchomieniu
  było kosztowne. Wprowadzono crawl inkrementalny (ETag/Last-Modified + hash) oraz
  równoległe pobieranie i batch embedding. Pobieranie paczkami okazało się wąskim
  gardłem: `usnet.us.edu.pl` odpowiada po ~11 s (czas generowania strony po stronie
  serwera), więc jedna jego strona blokowała całą paczkę i crawl szedł ~1 stronę/s.
  Stały pool żądań z limitem na domenę przyspieszył pobieranie z `us.edu.pl` z 1,5
  do 9,5 strony/s.
- **Szum w bazie** — ukryty baner cookies trafiał do ~13% fragmentów, a galerie
  zdjęć i strony załączników WordPressa zajmowały miejsca w wynikach. Usunięto je
  z ekstrakcji i crawla; ewaluacja retrievalu pokazała wzrost Hit@10 z 0,54 do 0,92.
- **Kontekst fragmentów** — płaski podział tekstu co 800 znaków odcinał fragment od
  strony: „Godziny pracy: pon 10–14" nie mówił, którego dziekanatu dotyczy. Sam podział
  po nagłówkach pogorszył wyniki (krótkie sekcje są jeszcze mniej samodzielne); pomógł
  dopiero prefiks `<tytuł strony> > <nagłówek sekcji>` w każdym fragmencie (Hit@1
  0,50 → 0,54, MRR 0,630 → 0,643). Zmiana chunkingu nie zmienia hasha tekstu, więc
  doszła wersja chunkera zapisywana w `pages`.
- **Port bazy danych** — domyślny port PostgreSQL (5432) bywał zajęty przez lokalne
  instalacje, stąd mapowanie na 5433 w `docker-compose.yml`.
- **Filtrowanie odpowiedzi** — zapewnienie, że bot nie odpowiada spoza kontekstu,
  wymagało starannie skonstruowanego system promptu z instrukcjami negatywnymi.

## 7.2 Zrealizowane i niezrealizowane elementy

### Zrealizowane

- Aplikacja terminalowa (`cli.py`) z rozmową wieloturową (historia konwersacji).
- Potok RAG: embedding pytania → wyszukiwanie wektorowe → generacja z kontekstem.
- **Retrieval z kontekstem rozmowy** (`build_query`) dla krótkich follow-upów.
- **Inkrementalny crawler** BFS: requesty warunkowe (304), deduplikacja po hashu,
  aktualizacja per-URL, tabela `pages` jako cache stanu.
- **Priorytetyzacja crawla**: priorytetowe seedy przed sitemapą oraz filtrowanie
  bezwartościowych pod-sitemap (załączniki/multimedia/tagi) — ~45,8 tys. → ~10,7 tys. URL-i.
- **Równoległe pobieranie** (`ThreadPoolExecutor`, stale do `CRAWL_WORKERS` żądań
  w locie, maks. 2 do usnet, 0,1 s odstępu na domenę) + **batch embedding** i batch insert.
- **Czysta ekstrakcja tekstu**: bez banera cookies, menu i stopki; pomijanie galerii
  i stron załączników WordPressa.
- **Chunking po sekcjach** z prefiksem tytułu strony i nagłówka sekcji; ekstrakcja
  zachowuje nagłówki h1–h6. `CHUNKER_VERSION` (w `pages.chunker_version`) wymusza
  ponowny podział i embedding po zmianie chunkingu.
- **Metadane fragmentów**: `title`, `page_type` (`student`, `wydzial`, `static`, `news`,
  `event`) i `published_at`; opcjonalna kara za stare aktualności w `retrieve()`
  (`OLD_NEWS_PENALTY`, domyślnie wyłączona).
- **Próg odcięcia** `MAX_DISTANCE` — gdy nic nie jest dość blisko, bot odpowiada stałym
  tekstem bez wywołania LLM-a.
- **Ewaluacja retrievalu** (`evaluation/evaluate.py`): 48 pytań z oznaczonymi
  stronami + 18 spoza zakresu; Hit/Recall/Precision/nDCG@k, MRR, raport pokrycia
  bazy, dobór progu. Wynik bazowy 1a: Hit@10 0,917, MRR 0,630; po chunkingu z
  prefiksem: Hit@1 0,542, MRR 0,643 (patrz [Testy](testy.md)).
- Crawl wielu domen: `us.edu.pl`, `usnet.us.edu.pl`, `eduroam.us.edu.pl`.
- **Dopytywanie o dziekanat** — przy pytaniu bez wskazania wydziału bot prosi o
  doprecyzowanie zamiast zgadywać.
- Skrypt ingestii z chunkingiem, paskiem postępu (`tqdm`) i trybami `CRAWL_FRESH`
  oraz `CRAWL_REFETCH` (ponowna ekstrakcja bez re-embeddingu niezmienionych stron).
- Infrastruktura Docker (PostgreSQL + pgvector) z automatyczną inicjalizacją bazy.
- Obsługa dwóch backendów LLM: Ollama (CPU/GPU) i vLLM (GPU).
- Tryb rozumowania Qwen3 (`ENABLE_THINKING`) i pełna konfiguracja przez `.env`.

### Niezrealizowane (planowane)

- Interfejs webowy — zaplanowany jako rozszerzenie projektu.
- Testy automatyczne (pytest) — do zaimplementowania w kolejnych iteracjach.
- CI/CD (GitHub Actions) — nieskonfigurowane w obecnej wersji.
- Uwierzytelnianie użytkowników — brak w wersji CLI.
- Monitoring i logowanie strukturalne — brak dedykowanego systemu logów.
- Pruning nieaktualnych stron — w bazie zostają stare fragmenty stron, które
  zwracają już 404, oraz stron, dla których nowa ekstrakcja daje pusty tekst. Zostają
  też strony, do których przebieg
  nie doszedł przed `CRAWL_LIMIT`. Dziś czyści je dopiero pełny re-crawl
  (`CRAWL_FRESH=true`) albo ręczne usunięcie.
- Strony załączników pobrane przed wprowadzeniem ich pomijania mogą nadal być w
  bazie (z krótkim podpisem zdjęcia zamiast banera); ich liczby nie zweryfikowano.

## 7.3 Kierunki rozwoju

- **Interfejs webowy** — migracja z CLI do aplikacji webowej z czatem i streamingiem.
- **RAG hybrydowy** — połączenie wyszukiwania wektorowego z pełnotekstowym
  (PostgreSQL FTS) dla zapytań dokładnych (numery indeksów, kody przedmiotów).
- **Wielomodalność** — upload plików PDF (np. regulaminów) i pytania na ich podstawie.
- **Panel administracyjny** — zarządzanie ingestią, przegląd fragmentów, monitoring jakości.
- **Ewaluacja automatyczna** — metryki jakości RAG (RAGAS, TruLens) do mierzenia
  trafności i halucynacji.
- **Konteneryzacja pełnej aplikacji** — `Dockerfile` dla `cli.py` / serwera webowego.
- **Pruning bazy** — automatyczne usuwanie fragmentów stron, które zniknęły ze źródła.
- **Różnorodność wyników** — limit fragmentów z jednej strony w top-k. Z prefiksem
  fragmenty jednej strony są do siebie podobne i zajmują kilka miejsc naraz (średnio
  6,7 różnych stron w top-10 zamiast 7,9), stąd spadek Hit@10 z 0,917 do 0,854.
- **Angielskie wersje portalu** (`/student/en/`) — pomijanie lub obniżanie w wynikach
  dla pytań po polsku.

---

*Dokument odzwierciedla stan kodu źródłowego repozytorium
[github.com/Erni0k/USBot](https://github.com/Erni0k/USBot). Wersja 0.4 — listopad 2026.*
