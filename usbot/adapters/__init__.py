"""Adaptery — implementacje portów z `usbot.ports` dla konkretnych technologii.

- `embeddings` — SentenceTransformer (BAAI/bge-m3)            → Embedder
- `reranker`   — CrossEncoder (BAAI/bge-reranker-v2-m3)      → Reranker
- `pgvector`   — PostgreSQL + pgvector (+ FTS, RRF)          → Retriever
- `llm`        — API zgodne z OpenAI (Ollama, vLLM)          → ChatLLM
"""
import os

# Modele są cache'owane lokalnie po pierwszym pobraniu — pracuj offline. Usuwa
# ostrzeżenie o HF Hub i zawieszanie przy starcie. Pierwsze pobranie: HF_HUB_OFFLINE=0.
# Musi być ustawione przed importem sentence_transformers / huggingface_hub.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
