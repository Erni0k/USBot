# Frontend (Next.js)

## Inicjalizacja

```bash
npx create-next-app@latest frontend --ts --app --tailwind --eslint
cd frontend
npm install ai @ai-sdk/react
```

Ustaw adres backendu w `frontend/.env.local`:

```
NEXT_PUBLIC_BACKEND_URL=http://localhost:8080
```

## Minimalny czat

Backend `/chat` zwraca strumień zwykłego tekstu (`text/plain`). Najprostszy
działający komponent — `app/page.tsx`:

```tsx
"use client";
import { useState } from "react";

type Msg = { role: "user" | "assistant"; content: string };

export default function Chat() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");

  async function send() {
    if (!input.trim()) return;
    const next: Msg[] = [...messages, { role: "user", content: input }];
    setMessages([...next, { role: "assistant", content: "" }]);
    setInput("");

    const res = await fetch(`${process.env.NEXT_PUBLIC_BACKEND_URL}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: next }),
    });

    const reader = res.body!.getReader();
    const decoder = new TextDecoder();
    let acc = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      acc += decoder.decode(value, { stream: true });
      setMessages((m) => {
        const copy = [...m];
        copy[copy.length - 1] = { role: "assistant", content: acc };
        return copy;
      });
    }
  }

  return (
    <main style={{ maxWidth: 640, margin: "2rem auto" }}>
      <h1>Asystent UŚ</h1>
      <div>
        {messages.map((m, i) => (
          <p key={i}><b>{m.role === "user" ? "Ty" : "Bot"}:</b> {m.content}</p>
        ))}
      </div>
      <input
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && send()}
        placeholder="Zadaj pytanie o UŚ..."
        style={{ width: "100%", padding: 8 }}
      />
      <button onClick={send}>Wyślij</button>
    </main>
  );
}
```

## A Vercel AI SDK?

Powyższy kod jest celowo prosty (czysty `fetch`), żeby działał od razu. Gdy
zechcecie pełnego `useChat` z AI SDK, najłatwiej dodać w Next.js trasę
`app/api/chat/route.ts`, która użyje `streamText` z dostawcą OpenAI-compatible
wskazującym na wasz backend albo bezpośrednio na vLLM — wtedy `useChat`
obsłuży protokół streamingu out-of-the-box.
