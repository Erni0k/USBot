"""Embedder na sentence-transformers (domyślnie BAAI/bge-m3, 1024 wymiary)."""
from collections.abc import Sequence
from functools import cached_property

from sentence_transformers import SentenceTransformer


class SentenceTransformerEmbedder:
    """Model ładowany leniwie — przy pierwszym embeddingu albo jawnie przez load().

    `load_message` (np. „Ładowanie modelu…”) jest wypisywany przed załadowaniem;
    None = bez komunikatu.
    """

    def __init__(self, model_name: str, load_message: str | None = None) -> None:
        self.model_name = model_name
        self.load_message = load_message

    @cached_property
    def model(self) -> SentenceTransformer:
        if self.load_message:
            print(self.load_message, flush=True)
        return SentenceTransformer(self.model_name)

    def load(self) -> None:
        self.model  # noqa: B018 — wymusza załadowanie

    def embed(self, text: str) -> list[float]:
        return self.model.encode(text, normalize_embeddings=True).tolist()

    def embed_batch(self, texts: Sequence[str], batch_size: int = 64):
        """Tablica numpy (n × wymiar) — psycopg z pgvector zapisuje jej wiersze wprost."""
        return self.model.encode(list(texts), normalize_embeddings=True, batch_size=batch_size)
