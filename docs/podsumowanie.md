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
  równoległe pobieranie i batch embedding.
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
- **Równoległe pobieranie** (`ThreadPoolExecutor`) + **batch embedding** i batch insert.
- Crawl wielu domen: `us.edu.pl`, `usnet.us.edu.pl`, `eduroam.us.edu.pl`.
- Skrypt ingestii z chunkingiem, paskiem postępu (`tqdm`) i trybem `CRAWL_FRESH`.
- Infrastruktura Docker (PostgreSQL + pgvector) z automatyczną inicjalizacją bazy.
- Obsługa dwóch backendów LLM: Ollama (CPU/GPU) i vLLM (GPU).
- Tryb rozumowania Qwen3 (`ENABLE_THINKING`) i pełna konfiguracja przez `.env`.

### Niezrealizowane (planowane)

- Interfejs webowy — zaplanowany jako rozszerzenie projektu.
- Testy automatyczne (pytest) — do zaimplementowania w kolejnych iteracjach.
- CI/CD (GitHub Actions) — nieskonfigurowane w obecnej wersji.
- Uwierzytelnianie użytkowników — brak w wersji CLI.
- Monitoring i logowanie strukturalne — brak dedykowanego systemu logów.
- Pruning nieaktualnych stron — usunięte ze źródła strony pozostają w bazie do
  najbliższego pełnego re-crawla (`CRAWL_FRESH=true`).

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

---

*Dokument odzwierciedla stan kodu źródłowego repozytorium
[github.com/Erni0k/USBot](https://github.com/Erni0k/USBot). Wersja 0.2 — czerwiec 2026.*
