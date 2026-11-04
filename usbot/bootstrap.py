"""Składanie aplikacji: adaptery według ustawień → porty → ChatService.

Jedyne miejsce (poza skryptami), które zna jednocześnie rdzeń i konkretne adaptery.
Z tych samych funkcji korzystają cli.py, evaluation/evaluate.py i ingestion/ingest.py
(oraz przyszła aplikacja webowa).
"""
from usbot.config import Settings
from usbot.core.chat import ChatService
from usbot.core.ranking import RerankingRetriever
from usbot.ports import Embedder, Retriever

EMBED_LOAD_MESSAGE = "Ładowanie modelu embeddingów (pierwsze uruchomienie może chwilę potrwać)..."
RERANK_LOAD_MESSAGE = "Ładowanie modelu rerankera (pierwsze uruchomienie może chwilę potrwać)..."


def build_embedder(settings: Settings, load_message: str | None = EMBED_LOAD_MESSAGE):
    from usbot.adapters.embeddings import SentenceTransformerEmbedder
    return SentenceTransformerEmbedder(settings.embed_model, load_message)


def build_retriever_parts(settings: Settings, embedder: Embedder | None = None, *,
                          pool=None, rerank_load_message: str | None = RERANK_LOAD_MESSAGE):
    """(retriever, retriever pgvector, reranker albo None) — części są potrzebne API
    (ładowanie rerankera przy starcie, sprawdzenie bazy w /api/health)."""
    from usbot.adapters.pgvector import PgvectorRetriever
    pg = PgvectorRetriever(
        settings.database_url, embedder or build_embedder(settings),
        mode=settings.retrieval_mode, old_news_penalty=settings.old_news_penalty,
        old_news_days=settings.old_news_days, max_per_url=settings.max_per_url, pool=pool,
    )
    if not settings.rerank:
        return pg, pg, None
    from usbot.adapters.reranker import CrossEncoderReranker
    reranker = CrossEncoderReranker(settings.rerank_model, rerank_load_message)
    return RerankingRetriever(pg, reranker), pg, reranker


def build_retriever(settings: Settings, embedder: Embedder | None = None) -> Retriever:
    """Retriever pgvector w trybie RETRIEVAL_MODE, z RERANK=true opakowany rerankerem."""
    return build_retriever_parts(settings, embedder)[0]


def build_llm(settings: Settings, *, max_retries: int = 2):
    from usbot.adapters.llm import OpenAICompatibleLLM
    return OpenAICompatibleLLM(settings.llm_base_url, settings.llm_api_key, settings.llm_model,
                               enable_thinking=settings.enable_thinking,
                               timeout=settings.llm_timeout, max_retries=max_retries,
                               reasoning_effort=settings.llm_reasoning_effort)


def build_chat_service(settings: Settings) -> ChatService:
    return ChatService(build_retriever(settings), build_llm(settings),
                       top_k=settings.top_k, max_distance=settings.max_distance,
                       min_rerank_score=settings.min_rerank_score)
