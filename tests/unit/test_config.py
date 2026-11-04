"""Settings: te same nazwy zmiennych env i ta sama interpretacja wartości co os.getenv."""
import pytest
from pydantic import ValidationError

from usbot.config import Settings


@pytest.fixture
def env(monkeypatch):
    for name in ("TOP_K", "MAX_DISTANCE", "RERANK", "RETRIEVAL_MODE", "ENABLE_THINKING",
                 "DATABASE_URL", "CRAWL_FRESH"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def settings():
    return Settings(_env_file=None)  # bez .env z repozytorium


def test_domyslne_wartosci(env):
    s = settings()
    assert (s.top_k, s.max_distance, s.max_per_url, s.retrieval_mode, s.rerank) == \
        (8, 0.43, 2, "vector", False)
    # te same wartości co w .env.example i tabelach w README / docs/api.md (3.5)
    assert (s.llm_model, s.crawl_limit, s.crawl_depth) == ("qwen3:8b", 6000, 3)
    assert s.llm_base_url == "http://127.0.0.1:11434/v1"


def test_nazwy_zmiennych_env(env):
    env.setenv("TOP_K", "3")
    env.setenv("MAX_DISTANCE", "0.5")
    env.setenv("DATABASE_URL", "postgresql://u:p@h:1/db")
    s = settings()
    assert (s.top_k, s.max_distance, s.database_url) == (3, 0.5, "postgresql://u:p@h:1/db")


@pytest.mark.parametrize("value, expected", [("true", True), ("TRUE", True), ("false", False),
                                             ("1", False), ("yes", False), ("", False)])
def test_bool_jak_dotad_tylko_true(env, value, expected):
    env.setenv("RERANK", value)
    env.setenv("CRAWL_FRESH", value)
    s = settings()
    assert s.rerank is expected and s.crawl_fresh is expected


def test_retrieval_mode_bez_wielkosci_liter_i_spacji(env):
    env.setenv("RETRIEVAL_MODE", " Hybrid ")
    assert settings().retrieval_mode == "hybrid"


def test_nieznany_tryb_blad(env):
    # T17 (część): nieznany RETRIEVAL_MODE → błąd przy wczytaniu ustawień
    env.setenv("RETRIEVAL_MODE", "foo")
    with pytest.raises(ValidationError):
        settings()
