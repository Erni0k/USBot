"""Ustawienia aplikacji z zmiennych środowiskowych i pliku `.env` (pydantic-settings).

Jedno miejsce zamiast `os.getenv` rozsianych po skryptach. Nazwy zmiennych są te same
co wcześniej (`DATABASE_URL`, `TOP_K`, `RERANK`…), opis każdej: README („Konfiguracja”)
i docs/api.md (3.5).

Kolejność źródeł jak przy `load_dotenv()`: zmienna środowiskowa wygrywa z `.env`.
Plik `.env` czytany jest z katalogu głównego repozytorium niezależnie od katalogu
roboczego (tak jak `load_dotenv()` szukało go w górę od skryptu). Inne klucze z `.env`
(np. `POSTGRES_USER` dla Dockera) są ignorowane i — inaczej niż przy `load_dotenv()` —
nie trafiają do `os.environ`.
"""
from functools import lru_cache
from pathlib import Path
import re
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


def _env_bool(v: object) -> object:
    """Tak jak dotąd `os.getenv(...).lower() == "true"`: tylko „true” (dowolna wielkość
    liter) włącza opcję, każda inna wartość ją wyłącza."""
    return v.strip().lower() == "true" if isinstance(v, str) else v


def _lower(v: object) -> object:
    return v.strip().lower() if isinstance(v, str) else v


EnvBool = Annotated[bool, BeforeValidator(_env_bool)]

# Origin frontendu: schemat://host[:port], bez ścieżki i bez „*”
_ORIGIN = re.compile(r"https?://[A-Za-z0-9.-]+(:\d{1,5})?")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8",
                                      extra="ignore")

    # --- baza ---
    database_url: str = "postgresql://chatbot:changeme@localhost:5433/chatbot"

    # --- LLM (API zgodne z OpenAI: Ollama, vLLM) ---
    llm_base_url: str = "http://127.0.0.1:11434/v1"  # nie localhost: na Windows +2 s na połączenie
    llm_api_key: str = "not-needed"
    llm_model: str = "qwen3:8b"
    enable_thinking: EnvBool = False
    # `reasoning_effort` wysyłany do API (np. "none"). Ollama ignoruje
    # chat_template_kwargs.enable_thinking — Qwen3 wyłącza tam dopiero
    # LLM_REASONING_EFFORT=none. Puste = parametr nie jest wysyłany (vLLM może go odrzucić).
    llm_reasoning_effort: str | None = None
    # Limit czasu na połączenie z LLM-em i na każdy kolejny fragment strumienia (sekundy).
    llm_timeout: float = 60.0

    # --- embeddingi i reranker ---
    embed_model: str = "BAAI/bge-m3"
    rerank_model: str = "BAAI/bge-reranker-v2-m3"

    # --- retrieval ---
    top_k: int = 8
    # Fragmenty dalsze niż próg (odległość kosinusowa) są odrzucane; gdy nie zostanie
    # żaden, bot odpowiada NO_INFO bez wywołania LLM-a. Wybór progu: docs/testy.md (6.4).
    max_distance: float = 0.43
    # Kara do odległości (tylko przy sortowaniu) dla aktualności i wydarzeń starszych niż
    # OLD_NEWS_DAYS. 0 = wyłączone. Wpływ na wyniki: docs/testy.md (6.4).
    old_news_penalty: float = 0.0
    old_news_days: int = 730
    # Najwyżej tyle fragmentów z jednej strony (URL-a) w wynikach; 0 = bez limitu.
    max_per_url: int = 2
    # vector = samo wyszukiwanie wektorowe; hybrid = wektorowe + pełnotekstowe (RRF).
    retrieval_mode: Annotated[Literal["vector", "hybrid"], BeforeValidator(_lower)] = "vector"
    # Reranking: RERANK_CANDIDATES najlepszych fragmentów ocenia cross-encoder i zostaje TOP_K.
    rerank: EnvBool = False
    # Przy RERANK=true próg dotyczy wyniku rerankera (0–1, większy = lepszy), nie odległości.
    min_rerank_score: float = 0.01

    # --- API HTTP (usbot/api) ---
    # Ile ostatnich tur (pytanie + odpowiedź) historii klienta trafia do ChatService.
    api_history_turns: int = Field(5, ge=0)
    # Dozwolone originy CORS, po przecinku (np. http://localhost:5173); puste = bez CORS.
    api_cors_origins: str = ""
    # Pula połączeń z bazą: rozmiar i limit czasu (sekundy) na połączenie, pobranie
    # połączenia z puli i pojedyncze zapytanie (statement_timeout).
    db_pool_min: int = Field(1, ge=0)
    db_pool_max: int = Field(4, ge=1)
    db_timeout: float = Field(10.0, gt=0)
    # Limit czasu (sekundy) jednego sprawdzenia w GET /api/health.
    health_timeout: float = Field(3.0, gt=0)
    # Limity żądania POST /api/chat (przekroczenie → 422; ciało ponad API_MAX_BODY_BYTES → 413).
    api_max_message_chars: int = Field(2000, ge=1)
    api_max_history_messages: int = Field(40, ge=0)
    api_max_history_chars: int = Field(40_000, ge=0)   # suma treści wiadomości historii
    api_max_body_bytes: int = Field(256 * 1024, ge=1024)
    # Rate limiting na adres IP (składnia slowapi/limits, kilka limitów po „;”).
    api_rate_limit_chat: str = "10/minute;200/day"
    api_rate_limit_default: str = "60/minute"        # /api/health, /api/faculties
    api_rate_limit_storage: str = "memory://"         # przy kilku procesach: redis://…
    # Ile odpowiedzi generuje się naraz: w sumie i dla jednego adresu IP.
    api_max_streams: int = Field(4, ge=1)
    api_max_streams_per_client: int = Field(2, ge=1)
    # Dokumentacja OpenAPI (/docs, /redoc, /openapi.json) — w publicznym wdrożeniu wyłączona.
    api_docs: EnvBool = False
    # Strict-Transport-Security (sekundy); 0 = nie wysyłaj (gdy HTTPS kończy reverse proxy,
    # ustaw nagłówek tam albo tutaj).
    api_hsts_seconds: int = Field(0, ge=0)

    # --- dziennik rozmów (usbot/api/chatlog.py, opis: docs/architektura.md) ---
    chat_log_enabled: EnvBool = True
    chat_log_dir: str = str(ROOT / "logs")
    chat_log_retention_days: int = Field(30, ge=1)
    chat_log_max_question_chars: int = Field(500, ge=0)

    @field_validator("api_cors_origins")
    @classmethod
    def _cors_origins(cls, v: str) -> str:
        """Tylko konkretne originy frontendu (schemat://host[:port]); bez „*” i ścieżek."""
        for origin in (o.strip() for o in v.split(",")):
            if origin and not _ORIGIN.fullmatch(origin):
                raise ValueError(f"API_CORS_ORIGINS: {origin!r} nie jest originem "
                                 "(np. https://czat.us.edu.pl); „*” jest niedozwolone")
        return v

    @field_validator("chat_log_dir")
    @classmethod
    def _log_dir(cls, v: str) -> str:
        """Ścieżka względna — względem katalogu repozytorium, nie katalogu roboczego."""
        return v if Path(v).is_absolute() else str(ROOT / v)

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.api_cors_origins.split(",") if o.strip()]

    # --- ingestia ---
    crawl_limit: int = 6000
    crawl_depth: int = 3
    crawl_workers: int = 6
    embed_batch: int = 16          # ile stron naraz do embeddingu
    crawl_fresh: EnvBool = False
    crawl_refetch: EnvBool = False  # po zmianie ekstrakcji tekstu
    # Fragment, którego treść (bez prefiksu) występuje na więcej niż BOILERPLATE_URLS
    # stronach, to boilerplate i nie trafia do chunks. 0 = wyłączone.
    boilerplate_urls: int = 20


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Ustawienia wczytane raz na proces."""
    return Settings()
