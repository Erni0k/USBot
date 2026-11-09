"""Źródła odpowiedzi: model cytuje numery fragmentów, adresy dokleja kod.

Gdy model przepisywał adresy z kontekstu, 8% podanych URL-i nie istniało, a trafną stronę
wskazywał w około połowie przypadków (docs/testy.md, 6.7). Numer można sprawdzić: numer
spoza kontekstu jest usuwany ze strumienia, a adres zawsze pochodzi z wyników wyszukiwania.
"""
import re
from collections.abc import Sequence

from usbot.core.models import Chunk, Message

FOOTER_HEADING = "Źródła:"

_CITATION = re.compile(r"\[\s*(\d{1,2}(?:\s*,\s*\d{1,2})*)\s*\]")
# Otwarty nawias na końcu kawałka tekstu: może to być początek numeru, poczekaj na resztę
_OPEN_CITATION = re.compile(r"\[[\d\s,]*$")
_FOOTER = re.compile(rf"\n*{FOOTER_HEADING}\n(?:\[\d+\] [^\n]+\n?)+\s*$")
_ANY_CITATION = re.compile(r" ?\[\d{1,2}(?:, ?\d{1,2})*\]")


FAQ_SCHEME = "faq://"
FAQ_LABEL = "FAQ (wpis sprawdzony ręcznie)"


def source_label(url: str) -> str:
    """Tekst źródła w stopce: adres strony albo oznaczenie wpisu FAQ (`faq://f01#<strona>`)."""
    if not url.startswith(FAQ_SCHEME):
        return url
    page = url.partition("#")[2]
    return f"{FAQ_LABEL}: {page}" if page else FAQ_LABEL


def source_numbers(chunks: Sequence[Chunk]) -> dict[str, int]:
    """Numer każdej strony (od 1, w kolejności wyników); fragmenty jednej strony dzielą numer."""
    numbers: dict[str, int] = {}
    for c in chunks:
        numbers.setdefault(c.url, len(numbers) + 1)
    return numbers


class CitationFilter:
    """Filtr strumienia odpowiedzi: przepuszcza numery istniejące i zapamiętuje użyte.

    Tokeny mogą przeciąć numer w dowolnym miejscu, więc otwarty nawias czeka na resztę.
    """

    def __init__(self, n_sources: int) -> None:
        self.n_sources = n_sources
        self.cited: list[int] = []
        self._pending = ""

    def _replace(self, match: re.Match) -> str:
        valid = [n for n in map(int, match[1].split(",")) if 1 <= n <= self.n_sources]
        self.cited.extend(n for n in valid if n not in self.cited)
        return f"[{', '.join(map(str, dict.fromkeys(valid)))}]" if valid else ""

    def feed(self, text: str) -> str:
        text = self._pending + text
        self._pending = ""
        tail = _OPEN_CITATION.search(text)
        if tail:
            self._pending = text[tail.start():]
            text = text[:tail.start()]
        return _CITATION.sub(self._replace, text)

    def flush(self) -> str:
        text, self._pending = self._pending, ""
        return _CITATION.sub(self._replace, text)


def sources_footer(cited: Sequence[int], numbers: dict[str, int]) -> str:
    """Lista cytowanych stron dopisywana po odpowiedzi; pusta, gdy model nic nie zacytował."""
    urls = {n: url for url, n in numbers.items()}
    lines = [f"[{n}] {source_label(urls[n])}" for n in sorted(cited)]
    return f"\n\n{FOOTER_HEADING}\n" + "\n".join(lines) if lines else ""


def without_citations(history: Sequence[Message]) -> list[Message]:
    """Historia dla LLM-a bez numerów i stopki: numery z poprzedniej tury znaczą co innego."""
    def clean(text: str) -> str:
        return _ANY_CITATION.sub("", _FOOTER.sub("", text)).strip()

    return [{**m, "content": clean(m["content"])} if m["role"] == "assistant" else m for m in history]
