"""Reranker na cross-encoderze sentence-transformers (domyślnie BAAI/bge-reranker-v2-m3)."""
from collections.abc import Sequence
from functools import cached_property

import torch
from sentence_transformers import CrossEncoder


class CrossEncoderReranker:
    """Ocena par (zapytanie, tekst); model ładowany leniwie przy pierwszym użyciu."""

    def __init__(self, model_name: str, load_message: str | None = None) -> None:
        self.model_name = model_name
        self.load_message = load_message

    @cached_property
    def model(self) -> CrossEncoder:
        if self.load_message:
            print(self.load_message, flush=True)
        # 512 tokenów jak w FlagEmbedding; najdłuższy fragment (prefiks + 800 znaków) ma mniej.
        # Na GPU fp16: ~3× szybciej niż fp32, wyniki ewaluacji praktycznie te same
        # (docs/testy.md, 6.4).
        fp16 = {"torch_dtype": torch.float16} if torch.cuda.is_available() else None
        return CrossEncoder(self.model_name, max_length=512, model_kwargs=fp16)

    def score(self, queries: Sequence[str], texts: Sequence[str]) -> list[float]:
        """Dla każdego tekstu najlepszy wynik po zapytaniach (wszystkie pary w jednym predict)."""
        if not texts:
            return []
        pairs = [(q, t) for q in queries for t in texts]
        scores = self.model.predict(pairs).reshape(len(queries), len(texts)).max(axis=0)
        return [float(s) for s in scores]
