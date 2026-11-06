import { HISTORY_TURNS, MAX_HISTORY_CHARS, MAX_HISTORY_MESSAGES } from "./config";
import type { HistoryMessage, Turn } from "./types";

/**
 * Historia wysyłana do API z zakończonych tur (docs/api.md, 3.1): pytanie i `done.answer`
 * (przy `refused`/`clarify` — ich komunikat); po `error` samo pytanie; przerwana odpowiedź —
 * pytanie i to, co zdążyło się wygenerować (użytkownik to widzi, więc model też powinien).
 * Wysyłamy tylko ostatnie tury, w limicie liczby wiadomości i znaków.
 */
export function buildHistory(turns: Turn[]): HistoryMessage[] {
  const pairs: HistoryMessage[][] = [];
  for (const t of turns) {
    const reply = replyFor(t);
    if (reply === null) continue;
    const pair: HistoryMessage[] = [{ role: "user", content: t.question }];
    if (reply) pair.push({ role: "assistant", content: reply });
    pairs.push(pair);
  }

  const recent = pairs.slice(-HISTORY_TURNS);
  while (recent.length > 0) {
    const flat = recent.flat();
    const chars = flat.reduce((n, m) => n + m.content.length, 0);
    if (flat.length <= MAX_HISTORY_MESSAGES && chars <= MAX_HISTORY_CHARS) return flat;
    recent.shift();
  }
  return [];
}

/** Treść odpowiedzi do historii; "" = samo pytanie, null = tura w toku (pomijana). */
function replyFor(t: Turn): string | null {
  switch (t.status) {
    case "done":
    case "refused":
      return t.answer;
    case "clarify":
      return t.clarify?.message ?? t.answer;
    case "stopped":
      return t.answer.trim();
    case "error":
      return "";
    default:
      return null;
  }
}

const STORAGE_KEY = "usbot.conversation";

/** Rozmowa w sessionStorage — przetrwa odświeżenie, znika po zamknięciu karty. */
export function loadTurns(): Turn[] {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    const turns: unknown = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(turns)) return [];
    // Odpowiedź przerwana odświeżeniem strony
    return (turns as Turn[]).map((t) =>
      t.status === "searching" || t.status === "writing" ? { ...t, status: "stopped" } : t,
    );
  } catch {
    return [];
  }
}

export function saveTurns(turns: Turn[]): void {
  try {
    if (turns.length === 0) sessionStorage.removeItem(STORAGE_KEY);
    else sessionStorage.setItem(STORAGE_KEY, JSON.stringify(turns));
  } catch {
    /* tryb prywatny / brak miejsca — rozmowa zostaje tylko w pamięci */
  }
}
