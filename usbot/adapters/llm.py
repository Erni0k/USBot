"""ChatLLM przez API zgodne z OpenAI (`/v1/chat/completions`): Ollama, vLLM."""
from collections.abc import Iterator

import httpx
from openai import OpenAI

from usbot.core.models import Message


class OpenAICompatibleLLM:
    """Port ChatLLM. `timeout` (sekundy) dotyczy połączenia i każdego kolejnego fragmentu
    strumienia — zawieszony serwer kończy się błędem zamiast wiecznego czekania.

    `reasoning_effort` (np. "none") jest wysyłany tylko, gdy podany: Ollama ignoruje
    `chat_template_kwargs.enable_thinking` i Qwen3 „myśli” mimo ENABLE_THINKING=false
    (tekst rozumowania przychodzi w osobnym polu i nie trafia do odpowiedzi, ale opóźnia
    pierwszy token). Pomiar: docs/api.md (3.2).
    """

    def __init__(self, base_url: str, api_key: str, model: str, *,
                 enable_thinking: bool = False, temperature: float = 0.3,
                 timeout: float | None = None, max_retries: int = 2,
                 reasoning_effort: str | None = None) -> None:
        self.client = OpenAI(
            base_url=base_url, api_key=api_key, max_retries=max_retries,
            **({"timeout": httpx.Timeout(timeout, connect=min(timeout, 5.0))}
               if timeout else {}),
        )
        self.model = model
        self.enable_thinking = enable_thinking
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort

    def stream(self, messages: list[Message]) -> Iterator[str]:
        """Kolejne kawałki odpowiedzi. Zamknięcie generatora zamyka połączenie HTTP —
        Ollama i vLLM przerywają wtedy generowanie."""
        extra = {"reasoning_effort": self.reasoning_effort} if self.reasoning_effort else {}
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            stream=True,
            **extra,
            # Tryb rozumowania Qwen3 (vLLM; Ollama ignoruje — patrz reasoning_effort)
            extra_body={"chat_template_kwargs": {"enable_thinking": self.enable_thinking}},
        )
        try:
            for chunk in stream:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    yield delta
        finally:
            stream.close()

    def ping(self, timeout: float) -> dict:
        """Sprawdzenie dla /api/health: serwer odpowiada i ma model `self.model`."""
        models = [m.id for m in self.client.with_options(timeout=timeout, max_retries=0)
                  .models.list()]
        if self.model not in models:
            raise RuntimeError(f"serwer nie ma modelu {self.model!r} (są: {', '.join(models)})")
        return {"model": self.model}
