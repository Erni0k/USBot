"""Zasoby API tworzone raz przy starcie (lifespan): modele, pula połączeń, klient LLM-a.

Odpowiednik usbot.bootstrap dla serwera — składa te same adaptery, ale z pulą połączeń,
timeoutami i sprawdzeniami dla /api/health. Żadne żądanie nie ładuje modelu.
"""
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from usbot.api.sse import ChatStreamer
from usbot.config import Settings

log = logging.getLogger("usbot.api")

# Sprawdzenie stanu: zwraca szczegóły (dict) albo rzuca wyjątek z opisem problemu.
HealthCheck = Callable[[], dict]


@dataclass
class Resources:
    service: ChatStreamer
    checks: dict[str, HealthCheck] = field(default_factory=dict)
    close: Callable[[], None] = lambda: None


def build_resources(settings: Settings) -> Resources:
    """ChatService na prawdziwych adapterach. Wywoływane w wątku przy starcie serwera."""
    from usbot.adapters.pgvector import create_pool
    from usbot.bootstrap import build_embedder, build_llm, build_retriever_parts
    from usbot.core.chat import ChatService

    log.info("ładowanie modelu embeddingów %s", settings.embed_model)
    embedder = build_embedder(settings, load_message=None)
    embedder.load()

    pool = create_pool(settings.database_url, min_size=settings.db_pool_min,
                       max_size=settings.db_pool_max, timeout=settings.db_timeout)
    # Bez czekania: serwer wstaje także przy niedostępnej bazie (/api/health to pokaże),
    # a pula łączy się w tle.
    pool.open(wait=False)

    retriever, pg, reranker = build_retriever_parts(settings, embedder, pool=pool,
                                                    rerank_load_message=None)
    if reranker is not None:
        log.info("ładowanie modelu rerankera %s", settings.rerank_model)
        reranker.load()

    # Bez ponawiania: klient czeka na strumień, a błąd i tak trafia do niego jako `error`
    llm = build_llm(settings, max_retries=0)
    service = ChatService(retriever, llm, top_k=settings.top_k,
                          max_distance=settings.max_distance,
                          min_rerank_score=settings.min_rerank_score,
                          max_history_turns=settings.api_history_turns)

    timeout = settings.health_timeout
    checks: dict[str, HealthCheck] = {
        "database": lambda: pg.ping(timeout),
        "embeddings": lambda: {"model": settings.embed_model,
                               "dim": len(embedder.embed("test"))},
        "llm": lambda: llm.ping(timeout),
    }
    if reranker is not None:
        checks["reranker"] = lambda: {"model": settings.rerank_model,
                                      "score": round(reranker.score(["test"], ["test"])[0], 3)}
    return Resources(service=service, checks=checks, close=pool.close)
