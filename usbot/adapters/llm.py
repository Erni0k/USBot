"""ChatLLM przez API zgodne z OpenAI (`/v1/chat/completions`): Ollama, vLLM."""
from collections.abc import Iterator

from openai import OpenAI

from usbot.core.models import Message


class OpenAICompatibleLLM:
    def __init__(self, base_url: str, api_key: str, model: str, *,
                 enable_thinking: bool = False, temperature: float = 0.3) -> None:
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model
        self.enable_thinking = enable_thinking
        self.temperature = temperature

    def stream(self, messages: list[Message]) -> Iterator[str]:
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            stream=True,
            # Tryb rozumowania Qwen3 (vLLM/Ollama)
            extra_body={"chat_template_kwargs": {"enable_thinking": self.enable_thinking}},
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
