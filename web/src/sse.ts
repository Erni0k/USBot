import type { ChatEvent } from "./types";

/**
 * Parser strumienia Server-Sent Events. Przyjmuje kolejne kawałki tekstu (mogą urywać się
 * w dowolnym miejscu) i zwraca kompletne zdarzenia. Komentarze (`: ping`) są pomijane;
 * typ bierzemy z pola `type` w danych JSON (powtarza linię `event:`).
 */
export class SseParser {
  private buffer = "";

  feed(chunk: string): ChatEvent[] {
    this.buffer += chunk.replace(/\r\n?/g, "\n");
    const events: ChatEvent[] = [];
    let end: number;
    while ((end = this.buffer.indexOf("\n\n")) >= 0) {
      const block = this.buffer.slice(0, end);
      this.buffer = this.buffer.slice(end + 2);
      const event = parseBlock(block);
      if (event) events.push(event);
    }
    return events;
  }

  /** Reszta bufora na końcu strumienia (zdarzenie bez kończącej pustej linii). */
  flush(): ChatEvent[] {
    const rest = this.buffer;
    this.buffer = "";
    const event = rest.trim() ? parseBlock(rest) : null;
    return event ? [event] : [];
  }
}

function parseBlock(block: string): ChatEvent | null {
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith(":")) continue; // komentarz, np. heartbeat
    if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
  }
  if (data.length === 0) return null;
  try {
    const parsed = JSON.parse(data.join("\n"));
    return parsed && typeof parsed.type === "string" ? (parsed as ChatEvent) : null;
  } catch {
    return null;
  }
}
