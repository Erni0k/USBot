import { useState } from "react";
import { HttpError, sendFeedback } from "../api";
import { FEEDBACK_ENABLED } from "../config";
import type { Rating, Turn } from "../types";

let endpointMissing = false; // 404/405 raz — nie próbujemy dalej w tej karcie

/**
 * 👍/👎 pod odpowiedzią. Bez VITE_FEEDBACK_ENABLED=true (albo gdy serwer nie ma
 * POST /api/feedback) przyciski są widoczne, ale wyłączone — z widocznym wyjaśnieniem.
 */
export function Feedback({ turn, onRate }: { turn: Turn; onRate: (r: Rating) => void }) {
  const [state, setState] = useState<"idle" | "sending" | "error" | "unavailable">(
    FEEDBACK_ENABLED && !endpointMissing ? "idle" : "unavailable",
  );
  const disabled = state === "unavailable" || state === "sending" || turn.rating !== undefined;

  const rate = async (rating: Rating) => {
    setState("sending");
    try {
      await sendFeedback({
        question: turn.question,
        answer: turn.answer,
        rating,
        sources: turn.sources.map((s) => s.url),
      });
      onRate(rating);
      setState("idle");
    } catch (err) {
      if (err instanceof HttpError && (err.status === 404 || err.status === 405)) {
        endpointMissing = true;
        setState("unavailable");
      } else setState("error");
    }
  };

  return (
    <div className="feedback" role="group" aria-label="Oceń odpowiedź">
      <span className="feedback-label" aria-hidden="true">
        Pomocna odpowiedź?
      </span>
      <button
        type="button"
        className="icon-button"
        aria-label="Pomocna"
        aria-pressed={turn.rating === "up"}
        disabled={disabled}
        onClick={() => rate("up")}
      >
        <span aria-hidden="true">👍</span>
      </button>
      <button
        type="button"
        className="icon-button"
        aria-label="Niepomocna"
        aria-pressed={turn.rating === "down"}
        disabled={disabled}
        onClick={() => rate("down")}
      >
        <span aria-hidden="true">👎</span>
      </button>
      <span className="feedback-note" role="status">
        {turn.rating
          ? "Dziękujemy za ocenę."
          : state === "unavailable"
            ? "Oceny będą dostępne wkrótce."
            : state === "error"
              ? "Nie udało się wysłać oceny — spróbuj ponownie."
              : ""}
      </span>
    </div>
  );
}
