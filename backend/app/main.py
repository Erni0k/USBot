"""FastAPI: endpoint /chat z retrievalem (RAG) i streamingiem odpowiedzi."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from openai import OpenAI
from pydantic import BaseModel

from . import config, rag

app = FastAPI(title="US Chatbot")

# W produkcji zawęź do domeny frontendu zamiast "*".
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Klient OpenAI wskazujący na vLLM/Ollama (oba są OpenAI-compatible).
client = OpenAI(base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY)


class Msg(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[Msg]


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat")
def chat(req: ChatRequest):
    question = req.messages[-1].content

    chunks = rag.retrieve(question)
    context = rag.build_context(chunks)
    system_content = config.SYSTEM_PROMPT.format(context=context)

    messages = [{"role": "system", "content": system_content}]
    messages += [{"role": m.role, "content": m.content} for m in req.messages]

    def token_stream():
        stream = client.chat.completions.create(
            model=config.LLM_MODEL,
            messages=messages,
            temperature=0.3,
            stream=True,
            # wyłącza tryb "thinking" Qwen3 na poziomie żądania (działa na vLLM >= 0.9)
            extra_body={"chat_template_kwargs": {"enable_thinking": config.ENABLE_THINKING}},
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    return StreamingResponse(token_stream(), media_type="text/plain; charset=utf-8")
