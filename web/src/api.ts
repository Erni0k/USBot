import { API_BASE } from "./config";
import { SseParser } from "./sse";
import type { ChatEvent, FacultyOption, HistoryMessage, Source } from "./types";

/** Odpowiedź API bez strumienia (422, 429, 503…). */
export class HttpError extends Error {
  readonly status: number;
  readonly retryAfter?: number;

  constructor(status: number, message: string, retryAfter?: number) {
    super(message);
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

/**
 * POST /api/chat i czytanie strumienia SSE. `onEvent` dostaje zdarzenia po kolei;
 * przerwanie `signal` rzuca AbortError (serwer przestaje wtedy generować).
 * Zwraca, czy strumień skończył się zdarzeniem `done` albo `error`.
 */
export async function streamChat(
  message: string,
  history: HistoryMessage[],
  signal: AbortSignal,
  onEvent: (event: ChatEvent) => void,
): Promise<boolean> {
  const res = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ message, history }),
    signal,
  });
  if (!res.ok || !res.body) throw await httpError(res);

  const parser = new SseParser();
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let finished = false;
  const emit = (events: ChatEvent[]) => {
    for (const ev of events) {
      if (ev.type === "done" || ev.type === "error") finished = true;
      onEvent(ev);
    }
  };
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      emit(parser.feed(value));
    }
    emit(parser.flush());
  } finally {
    reader.releaseLock();
  }
  return finished;
}

async function httpError(res: Response): Promise<HttpError> {
  const retry = Number(res.headers.get("Retry-After"));
  const retryAfter = Number.isFinite(retry) && retry > 0 ? retry : undefined;
  let detail = "";
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") detail = body.detail;
    else if (Array.isArray(body?.detail))
      detail = body.detail.map((d: { msg?: string }) => d.msg).join("; ");
  } catch {
    /* odpowiedź nie-JSON (np. z proxy) */
  }
  return new HttpError(res.status, detail || `HTTP ${res.status}`, retryAfter);
}

export interface Health {
  status: "ok" | "error";
  checks: Record<string, { ok: boolean; ms: number; error: string | null }>;
}

/** GET /api/health; `null`, gdy API w ogóle nie odpowiada. */
export async function getHealth(signal?: AbortSignal): Promise<Health | null> {
  try {
    const res = await fetch(`${API_BASE}/api/health`, { signal });
    const body = (await res.json()) as Health;
    return body && typeof body.status === "string" ? body : null;
  } catch {
    return null;
  }
}

/** GET /api/faculties — zapas, gdy `clarify` przyszło bez `options`. */
export async function getFaculties(): Promise<FacultyOption[]> {
  const res = await fetch(`${API_BASE}/api/faculties`);
  if (!res.ok) throw await httpError(res);
  return (await res.json()) as FacultyOption[];
}

export interface FeedbackPayload {
  question: string;
  answer: string;
  rating: 1 | -1;
  sources: Pick<Source, "url" | "dist" | "rerank">[];
  comment: string | null;
  followup: boolean;
}

/**
 * POST /api/feedback (docs/api.md, 3.1). Rzuca HttpError; 404/405 oznacza serwer
 * bez tego endpointu (np. starsza wersja API).
 */
export async function sendFeedback(payload: FeedbackPayload): Promise<void> {
  const res = await fetch(`${API_BASE}/api/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw await httpError(res);
}
