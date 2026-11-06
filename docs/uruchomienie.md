# 4. Instrukcja uruchomienia

## 4.1 Wymagania wstępne

| Narzędzie | Minimalna wersja | Cel |
|---|---|---|
| Python | 3.11 | Uruchamianie aplikacji i skryptów |
| pip | 23+ | Instalacja zależności Python |
| Docker | 24+ | Kontener bazy danych PostgreSQL |
| Docker Compose | v2 | Orkiestracja kontenerów |
| Ollama | dowolna (zalecane) | Lokalny serwer LLM |
| Git | 2+ | Klonowanie repozytorium |
| ~2 GB miejsca | — | Model embeddingów `BAAI/bge-m3` |
| ~5 GB miejsca | — | Model LLM `qwen3:8b` (Ollama) |
| ~2.3 GB miejsca (opcjonalnie) | — | Reranker `BAAI/bge-reranker-v2-m3` (`RERANK=true`) |
| ~8 GB pamięci GPU (zalecane) | — | bge-m3 (~2 GB) i `qwen3:8b` (~6 GB) na jednej karcie — tak mierzono czasy w [API](api.md) (3.2) |

!!! note "Bez GPU lub z GPU"
    Ollama działa też na CPU, ale model `qwen3:8b` jest wtedy wolny — rozważ
    `qwen3:4b` lub `qwen3:1.7b`. Z kartą NVIDIA odpowiedzi są szybkie.
    Alternatywą jest vLLM (wymaga `nvidia-container-toolkit`).

!!! note "Reranking (opcjonalnie, `RERANK=true`)"
    Reranker wymaga PyTorch z CUDA: na RTX 3080 ocena 20 fragmentów trwa ~90 ms (fp16),
    na CPU ~6 s na pytanie. Model pobierz raz, bo `cli.py` i API działają offline:
    `HF_HUB_OFFLINE=0 python -c "from sentence_transformers import CrossEncoder; CrossEncoder('BAAI/bge-reranker-v2-m3')"`.
    Przy `RERANK=true` próg odcięcia to `MIN_RERANK_SCORE` (domyślnie 0.01), a
    `MAX_DISTANCE` nie działa. Szczegóły: [Testy](testy.md) (6.4).

## 4.2 Instalacja i uruchomienie

### Krok 1 — Klonowanie repozytorium

```bash
git clone https://github.com/Erni0k/USBot.git
cd USBot
```

### Krok 2 — Konfiguracja środowiska

```bash
cp .env.example .env
# Otwórz .env i uzupełnij wartości (szczególnie LLM_BASE_URL i LLM_MODEL)
```

### Krok 3 — Instalacja zależności Python

```bash
pip install -r requirements.txt
```

### Krok 4 — Uruchomienie modelu językowego (Ollama, zalecane)

```bash
ollama pull qwen3:8b
ollama serve
# W .env ustaw:
#   LLM_BASE_URL=http://127.0.0.1:11434/v1
#   LLM_MODEL=qwen3:8b
```

### Krok 5 — Uruchomienie bazy danych

```bash
docker compose up -d db
```

### Krok 6 — Ingestia danych

```bash
python ingestion/ingest.py
# Pierwsze uruchomienie pobiera model embeddingów (~2 GB) z Hugging Face.
# Kolejne uruchomienia są inkrementalne — pobierają tylko zmienione strony.
```

### Krok 7 — Uruchomienie chatbota

```bash
python cli.py
```

Wpisz pytanie i naciśnij Enter. Wyjście: `wyjdź` lub `Ctrl+C`.

!!! warning "Kolejność startu"
    Zanim odpalisz bota, upewnij się, że **Ollama działa** (`ollama serve`) oraz
    **baza jest wstała** (`docker compose up -d db`).

### Krok 7b — API HTTP dla strony WWW (zamiast terminala)

```bash
uvicorn usbot.api.main:app --host 127.0.0.1 --port 8000
```

Serwer przy starcie ładuje model embeddingów (z `RERANK=true` także reranker), otwiera
pulę połączeń z bazą i tworzy klienta LLM-a — to trwa kilkanaście–kilkadziesiąt sekund;
gotowość sygnalizuje linia `Application startup complete`. Sprawdzenie:

```bash
curl http://127.0.0.1:8000/api/health        # "status": "ok" i 200, gdy baza, model i LLM działają
curl -N http://127.0.0.1:8000/api/chat -H 'Content-Type: application/json' \
  -d '{"message": "Jak połączyć się z siecią eduroam?", "history": []}'
```

- Przy vLLM na porcie 8000 uruchom API na innym porcie: `--port 8080`.
- Na Windows ustaw w `.env` `LLM_BASE_URL=http://127.0.0.1:11434/v1` — z `localhost`
  każde nowe połączenie z Ollamą czeka ~2–3 s na nieudaną próbę przez IPv6.
- Na Ollamie `ENABLE_THINKING=false` nie wyłącza rozumowania Qwen3; zrób to przez
  `LLM_REASONING_EFFORT=none` (pierwszy token kilka razy szybciej — [API](api.md), 3.2).
- Frontend z innego originu wymaga `API_CORS_ORIGINS` (np. `https://czat.example.pl`);
  serwer deweloperski strony (`npm run dev`, krok 7c) idzie przez proxy i go nie potrzebuje. Dokumentację OpenAPI (`/docs`) włącza lokalnie
  `API_DOCS=true`.

!!! warning "Publiczne wdrożenie"
    API nie ma kont użytkowników — chronią je limity (Architektura, 2.4). Przed
    wystawieniem w internecie:

    - uruchom za reverse proxy z HTTPS (np. nginx; strumienia SSE nie buforuj — serwer
      wysyła `X-Accel-Buffering: no`) i przekaż adres klienta:
      `uvicorn usbot.api.main:app --host 127.0.0.1 --port 8000 --proxy-headers
      --forwarded-allow-ips=127.0.0.1 --no-server-header`
      (bez `--proxy-headers` rate limiting widzi tylko adres proxy);
    - ustaw `API_CORS_ORIGINS` na domenę frontendu, np. `https://czat.us.edu.pl`, i
      `API_HSTS_SECONDS=31536000` (albo HSTS na proxy);
    - zmień hasło bazy z `changeme`; `.env` zostaje tylko na serwerze;
    - przy kilku procesach (`--workers`) lub instancjach ustaw
      `API_RATE_LIMIT_STORAGE=redis://…` — inaczej limity liczy każdy proces osobno;
    - dziennik rozmów (`logs/`) czyści serwer; gdy bywa wyłączony, dodaj do crona
      `python -m usbot.api.chatlog cleanup`; stronę czatu uzupełnij o informację o
      zapisywaniu pytań (RODO, Architektura 2.4);
    - po aktualizacji scrapera uruchom raz ingestię z `CRAWL_REFETCH=true` (usuwa tekst
      ukryty w HTML z istniejących stron);
    - przed wdrożeniem: `pip-audit -r requirements-dev.txt`.
- `--workers N` uruchamia N procesów, każdy z własną kopią modeli (bge-m3 ~2 GB pamięci GPU
  na proces). W jednym procesie żądania obsługują wątki: embedding i reranking idą po
  kolei (blokada na modelu), a równoległe odpowiedzi LLM-a obsługuje serwer modelu.

Kontrakt endpointów i format zdarzeń SSE: [Dokumentacja API](api.md) (3.1).

### Krok 7c — Strona WWW (czat w przeglądarce)

Wymaga Node.js 22.12+ i działającego API (krok 7b).

```bash
cd web
npm install
npm run dev
```

Czat jest pod `http://localhost:5173`. Serwer deweloperski Vite przekazuje `/api` do
`http://127.0.0.1:8000` (proxy), więc strona i API są dla przeglądarki jednym originem —
`API_CORS_ORIGINS` nie jest potrzebne. Inny adres API (np. `--port 8080` przy vLLM):
`VITE_DEV_PROXY_TARGET=http://127.0.0.1:8080` w `web/.env` (wzór: `web/.env.example`).

Wdrożenie: `npm run build` tworzy statyczne pliki w `web/dist/`. Najprościej serwować je
z tego samego reverse proxy co API (`/` → `web/dist/`, `/api` → uvicorn). Gdy strona jest
pod innym originem niż API, ustaw w `web/.env` przed budowaniem
`VITE_API_BASE_URL=https://api.example.pl`, a w `.env` API —
`API_CORS_ORIGINS=https://czat.example.pl`.

Opis interfejsu, stanów i dostępności: [Interfejs UI/UX](ui-ux.md).

### Krok 8 — Testy (opcjonalnie)

```bash
pip install -r requirements-dev.txt
pytest tests/unit          # bez Dockera, bazy i modeli
pytest tests/integration   # wymaga działającego Dockera (testcontainers)
```

Testy API (`tests/unit/test_api.py`) działają na atrapach — bez serwera, bazy i LLM-a.
Testy integracyjne same uruchamiają tymczasowy kontener `pgvector/pgvector:pg16` —
baza z kroku 5 nie jest potrzebna. Szczegóły: [Testy](testy.md) (6.3).

## 4.3 Ingestia — szczegóły

Pasek postępu pokazuje `+` (strona nowa/zmieniona) oraz `=` (bez zmian).
Crawl jest inkrementalny dzięki tabeli `pages` (ETag/Last-Modified + hash treści).

Pełne odświeżenie od zera (czyści `chunks` i `pages`):

```bash
# Linux / macOS
CRAWL_FRESH=true python ingestion/ingest.py

# Windows PowerShell
$env:CRAWL_FRESH="true"; python ingestion/ingest.py
```

Po zmianie ekstrakcji tekstu (`scrape.py`) pobierz wszystkie strony od nowa, ale bez
liczenia embeddingów dla stron, których tekst się nie zmienił:

```bash
# Linux / macOS
CRAWL_REFETCH=true python ingestion/ingest.py

# Windows PowerShell
$env:CRAWL_REFETCH="true"; python ingestion/ingest.py
```

`CRAWL_LIMIT` musi objąć całą bazę — strony, do których przebieg nie dojdzie,
zachowują starą ekstrakcję. Pełny przebieg `CRAWL_REFETCH=true CRAWL_LIMIT=12000`
trwa ok. 1,5 h: strony z cache serwera idą ~9 stron/s, a aktualności i wydarzenia
(`post`/`event`, odpowiedź ~1 s) ~2 strony/s. Ogranicza to serwer, nie GPU — CPU, GPU
i baza są w tym czasie w większości bezczynne. Log warto zapisać do pliku
(`*.log` jest w `.gitignore`):

```bash
CRAWL_REFETCH=true CRAWL_LIMIT=12000 python ingestion/ingest.py > ingest-refetch.log 2>&1
```

**Po zmianie chunkingu** (`_sections()` / `_chunks()` w `ingest.py`, rozmiar fragmentów,
prefiks) podbij stałą `CHUNKER_VERSION` w `ingestion/ingest.py`. Inny podział nie zmienia
tekstu strony, więc hash zostaje ten sam i `CRAWL_REFETCH` nie przeliczyłby embeddingów.
Wersja podziału jest zapisana w `pages.chunker_version`; przy starcie ingestia pobiera
(bez 304) i embeduje od nowa wszystkie strony z inną wersją — wystarczy zwykłe
`python ingestion/ingest.py`. Strony, do których przebieg nie dojdzie przed
`CRAWL_LIMIT`, zostaną przeliczone w kolejnym przebiegu. Pełne przeliczenie bazy
(~12 tys. stron) trwa, jak `CRAWL_REFETCH`, ok. 1,5 h.

Zachowanie crawlera dostroisz w `.env`: `CRAWL_LIMIT`, `CRAWL_DEPTH`,
`CRAWL_WORKERS`, `EMBED_BATCH` (patrz [Dokumentacja API](api.md)).
Uruchamiaj okresowo (np. cron raz dziennie), gdy treści na stronie się zmieniają.

## 4.4 Konfiguracja bazy danych

Inicjalizacja przebiega automatycznie przy pierwszym uruchomieniu kontenera — plik
`infra/init.sql` wykonywany jest przez mechanizm `docker-entrypoint-initdb.d`.
Tworzy on:

- rozszerzenia pgvector i unaccent (`CREATE EXTENSION IF NOT EXISTS vector`, `unaccent`)
  oraz funkcję `immutable_unaccent()`,
- tabelę `chunks` z indeksami `chunks_embedding_idx` (HNSW), `chunks_content_tsv_idx`
  (GIN, wyszukiwanie pełnotekstowe) i `chunks_url_idx`,
- tabelę `pages` (stan crawlera).

`init.sql` nie wykona się ponownie na istniejącym wolumenie, dlatego kolumny dodane
później (`chunks.page_type`, `chunks.published_at`, `chunks.content_hash`,
`chunks.body_hash`, `chunks.content_tsv`, `pages.chunker_version`, `pages.chunk_hashes`)
dokłada migracja w `_ensure_schema()` przy każdym starcie `ingestion/ingest.py`. `cli.py`
korzysta z `body_hash`, a tryb `RETRIEVAL_MODE=hybrid` z `content_tsv`, więc po
aktualizacji kodu uruchom ingestię przed botem. Dodanie `content_tsv` przepisuje całą
tabelę `chunks` (z indeksem HNSW) — pierwsza migracja trwa ok. minuty.

!!! info "Port bazy danych: 5433"
    Baza mapowana jest na port **5433** (`5433:5432` w `docker-compose.yml`), aby
    uniknąć konfliktu z lokalnym PostgreSQL na domyślnym porcie 5432. Zgodnie z tym
    `DATABASE_URL` w `.env` używa `localhost:5433`.

Dane persystowane są w wolumenie Docker `pgdata`. Reset bazy:

```bash
docker compose down -v        # usuwa wolumen pgdata
docker compose up -d db        # tworzy bazę od nowa
python ingestion/ingest.py     # ponowna ingestia
```

!!! danger "Nie commituj `.env`"
    Plik `.env` zawiera poufne dane dostępowe i nie należy go commitować do
    repozytorium (jest w `.gitignore`). Współdziel tylko `.env.example`.

## 4.5 Wdrożenie produkcyjne (Docker, GPU NVIDIA)

Całość — baza, model językowy, API, strona z HTTPS, harmonogram ingestii i kopie
zapasowe — startuje jedną komendą z `compose.prod.yml`:

```bash
docker compose -f compose.prod.yml up -d --build
```

(`docker-compose.yml` bez `-f` zostaje do pracy lokalnej: sama baza na porcie 5433.)

### Usługi

```text
 internet ──443/80──► web (Caddy) ──/api/*──► api (FastAPI) ──► llm (vLLM, Qwen3)
                      statyczny frontend        │ bge-m3 (GPU)
                      TLS automatyczny          └──► db (Postgres + pgvector)
                                                        ▲            ▲
        ingest (supercronic: codziennie / co tydzień) ──┘   backup (pg_dump co dobę)
        models (jednorazowo: modele Hugging Face na wolumen)
```

| Usługa | Obraz | Rola | Czeka na |
|---|---|---|---|
| `db` | `pgvector/pgvector:0.8.7-pg16-bookworm` | baza; `infra/init.sql` przy pierwszym starcie; healthcheck `pg_isready` | — |
| `llm` | `vllm/vllm-openai:v0.33.0` | Qwen3 przez API zgodne z OpenAI pod nazwą `qwen3`; `--reasoning-parser qwen3`, `enable_thinking=false`; GPU; healthcheck `GET /health` | — |
| `models` | `usbot-api` (`Dockerfile`) | jednorazowo pobiera `EMBED_MODEL` (przy `RERANK=true` także reranker) na wolumen `hf-cache` | — |
| `api` | `usbot-api` | `uvicorn usbot.api.main:app` za proxy (`--proxy-headers`); GPU (bge-m3); healthcheck `GET /api/health` | `db`, `llm` zdrowe; `models` zakończone |
| `web` | `usbot-web` (`web/Dockerfile`) | Caddy: `web/dist` + reverse proxy `/api/*` bez buforowania (SSE), TLS, nagłówki bezpieczeństwa (CSP); jedyna usługa z portami na zewnątrz (80, 443, 443/udp) | `api` zdrowe |
| `ingest` | `usbot-api` | harmonogram ingestii (supercronic) z blokadą `flock` — jeden przebieg naraz; pusta baza → przebieg od razu; GPU (bge-m3) | `db` zdrowa; `models` zakończone |
| `backup` | `prodrigestivill/postgres-backup-local:16-alpine` | `pg_dump` co dobę do wolumenu `backups` z rotacją | `db` zdrowa |

Wolumeny: `pgdata` (baza), `hf-cache` (bge-m3, reranker), `vllm-cache` (model LLM),
`chat-logs` (dziennik rozmów, 30 dni), `ingest-state` (blokada, crontab, znacznik
ostatniego udanego przebiegu), `caddy-data` (certyfikaty — nie usuwać: Let's Encrypt ma
limit wydań na domenę), `caddy-config`, `backups`.

Sekrety są tylko w `.env` na serwerze: obrazy ich nie zawierają (`.dockerignore`), a
compose przekazuje je kontenerom przy starcie. Obraz API działa jako użytkownik `app`
(UID 10001), bez roota.

### Wymagania sprzętowe

| Zasób | Minimum (test) | Zalecane (produkcja) |
|---|---|---|
| GPU NVIDIA | 10 GB (RTX 3080) — Qwen3-4B-AWQ | 24 GB (L4, A10, RTX 4090) — Qwen3-8B-AWQ; ≥ 40 GB — Qwen3-8B |
| Sterownik NVIDIA | ≥ 580 (obraz vLLM v0.33.0 jest na CUDA 13.0; karty serwerowe także 535/550/570 przez CUDA forward compatibility) — test: 610.88 | najnowszy z gałęzi produkcyjnej |
| RAM | 16 GB | 32 GB |
| CPU | 4 rdzenie | 8 rdzeni |
| Dysk | 80 GB: obrazy ~35 GB (vLLM ~24 GB, API ~10 GB po rozpakowaniu), cache budowania ~18 GB, modele (bge-m3 2,3 GB + LLM 2,7–16 GB), baza ~0,7 GB, kopie ~0,25 GB każda | 150 GB SSD |
| System | Docker Engine 24+ z Compose v2 i NVIDIA Container Toolkit; Windows 11 + Docker Desktop (WSL2) — tylko do testów | Ubuntu 24.04 LTS |
| Sieć | — | publiczny adres, DNS domeny → serwer, otwarte 80/tcp, 443/tcp, 443/udp |

**Dobór modelu do pamięci GPU.** vLLM rezerwuje `VLLM_GPU_MEMORY_UTILIZATION` × całą
pamięć karty (wagi + KV cache), a obok na tej samej karcie działa bge-m3 w API
(~2,3 GB) i — w czasie ingestii — drugi bge-m3 w `ingest`. KV cache Qwen3-8B to
~0,14 MB na token: jedna rozmowa o pełnej długości 8192 tokenów to ~1,2 GB, cztery
równoległe (`API_MAX_STREAMS=4`) — do ~4,8 GB (gdy brakuje, vLLM kolejkuje).

| Pamięć GPU | `VLLM_MODEL` | Wagi | `VLLM_GPU_MEMORY_UTILIZATION` | Uwagi |
|---|---|---|---|---|
| 10–12 GB | `Qwen/Qwen3-4B-AWQ` | ~2,7 GB | 0,45 (10 GB) / 0,5 (12 GB) | słabsze odpowiedzi niż 8B; tak przebiegł test lokalny |
| 16 GB | `Qwen/Qwen3-8B-AWQ` | ~6 GB | 0,6 | |
| 24 GB | `Qwen/Qwen3-8B-AWQ` | ~6 GB | 0,6 (domyślnie) | zapas na 4 równoległe rozmowy i ingestię |
| 24 GB | `Qwen/Qwen3-8B-FP8` | ~9,4 GB | 0,7 | bliżej jakości bf16 niż AWQ; FP8 wymaga GPU Ada/Hopper (RTX 40xx, L4, H100) |
| ≥ 40 GB | `Qwen/Qwen3-8B` (bf16) | ~16,4 GB | 0,6 | pełna precyzja; 8B w bf16 potrzebuje ~16–20 GB z KV cache, więc na 24 GB nie zmieści się obok bge-m3 i ingestii |

Bez GPU obraz API da się zbudować z `--build-arg TORCH_INDEX=https://download.pytorch.org/whl/cpu`
(embedding pytania ~0,1 s, ingestia wielokrotnie wolniej), a zamiast usługi `llm` użyć
Ollamy — tego wariantu `compose.prod.yml` nie obejmuje.

### Instrukcja krok po kroku

1. **Docker i GPU.** Zainstaluj [Docker Engine](https://docs.docker.com/engine/install/)
   z wtyczką Compose, sterownik NVIDIA i
   [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html),
   potem `sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker`.
   Sprawdzenie, czy kontener widzi kartę:

    ```bash
    docker run --rm --gpus all ubuntu nvidia-smi
    ```

2. **DNS i zapora.** Rekord A/AAAA domeny (np. `czat.us.edu.pl`) wskazuje serwer; porty
   80/tcp, 443/tcp i 443/udp są otwarte z internetu (80 jest potrzebny do wydania
   certyfikatu i przekierowania na HTTPS).

3. **Kod i konfiguracja.**

    ```bash
    git clone https://github.com/Erni0k/USBot.git && cd USBot
    cp .env.example .env
    chmod 600 .env
    ```

    W `.env` ustaw co najmniej: `POSTGRES_PASSWORD` (losowe, bez `@ : / ? #`, np.
    `openssl rand -hex 24`), `SITE_ADDRESS` (domena), `VLLM_MODEL` i
    `VLLM_GPU_MEMORY_UTILIZATION` (tabela wyżej) oraz `CRAWL_LIMIT=15000` — limit musi
    objąć całą bazę, ~12 tys. stron (4.3). `DATABASE_URL`, `LLM_BASE_URL`, `LLM_MODEL` i
    `LOG_FORMAT` z `.env` w kontenerach nie działają — ustawia je `compose.prod.yml`.

4. **Start.**

    ```bash
    docker compose -f compose.prod.yml up -d --build
    docker compose -f compose.prod.yml ps
    ```

    Kolejności pilnują healthchecki: `db` → `models` (pobiera bge-m3) i `llm` (pobiera
    model i kompiluje grafy CUDA) → `api` (ładuje bge-m3) → `web` (Caddy wydaje
    certyfikat). Test lokalny (RTX 3080 10 GB, Qwen3-4B-AWQ): pierwsze budowanie obrazów
    ~11 min (głównie torch z CUDA), pobranie i start vLLM ~2,5 min (kolejne starty ~2 min —
    kompilacja grafów), `models` za pierwszym razem pobiera bge-m3 (~2,3 GB; potem 3 s), `api` gotowe ~10 s
    po starcie, `web` od razu. Postęp:
    `docker compose -f compose.prod.yml logs -f llm api web`.

5. **Pierwsza ingestia.** Na pustej bazie `ingest` od razu uruchamia przebieg (pełny crawl
   ~1,5 h); do tego czasu czat odpowiada „Nie mam tej informacji…”. Zamiast czekać można
   odtworzyć bazę z kopii („Odtwarzanie bazy”).

    ```bash
    docker compose -f compose.prod.yml logs -f ingest
    ```

6. **Sprawdzenie.** `https://<domena>` — czat; `curl https://<domena>/api/health` —
   `"status": "ok"`; metryki:
   `docker compose -f compose.prod.yml exec api curl -s localhost:8000/api/metrics`.

7. **Autostart.** Usługi mają `restart: unless-stopped`, więc po restarcie serwera wstają
   same (o ile Docker startuje z systemem: `sudo systemctl enable docker`).

### Weryfikacja (test lokalny, 2026-11-06)

Windows 11 + Docker Desktop (WSL2), RTX 3080 10 GB (~2,5 GB zajmuje pulpit),
`Qwen/Qwen3-4B-AWQ` z `VLLM_GPU_MEMORY_UTILIZATION=0.45`. Osobny plik konfiguracji
(`.env.prod.local` z `USBOT_ENV_FILE=.env.prod.local`), żeby nie ruszać `.env` do pracy
lokalnej, oraz `CRAWL_LIMIT=20` i `INGEST_SCHEDULE="*/4 * * * *"`, żeby zobaczyć harmonogram:

```bash
docker compose --env-file .env.prod.local -f compose.prod.yml up -d --build
```

| Sprawdzenie | Wynik |
|---|---|
| start od zera (nowe wolumeny) | wszystkie usługi `healthy`, `models` `Exited (0)` |
| pusta baza → ingestia od razu | 20 stron, 102 fragmenty w 70 s; czat odpowiadał już ze stron eduroam |
| harmonogram | przebiegi co 4 min (`job succeeded`), czas `Europe/Warsaw`; drugi przebieg: 20 stron bez zmian, 0 embeddingów |
| jeden przebieg naraz | drugi `run.sh` w tym samym kontenerze i w `docker compose run` — „poprzedni przebieg jeszcze trwa — pomijam” |
| HTTPS | `https://localhost` (certyfikat lokalnego CA Caddy), HTTP → 308 na HTTPS, HTTP/2 i HTTP/3 (`Alt-Svc`) |
| nagłówki | CSP `script-src 'self'` bez błędów w konsoli, `X-Frame-Options: DENY`, `Cache-Control` |
| SSE przez Caddy | „Jak połączyć się z siecią eduroam?” — 3 źródła, 181 tokenów, pierwszy token po 0,74 s, całość 2,2 s |
| `/api/metrics` | z zewnątrz 404, z `exec api curl` liczniki; przerwana odpowiedź liczona jako `cancelled` |
| logi | API i Caddy w JSON, bez adresów IP i nagłówków klienta |
| kopia i odtworzenie | `backup.sh`: 237 MB w 31 s; odtworzenie bazy deweloperskiej (42 330 fragmentów, 12 055 stron) do pustej bazy — 67 s, wszystkie indeksy |
| czat w przeglądarce | `WEB_URL=https://localhost/ node web/scripts/e2e.mjs <katalog>` ([UI/UX](ui-ux.md), 5.5): wszystkie scenariusze OK — strumieniowanie, źródła, `clarify` → WNP, `refused`, `Esc`, `429`, tryb ciemny, telefon |

Nie sprawdzono lokalnie: certyfikatu Let's Encrypt dla prawdziwej domeny (wymaga DNS i
portów 80/443 z internetu) ani modeli 8B (za mało pamięci GPU).

### Wybory projektowe

**Modele Hugging Face na wolumenie, nie w obrazie.** Obraz API zawiera kod i zależności
(torch z CUDA), a bge-m3 (~2,3 GB) i reranker (~2,3 GB) pobiera jednorazowa usługa
`models` do wolumenu `hf-cache`, z którego `api` i `ingest` czytają offline
(`HF_HUB_OFFLINE=1`). Za: obraz mniejszy o 2,3–4,6 GB, szybsze budowanie i wysyłanie do
rejestru (zmiana kodu nie przenosi modeli), jedna kopia modelu dla `api` i `ingest`,
zmiana `EMBED_MODEL` / `RERANK` bez przebudowy. Przeciw: pierwszy start potrzebuje
dostępu do Hugging Face. Na serwer bez tego dostępu wolumen da się przenieść:
`docker run --rm -v usbot-prod_hf-cache:/m -v "$PWD":/b alpine tar czf /b/hf.tgz -C /m .`
(i odwrotnie `tar xzf`). vLLM ma osobny wolumen `vllm-cache`, bo jego obraz działa jako
root.

**Harmonogram ingestii: supercronic w kontenerze `ingest`, nie ofelia.** Ofelia uruchamia
zadania przez gniazdo Dockera, a kontener z `/var/run/docker.sock` ma w praktyce
uprawnienia roota na hoście. Supercronic to cron dla kontenerów: działa jako zwykły
użytkownik, pisze wyjście zadań do logów kontenera i bierze harmonogram z
`INGEST_SCHEDULE` / `INGEST_REFETCH_SCHEDULE` (składnia cron, strefa `TZ`). Domyślnie:
pon–sob o 3:00 przyrostowo, w niedzielę o 3:00 `CRAWL_REFETCH=true`. Jeden przebieg naraz
pilnuje `flock` na wolumenie `ingest-state` (`infra/ingest/run.sh`), także przy
uruchomieniu ręcznym — przebieg, który zastanie blokadę, kończy się komunikatem
„poprzedni przebieg jeszcze trwa — pomijam”. Model zajmuje pamięć GPU tylko w czasie
przebiegu (osobny proces).

```bash
# ręcznie, np. po zmianie scrapera (ta sama blokada co harmonogram)
docker compose -f compose.prod.yml exec ingest /app/infra/ingest/run.sh refetch
# kiedy był ostatni udany przebieg
docker compose -f compose.prod.yml exec ingest cat /data/state/last-success
```

**Monitoring: healthchecki, logi JSON i `/api/metrics` w formacie Prometheusa.**
Healthcheck `api` to `GET /api/health` (baza, embeddingi, LLM), więc
`docker compose ps` pokaże `unhealthy`, gdy padnie którakolwiek zależność. API pisze logi
jako JSON (`LOG_FORMAT=json`: `ts`, `level`, `logger`, `msg`, w dzienniku dostępu
`method`, `path`, `status` — bez IP), Caddy także; Docker trzyma je z rotacją
(5 × 20 MB na usługę):

```bash
docker compose -f compose.prod.yml logs api --no-log-prefix \
  | jq -cR 'fromjson? | select(.level=="ERROR" or .level=="WARNING")'
```

`/api/metrics` ([API](api.md), 3.1) podaje liczbę odpowiedzi według wyniku, odsetek
`refused` i histogramy czasu do pierwszego tokenu i całej odpowiedzi (plus mediana i p95
z ostatnich 500 odpowiedzi). Format tekstowy Prometheusa czyta i człowiek (`curl`), i —
bez zmian w kodzie — Prometheus, gdy powstanie (scrape `api:8000/api/metrics` w sieci
compose); osobny JSON służyłby tylko człowiekowi, a pełny stos (Prometheus + Grafana) to
dwie usługi więcej do utrzymania przy jednym serwerze. Liczniki są w pamięci procesu
(restart je zeruje), bez dodatkowej zależności. Caddy odpowiada na `/api/metrics` 404 —
metryki nie są publiczne.

**Kopie zapasowe: `pg_dump` co dobę, a w razie braku kopii — crawl.** Usługa `backup`
robi pełny `pg_dump` (z `CREATE EXTENSION`, gzip) o 2:00, przed ingestią o 3:00, i trzyma 7
dziennych, 4 tygodniowe i 3 miesięczne kopie w wolumenie `backups`. Zrzut bazy z 42 tys.
fragmentów (708 MB w Postgresie) to plik ~237 MB, robiony w ~30 s; odtworzenie z budową
indeksów trwa ~70 s (test lokalny). Bez kopii baza jest
odtwarzalna z crawla: pełny przebieg (~12 tys. stron) trwa **ok. 1,5 h** — ogranicza go
serwer uczelni, nie GPU (4.3) — ale stron, które zniknęły z serwisów, już się nie odzyska.
Kopie warto co jakiś czas wynosić poza serwer:

```bash
docker compose -f compose.prod.yml cp backup:/backups ./backups
```

### Odtwarzanie bazy

Z kopii (`/backups/last/`, `daily/`, `weekly/`, `monthly/` w kontenerze `backup`;
użytkownik i baza jak w `POSTGRES_USER` / `POSTGRES_DB`):

```bash
C="docker compose -f compose.prod.yml"
$C stop api ingest                                  # nikt nie czyta ani nie pisze
$C exec db dropdb -U chatbot chatbot
$C exec db createdb -U chatbot chatbot
$C exec backup ls -l /backups/last /backups/daily   # wybierz plik
$C exec -T backup cat /backups/last/chatbot-latest.sql.gz \
  | gunzip | $C exec -T db psql -q -U chatbot -d chatbot -v ON_ERROR_STOP=1
$C start api ingest
```

Zrzut zawiera rozszerzenia, tabele, dane i indeksy (HNSW buduje się przy odtwarzaniu).
Kopię spoza serwera (`./backups/...sql.gz`) wczytuje się tak samo:
`gunzip -c plik.sql.gz | $C exec -T db psql …`.

Z crawla (bez kopii): `$C stop api ingest`, `dropdb` / `createdb` jak wyżej, schemat
`$C exec -T db psql -U chatbot -d chatbot < infra/init.sql`, potem `$C start ingest` —
pusta baza uruchamia pełny przebieg od razu (~1,5 h) — i `$C start api`.

### Aktualizacja wersji

```bash
git pull
docker compose -f compose.prod.yml up -d --build   # przebudowuje i podmienia zmienione usługi
docker image prune -f                               # stare warstwy
```

- Przed aktualizacją zmieniającą schemat bazy albo ekstrakcję zrób kopię:
  `$C exec backup /backup.sh`.
- `api` po podmianie ładuje bge-m3 (~10–40 s) — w tym czasie czat zwraca błąd
  502; aktualizuj poza godzinami zajęć.
- Po zmianie scrapera — przebieg `refetch` (wyżej); po zmianie chunkingu wystarczy
  zwykły przebieg (`CHUNKER_VERSION`, 4.3).
- Nowy vLLM: `VLLM_IMAGE_TAG` w `.env` (sprawdź w liście zmian flagi
  `--reasoning-parser` i `--default-chat-template-kwargs`), potem `$C up -d llm`.
- Inny model: `VLLM_MODEL` w `.env` i `$C up -d llm`. API zawsze woła `qwen3`
  (`--served-model-name`), więc jego konfiguracji się nie zmienia.
- Wersje obrazów: `USBOT_VERSION=2026.11.06 $C up -d --build` taguje obrazy; powrót to
  `USBOT_VERSION=<poprzednia> $C up -d` (bez `--build`).

### Problemy

| Objaw | Przyczyna / rozwiązanie |
|---|---|
| `llm` długo `starting`, potem `unhealthy` | pierwszy start pobiera model (`$C logs llm`); `CUDA out of memory` — mniejszy `VLLM_GPU_MEMORY_UTILIZATION` albo mniejszy model (tabela) |
| `api` `unhealthy` | `$C exec api curl -s localhost:8000/api/health` pokaże, które sprawdzenie nie przechodzi |
| przeglądarka ostrzega o certyfikacie | `SITE_ADDRESS=localhost` = certyfikat z lokalnego CA Caddy (test); przy domenie — DNS i porty 80/443 (`$C logs web`) |
| `llm` restartuje się z `KV cache is needed, which is larger than the available KV cache memory` | na karcie współdzielonej z pulpitem (Windows) albo innym procesem wolna pamięć zmienia się między startami — zwiększ `VLLM_GPU_MEMORY_UTILIZATION` o 0,02–0,05 albo zmniejsz `VLLM_MAX_MODEL_LEN` (np. 6144) |
| usługa nie startuje: `API_CORS_ORIGINS: '# domena…' nie jest originem` | w `.env` po pustej wartości stoi komentarz w tej samej linii — compose bierze go jako wartość; przenieś komentarz do osobnej linii |
| `ingest` kończy się `CUDA out of memory` | ingestia potrzebuje pamięci GPU obok vLLM i API — zmniejsz `VLLM_GPU_MEMORY_UTILIZATION` |
| wszyscy dostają „Za dużo pytań” | rate limit widzi adres proxy zamiast klienta — API musi dostawać `X-Forwarded-For` (uvicorn z `--proxy-headers`, jest w obrazie) |
