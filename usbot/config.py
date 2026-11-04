"""Ustawienia aplikacji z zmiennych środowiskowych i pliku `.env` (pydantic-settings).

Jedno miejsce zamiast `os.getenv` rozsianych po skryptach. Nazwy zmiennych są te same
co wcześniej (`DATABASE_URL`, `TOP_K`, `RERANK`…), opis każdej: README („Konfiguracja”)
i docs/api.md (3.3).

Kolejność źródeł jak przy `load_dotenv()`: zmienna środowiskowa wygrywa z `.env`.
Plik `.env` czytany jest z katalogu głównego repozytorium niezależnie od katalogu
roboczego (tak jak `load_dotenv()` szukało go w górę od skryptu). Inne klucze z `.env`
(np. `POSTGRES_USER` dla Dockera) są ignorowane i — inaczej niż przy `load_dotenv()` —
nie trafiają do `os.environ`.
"""
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BeforeValidator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


def _env_bool(v: object) -> object:
    """Tak jak dotąd `os.getenv(...).lower() == "true"`: tylko „true” (dowolna wielkość
    liter) włącza opcję, każda inna wartość ją wyłącza."""
    return v.strip().lower() == "true" if isinstance(v, str) else v


def _lower(v: object) -> object:
    return v.strip().lower() if isinstance(v, str) else v


EnvBool = Annotated[bool, BeforeValidator(_env_bool)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8",
                                      extra="ignore")

    # --- baza ---
    database_url: str = "postgresql://chatbot:changeme@localhost:5433/chatbot"

    # --- LLM (API zgodne z OpenAI: Ollama, vLLM) ---
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "not-needed"
    llm_model: str = "qwen3:1.7b"
    enable_thinking: EnvBool = False

    # --- embeddingi i reranker ---
    embed_model: str = "BAAI/bge-m3"
    rerank_model: str = "BAAI/bge-reranker-v2-m3"

    # --- retrieval ---
    top_k: int = 5
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

    # --- ingestia ---
    crawl_limit: int = 2000
    crawl_depth: int = 2
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
