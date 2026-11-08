import { useCallback, useEffect, useRef, useState } from "react";
import { getFaculties, HttpError, streamChat } from "./api";
import { MAX_MESSAGE_CHARS } from "./config";
import { buildHistory, loadTurns, saveTurns } from "./history";
import type { ChatEvent, Turn } from "./types";
import { useAnnouncer } from "./useAnnouncer";

let seq = 0;
const newId = () => `${Date.now().toString(36)}-${(seq++).toString(36)}`;

/**
 * Stan rozmowy: tury, wysyłanie pytania ze strumieniem SSE, przerwanie (AbortController),
 * blokada po 429/503 (Retry-After) i komunikaty dla czytników ekranu.
 */
export function useChat() {
  const [turns, setTurns] = useState<Turn[]>(loadTurns);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [retryUntil, setRetryUntil] = useState(0);
  const controller = useRef<AbortController | null>(null);
  const { items: announcements, actions: announcer } = useAnnouncer();

  const turnsRef = useRef(turns);

  useEffect(() => {
    turnsRef.current = turns;
    saveTurns(turns);
  }, [turns]);

  const update = useCallback((id: string, fn: (t: Turn) => Turn) => {
    setTurns((ts) => ts.map((t) => (t.id === id ? fn(t) : t)));
  }, []);

  const send = useCallback(
    async (raw: string, replaceId?: string) => {
      const question = raw.trim();
      if (!question || question.length > MAX_MESSAGE_CHARS) return false;
      if (controller.current || Date.now() < retryUntil) return false;

      const id = newId();
      const ctrl = new AbortController();
      controller.current = ctrl;
      setActiveId(id);

      // „Spróbuj ponownie” zastępuje turę z błędem, zamiast dopisywać nową
      const base = replaceId ? turnsRef.current.filter((t) => t.id !== replaceId) : turnsRef.current;
      const history = buildHistory(base);
      const turn: Turn = { id, question, status: "searching", sources: [], answer: "", followup: history.length > 0 };
      const next: Turn[] = [...base, turn];
      turnsRef.current = next;
      setTurns(next);

      announcer.reset();
      announcer.say("Szukam odpowiedzi…");

      const onEvent = (ev: ChatEvent) => {
        switch (ev.type) {
          case "sources":
            update(id, (t) => ({ ...t, status: "writing", sources: ev.sources }));
            announcer.say(
              ev.sources.length === 1 ? "Znaleziono 1 źródło. Piszę odpowiedź…" : `Znaleziono źródła: ${ev.sources.length}. Piszę odpowiedź…`,
            );
            break;
          case "token":
            update(id, (t) => ({ ...t, answer: t.answer + ev.text }));
            announcer.stream(ev.text);
            break;
          case "refused":
            update(id, (t) => ({ ...t, status: "refused", answer: ev.message }));
            announcer.say(ev.message);
            break;
          case "clarify":
            update(id, (t) => ({
              ...t,
              status: "clarify",
              answer: ev.message,
              clarify: { message: ev.message, options: ev.options ?? [] },
            }));
            announcer.say(`${ev.message} Wybierz wydział jednym z przycisków pod pytaniem.`);
            if (!ev.options?.length)
              getFaculties()
                .then((options) => update(id, (t) => (t.clarify ? { ...t, clarify: { ...t.clarify, options } } : t)))
                .catch(() => undefined); // bez przycisków użytkownik może wpisać skrót wydziału
            break;
          case "done":
            update(id, (t) => ({
              ...t,
              answer: ev.answer,
              status: t.status === "refused" || t.status === "clarify" ? t.status : "done",
            }));
            announcer.flush();
            break;
          case "error":
            update(id, (t) => ({ ...t, status: "error", error: { code: ev.code, message: ev.message } }));
            announcer.flush();
            announcer.say(`Błąd: ${ev.message}`);
            break;
        }
      };

      try {
        const finished = await streamChat(question, history, ctrl.signal, onEvent);
        if (!finished) {
          const message = "Połączenie zostało przerwane przed końcem odpowiedzi.";
          update(id, (t) => ({ ...t, status: "error", error: { code: "stream_closed", message } }));
          announcer.say(message);
        }
      } catch (err) {
        if (ctrl.signal.aborted) {
          update(id, (t) => ({ ...t, status: "stopped" }));
          announcer.flush();
          announcer.say("Zatrzymano generowanie odpowiedzi.");
        } else {
          const error = describeError(err);
          if (error.retryAfter) setRetryUntil(Date.now() + error.retryAfter * 1000);
          update(id, (t) => ({ ...t, status: "error", error }));
          announcer.say(`Błąd: ${error.message}`);
        }
      } finally {
        controller.current = null;
        setActiveId(null);
      }
      return true;
    },
    [announcer, retryUntil, update],
  );

  const stop = useCallback(() => controller.current?.abort(), []);

  const reset = useCallback(() => {
    controller.current?.abort();
    turnsRef.current = [];
    setTurns([]);
    announcer.reset();
    announcer.say("Rozpoczęto nową rozmowę.");
  }, [announcer]);

  const rate = useCallback(
    (id: string, rating: Turn["rating"]) => update(id, (t) => ({ ...t, rating })),
    [update],
  );

  return {
    turns,
    busy: activeId !== null,
    activeId,
    retryUntil,
    send,
    stop,
    reset,
    rate,
    announcements,
  };
}

function describeError(err: unknown): NonNullable<Turn["error"]> {
  if (err instanceof HttpError) {
    if (err.status === 422)
      return {
        code: "too_long",
        message: "Pytanie albo historia rozmowy są za długie. Skróć pytanie lub zacznij nową rozmowę.",
      };
    if (err.status === 429 || err.status === 503) {
      const retryAfter = err.retryAfter ?? (err.status === 503 ? 5 : 30);
      return {
        code: err.status === 429 ? "rate_limited" : "busy",
        message:
          err.status === 429
            ? `Za dużo pytań w krótkim czasie. Spróbuj ponownie za ${retryAfter} s.`
            : `Serwer jest teraz zajęty innymi odpowiedziami. Spróbuj ponownie za ${retryAfter} s.`,
        retryAfter,
      };
    }
    return { code: `http_${err.status}`, message: `Serwer zwrócił błąd (HTTP ${err.status}).` };
  }
  return {
    code: "network",
    message: "Brak połączenia z serwerem czatu. Sprawdź połączenie z internetem i spróbuj ponownie.",
  };
}
