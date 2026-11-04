"""OpenAICompatibleLLM na atrapie klienta: parametry wywołania i zamykanie strumienia."""
from types import SimpleNamespace

import pytest

from usbot.adapters.llm import OpenAICompatibleLLM


class FakeStream:
    def __init__(self, parts):
        self.parts = parts
        self.closed = False

    def __iter__(self):
        for p in self.parts:
            delta = SimpleNamespace(content=p)
            yield SimpleNamespace(choices=[SimpleNamespace(delta=delta)])

    def close(self):
        self.closed = True


def llm_with(stream, **kwargs):
    llm = OpenAICompatibleLLM("http://127.0.0.1:1/v1", "x", "qwen3:8b", **kwargs)
    calls = []

    def create(**kw):
        calls.append(kw)
        return stream

    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return llm, calls


def test_stream_pomija_puste_kawalki_i_zamyka_strumien_na_koncu():
    stream = FakeStream(["A", None, "", "la"])
    llm, calls = llm_with(stream)
    assert list(llm.stream([{"role": "user", "content": "?"}])) == ["A", "la"]
    assert stream.closed
    assert "reasoning_effort" not in calls[0]
    assert calls[0]["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_zamkniecie_generatora_zamyka_strumien_http():
    stream = FakeStream(["a", "b", "c"])
    llm, _ = llm_with(stream)
    gen = llm.stream([{"role": "user", "content": "?"}])
    assert next(gen) == "a"
    gen.close()  # klient API się rozłączył
    assert stream.closed


def test_reasoning_effort_wysylany_tylko_gdy_ustawiony():
    llm, calls = llm_with(FakeStream([]), reasoning_effort="none")
    list(llm.stream([]))
    assert calls[0]["reasoning_effort"] == "none"


@pytest.mark.parametrize("timeout, expected", [(None, 600.0), (30.0, 30.0)])
def test_timeout_klienta(timeout, expected):
    llm = OpenAICompatibleLLM("http://127.0.0.1:1/v1", "x", "m", timeout=timeout)
    t = llm.client.timeout
    assert (t if isinstance(t, float) else t.read) == expected
