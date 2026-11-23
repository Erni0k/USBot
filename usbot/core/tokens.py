"""Szacowanie liczby tokenów qwen3:8b bez wywoływania modelu.

Ollama przez `/v1/chat/completions` ignoruje `num_ctx`, więc prompt i odpowiedź mieszczą się
razem w 4096 tokenach; za długi prompt zostawia mało miejsca na odpowiedź i ją ucina.
Współczynniki dopasowane do `prompt_eval_count` z Ollamy (docs/testy.md, 6.10 i 6.13).
"""
from collections.abc import Sequence

from usbot.core.models import Message

# prompt_tokens() = PROMPT_SCALE · Σ estimated_tokens(treść) + PER_MESSAGE · wiadomości + PROMPT_BASE;
# dopasowanie na 264 promptach (w tym rozmowach dwuturowych): średni błąd 63 tokeny
PROMPT_SCALE = 1.024
PER_MESSAGE = 16
PROMPT_BASE = 72
# Największe zaniżenie prompt_tokens() w tym pomiarze (183) z zapasem
PROMPT_MARGIN = 190


def estimated_tokens(text: str) -> float:
    """Przybliżona liczba tokenów `text`.

    Liczba znaków nie wystarcza: cyfry kosztują kilka razy więcej niż litery, więc kontekst
    tej samej długości różni się o setki tokenów.
    """
    digits = sum(ch.isdigit() for ch in text)
    letters = sum(ch.isalpha() for ch in text)
    spaces = sum(ch.isspace() for ch in text)
    return 0.27 * letters + 1.43 * digits + 0.53 * spaces + 0.66 * (len(text) - digits - letters - spaces)


def prompt_tokens(messages: Sequence[Message]) -> float:
    """Szacowana liczba tokenów całego promptu (szablon czatu Ollamy wliczony)."""
    return (PROMPT_SCALE * sum(estimated_tokens(m["content"]) for m in messages)
            + PER_MESSAGE * len(messages) + PROMPT_BASE)


def fits(messages: Sequence[Message], max_tokens: int) -> bool:
    """Prompt zmieści się w `max_tokens` także przy największym zaobserwowanym błędzie szacunku."""
    return prompt_tokens(messages) + PROMPT_MARGIN <= max_tokens
