"""Pobiera modele Hugging Face potrzebne API i ingestii do cache'u (`HF_HOME`).

W kontenerach uruchamia go jednorazowa usługa `models` (compose.prod.yml) przed `api` i
`ingest`, które potem działają offline (`HF_HUB_OFFLINE=1`) z tego samego wolumenu.
Modele są ładowane tak jak w aplikacji (sentence-transformers, na CPU), więc w cache'u
lądują dokładnie potrzebne pliki (bez wersji ONNX itp.) i od razu wiadomo, że da się je
wczytać offline. Już pobrane pliki nie są pobierane ponownie.

    python -m usbot.download_models      # EMBED_MODEL, a przy RERANK=true także RERANK_MODEL
"""
import os
import sys
import time


def main() -> int:
    os.environ["HF_HUB_OFFLINE"] = "0"  # przed importem huggingface_hub (czyta to przy imporcie)
    from sentence_transformers import CrossEncoder, SentenceTransformer

    from usbot.config import get_settings

    settings = get_settings()
    jobs = [(settings.embed_model, SentenceTransformer)]
    if settings.rerank:
        jobs.append((settings.rerank_model, CrossEncoder))
    for name, cls in jobs:
        start = time.perf_counter()
        cls(name, device="cpu")
        print(f"model {name}: gotowy ({time.perf_counter() - start:.1f} s)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
