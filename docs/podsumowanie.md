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
- **Powtórzona treść i jedna strona w całym kontekście** — kopie tej samej treści
  (strona i jej nieprzetłumaczona wersja pod `/student/en/`, przyciski „Udostępnij”)
  stanowią tylko 2–4% fragmentów, ale prefiks z tytułem sprawił, że fragmenty jednej
  strony zajmowały razem wiele miejsc: średnio 5,5 różnych stron w top-8, w skrajnych
  przypadkach 7 z 8 miejsc z jednej strony. Deduplikacja po hashu treści i limit 2
  fragmentów na stronę dały 6,4 strony w top-8 i Hit@10 0,854 → 0,896. Deduplikacja
  wymagała rozstrzygania remisów — identyczne kopie mają tę samą odległość i bez reguły
  (krótszy URL wygrywa) zostawała przypadkowa kopia `/en/`.
- **Próg odcięcia na właściwej skali** — próg odległości wektorowej (`MAX_DISTANCE`)
  odrzucał pytania ze skrótami („Gdzie jest dziekanat WNŚiT?”), bo rozkłady odległości
  pytań w zakresie i spoza niego nakładają się. Reranker ocenia parę (pytanie, fragment)
  i jego wynik rozdziela je lepiej: przy progu 0.01 żadne pytanie w zakresie nie jest
  odrzucane (było 2 z 56), a pytań spoza zakresu odrzuconych jest 10 z 18 (było 8).
  Pytań o inne uczelnie nie rozdziela żaden próg — reranker ocenia temat, nie uczelnię.
  Reranker na CPU jest za wolny (~6 s), stąd fp16 na GPU (~90 ms) i domyślnie wyłączony.
- **Port bazy danych** — domyślny port PostgreSQL (5432) bywał zajęty przez lokalne
  instalacje, stąd mapowanie na 5433 w `docker-compose.yml`.
- **Filtrowanie odpowiedzi** — zapewnienie, że bot nie odpowiada spoza kontekstu,
  wymagało starannie skonstruowanego system promptu z instrukcjami negatywnymi.

## 7.2 Zrealizowane i niezrealizowane elementy

### Zrealizowane

- Aplikacja terminalowa (`cli.py`) z rozmową wieloturową (historia konwersacji).
- **API HTTP** (`usbot/api/`, FastAPI) dla strony WWW: `POST /api/chat` jako Server-Sent
  Events, `/api/health` z limitami czasu, `/api/faculties`; zasoby (modele, pula połączeń
  z bazą, klient LLM-a) ładowane raz przy starcie, serwer bezstanowy, rozłączenie klienta
  przerywa generowanie. Pierwszy token po ~0.8 s, całość ~6.4 s (mediana, `qwen3:8b`,
  `LLM_REASONING_EFFORT=none`; [API](api.md), 3.2).
- **Architektura warstwowa** (`usbot/`): rdzeń (`ChatService` — odpowiedź jako strumień
  zdarzeń `sources`/`token`/`refused`/`clarify`/`done`) zależy tylko od portów; adaptery
  bge-m3, cross-encodera, pgvector i API OpenAI wspólne dla `cli.py`, ewaluacji i ingestii;
  konfiguracja przez pydantic-settings; API HTTP to kolejny adapter wejścia. Refaktor
  bez zmiany wyników ewaluacji.
- **Testy automatyczne** (pytest): jednostkowe na atrapach portów (bez Dockera) i
  integracyjne retrievera pgvector na testcontainers — plan i status w [Testy](testy.md) (6.2).
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
  `event`) i `published_at`; opcjonalna kara za stare aktualności w wyszukiwaniu
  (`OLD_NEWS_PENALTY`, domyślnie wyłączona).
- **Próg odcięcia** `MAX_DISTANCE` — gdy nic nie jest dość blisko, bot odpowiada stałym
  tekstem bez wywołania LLM-a.
- **Różnorodność wyników** — w top-k ta sama treść występuje raz, a jedna strona zajmuje
  najwyżej `MAX_PER_URL` (2) miejsc; zwolnione miejsca zajmują kolejni kandydaci.
- **Wyszukiwanie hybrydowe** (`RETRIEVAL_MODE=hybrid`, domyślnie wyłączone) — wektorowe
  + pełnotekstowe (`chunks.content_tsv`, `'simple'` + `unaccent`, GIN) łączone przez
  Reciprocal Rank Fusion. Na zbiorze testowym nie poprawia kontekstu LLM-a (Hit@8 po
  progu bez zmian), obniża Hit@1 — domyślny zostaje tryb wektorowy.
- **Reranking** (`RERANK=true`, domyślnie wyłączony — wymaga GPU) — 20 kandydatów ocenia
  cross-encoder `bge-reranker-v2-m3`, próg działa na jego wyniku (`MIN_RERANK_SCORE` =
  0.01). Trafienie w top-8 po progu: 50/56 pytań zamiast 45/56, 0/56 pytań w zakresie
  odrzuconych (było 2), 10/18 spoza zakresu odrzuconych (było 8). Hit@1 bez progu
  spada (0.571 → 0.536) — część nowych top-1 to nieoznaczone trafne strony, część stare
  aktualności.
- **Cache embeddingów i boilerplate** — `content_hash` w `chunks`: treść, która już jest
  w bazie, nie jest embedowana ponownie; treść na ponad 20 stronach (`BOILERPLATE_URLS`)
  nie trafia do bazy.
- **Ewaluacja retrievalu** (`evaluation/evaluate.py`, `--mode vector|hybrid`, `--rerank`): 56 pytań z
  oznaczonymi stronami (w tym 8 ze skrótami i nazwami własnymi) + 18 spoza zakresu; Hit/Recall/Precision/nDCG@k, MRR, raport pokrycia
  bazy, dobór progu. Wynik bazowy 1a: Hit@10 0,917, MRR 0,630; po chunkingu z
  prefiksem: Hit@1 0,542, MRR 0,643; po deduplikacji i limicie na stronę: Hit@10 0,896,
  MRR 0,649; z rerankingiem (56 pytań): Hit@8 po progu 0,804 → 0,893 (patrz [Testy](testy.md)).
- Crawl wielu domen: `us.edu.pl`, `usnet.us.edu.pl`, `eduroam.us.edu.pl`.
- **Data publikacji komunikatów w kontekście** i dzisiejsza data w prompcie — bot nie podaje
  dni wolnych z 2016/2017 ani starych kwot jako aktualnych ([Testy](testy.md), 6.5).
- **Dopytywanie o dziekanat** — przy pytaniu bez wskazania wydziału bot prosi o
  doprecyzowanie zamiast zgadywać (reguła w `ChatService`, bez wywołania LLM-a). Skróty
  wydziałów w zapytaniu są rozwijane do pełnych nazw, więc odpowiedź „WNP” trafia we
  właściwy dziekanat (tryb vector: zły wydział na 1. miejscu 39% → 2% rozmów).
- Skrypt ingestii z chunkingiem, paskiem postępu (`tqdm`) i trybami `CRAWL_FRESH`
  oraz `CRAWL_REFETCH` (ponowna ekstrakcja bez re-embeddingu niezmienionych stron).
- Infrastruktura Docker (PostgreSQL + pgvector) z automatyczną inicjalizacją bazy.
- Obsługa dwóch backendów LLM: Ollama (CPU/GPU) i vLLM (GPU).
- **Bezpieczeństwo publicznego API** (Architektura, 2.4): model zagrożeń; kontekst w
  `<context>` z instrukcją i przypomnieniem; usuwanie tekstu ukrytego w HTML; limity
  długości (`422`), rate limiting na IP (slowapi), limit równoległych strumieni; CORS tylko
  dla frontendu, nagłówki bezpieczeństwa; dziennik rozmów bez IP z retencją 30 dni;
  przegląd zależności (`pip-audit`) i sekretów.
- Tryb rozumowania Qwen3 (`ENABLE_THINKING`; na Ollamie wyłączany przez
  `LLM_REASONING_EFFORT=none`, bo Ollama ignoruje `ENABLE_THINKING`) i pełna konfiguracja
  przez `.env`.

### Niezrealizowane (planowane)

- Interfejs przeglądarkowy (frontend) — API HTTP jest gotowe, strony WWW jeszcze nie ma;
  mapowanie zdarzeń na widoki: [UI/UX](ui-ux.md) (5.4).
- Część testów z planu (crawler, chunking, ekstrakcja — T01, T06–T10, T14, T15).
- CI/CD (GitHub Actions) — nieskonfigurowane w obecnej wersji.
- Uwierzytelnianie użytkowników — brak (API jest publiczne; chronią je limity na IP).
- Limit dla ataku rozproszonego (wiele adresów IP) — tylko na reverse proxy / WAF.
- Pełna odporność na prompt injection — mały model wciąż wykonuje część poleceń
  (Architektura, 2.4).
- Monitoring i logowanie strukturalne — brak dedykowanego systemu logów.
- Pruning nieaktualnych stron — w bazie zostają stare fragmenty stron, które
  zwracają już 404, oraz stron, dla których nowa ekstrakcja daje pusty tekst. Zostają
  też strony, do których przebieg
  nie doszedł przed `CRAWL_LIMIT`. Dziś czyści je dopiero pełny re-crawl
  (`CRAWL_FRESH=true`) albo ręczne usunięcie.
- Strony załączników pobrane przed wprowadzeniem ich pomijania mogą nadal być w
  bazie (z krótkim podpisem zdjęcia zamiast banera); ich liczby nie zweryfikowano.

## 7.3 Kierunki rozwoju

- **Strona WWW** — frontend na API HTTP (strumień SSE, przyciski wydziałów, lista
  źródeł); przerwanie odpowiedzi także w trakcie rozumowania Qwen3 (dziś dopiero po jego
  końcu, gdy rozumowanie jest włączone).
- **Lepszy RAG hybrydowy** — tryb `hybrid` istnieje, ale przegrywa z wektorowym; traci
  też na rozwijaniu skrótów wydziałów (pełna nazwa przyciąga aktualności wydziału). Do
  sprawdzenia: ranking pełnotekstowy ważący rzadkie słowa (BM25/IDF), próg uwzględniający
  dopasowanie słowne (pytania ze skrótami tracą dziś przez `MAX_DISTANCE`, nie przez
  ranking), przegląd etykiet z kandydatami z obu trybów i większy zbiór pytań ze skrótami.
  Próg odległości przestaje być problemem przy rerankingu (skróty: 8/8 z trafieniem po progu).
- **Reranking z datą** — reranker nie widzi daty publikacji, więc stare aktualności
  (rekrutacja 2016/2017) wygrywają z aktualnymi stronami; kara `OLD_NEWS_PENALTY` działa
  dziś tylko przed rerankingiem.
- **Wielomodalność** — upload plików PDF (np. regulaminów) i pytania na ich podstawie.
- **Panel administracyjny** — zarządzanie ingestią, przegląd fragmentów, monitoring jakości.
- **Ewaluacja automatyczna** — metryki jakości RAG (RAGAS, TruLens) do mierzenia
  trafności i halucynacji.
- **Konteneryzacja pełnej aplikacji** — `Dockerfile` dla API (`uvicorn`) i `cli.py`.
- **Pruning bazy** — automatyczne usuwanie fragmentów stron, które zniknęły ze źródła.
- **Limit 1 fragmentu na stronę** — na etykietach URL-owych wygrywa z obecnym limitem 2
  (Hit@8 0,854 → 0,896), ale może odcinać drugą sekcję strony z odpowiedzią; do decyzji
  po ewaluacji samych odpowiedzi LLM-a.
- **Follow-upy po zmianie wydziału** — „a wnst?” po pytaniu o dziekanat WH nadal słabo
  trafia w dziekanat WNŚiT. Rozwijanie skrótów pomaga częściowo: w trybie vector strona
  dziekanatu WNŚiT wchodzi do top-4 po pytaniu z pełną nazwą WH, ale po „Godziny
  dziekanatu WH” zostają strony WNŚiT niebędące dziekanatem.
- **Pokrycie bazy** — test 80 pytań ([Testy](testy.md), 6.5): z 44 stron wzorcowych w bazie
  jest 19; brakuje m.in. `rekrutacja.us.edu.pl`, CINiBA, Erasmus, CWFiS i najnowszych
  komunikatów. To główna przyczyna odmów na pytania potoczne — wyższy próg i przeformułowanie
  pytania przez LLM nie pomagają.
- **Pytania o egzaminy i oceny** — `qwen3:8b` myli zasady (poprawki, egzamin komisyjny,
  odwołania); do sprawdzenia reranking albo większy model.
- **Angielskie wersje portalu** (`/student/en/`) — pomijanie lub obniżanie w wynikach
  dla pytań po polsku.

---

*Dokument odzwierciedla stan kodu źródłowego repozytorium
[github.com/Erni0k/USBot](https://github.com/Erni0k/USBot). Wersja 0.6 — listopad 2026.*
