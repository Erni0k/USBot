import { useEffect, useRef, useState } from "react";
import { HttpError, sendFeedback } from "../api";
import { FEEDBACK_ENABLED, MAX_FEEDBACK_COMMENT_CHARS } from "../config";
import type { Rating, Turn } from "../types";

let endpointMissing = false; // 404/405 raz — nie próbujemy dalej w tej karcie

/**
 * 👍/👎 pod odpowiedzią. 👍 wysyła ocenę od razu, 👎 najpierw pyta o opcjonalny komentarz.
 * Przy VITE_FEEDBACK_ENABLED=false (albo gdy serwer nie ma POST /api/feedback) przyciski
 * są widoczne, ale wyłączone — z widocznym wyjaśnieniem.
 */
export function Feedback({ turn, onRate }: { turn: Turn; onRate: (r: Rating) => void }) {
  const [state, setState] = useState<"idle" | "sending" | "error" | "unavailable">(
    FEEDBACK_ENABLED && !endpointMissing ? "idle" : "unavailable",
  );
  const [commenting, setCommenting] = useState(false);
  const [comment, setComment] = useState("");
  const textarea = useRef<HTMLTextAreaElement>(null);
  const downButton = useRef<HTMLButtonElement>(null);
  const disabled = state === "unavailable" || state === "sending" || turn.rating !== undefined;

  useEffect(() => {
    if (!commenting) return;
    textarea.current?.focus();
    // focus() pokazuje samo pole, a pod nim są jeszcze przyciski
    textarea.current?.closest("form")?.scrollIntoView({ block: "nearest" });
  }, [commenting]);

  const rate = async (rating: Rating, text = "") => {
    setState("sending");
    try {
      await sendFeedback({
        question: turn.question,
        answer: turn.answer,
        rating: rating === "up" ? 1 : -1,
        sources: turn.sources.map(({ url, dist, rerank }) => ({ url, dist, rerank })),
        comment: text.trim() || null,
        followup: turn.followup ?? false,
      });
      onRate(rating);
      setCommenting(false);
      setState("idle");
    } catch (err) {
      if (err instanceof HttpError && (err.status === 404 || err.status === 405)) {
        endpointMissing = true;
        setCommenting(false);
        setState("unavailable");
      } else setState("error");
    }
  };

  const cancel = () => {
    setCommenting(false);
    setState("idle");
    downButton.current?.focus();
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
        ref={downButton}
        type="button"
        className="icon-button"
        aria-label="Niepomocna"
        aria-pressed={turn.rating === "down"}
        aria-expanded={commenting}
        disabled={disabled}
        onClick={() => setCommenting(true)}
      >
        <span aria-hidden="true">👎</span>
      </button>
      <span className="feedback-note" role="status">
        {turn.rating
          ? "Dziękujemy za ocenę."
          : state === "unavailable"
            ? "Oceny są teraz niedostępne."
            : state === "error"
              ? "Nie udało się wysłać oceny — spróbuj ponownie."
              : ""}
      </span>
      {commenting && (
        <form
          className="feedback-form"
          onSubmit={(e) => {
            e.preventDefault();
            void rate("down", comment);
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") cancel();
          }}
        >
          <label htmlFor={`feedback-${turn.id}`}>Co było nie tak? (opcjonalnie)</label>
          <textarea
            id={`feedback-${turn.id}`}
            ref={textarea}
            rows={2}
            maxLength={MAX_FEEDBACK_COMMENT_CHARS}
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            aria-describedby={`feedback-hint-${turn.id}`}
          />
          <p id={`feedback-hint-${turn.id}`} className="feedback-hint">
            Nie wpisuj danych osobowych. Pytanie, odpowiedź i komentarz zapiszemy bez adresu IP na 30 dni.
          </p>
          <div className="feedback-actions">
            <button type="submit" className="button primary" disabled={state === "sending"}>
              Wyślij ocenę
            </button>
            <button type="button" className="button secondary" disabled={state === "sending"} onClick={cancel}>
              Anuluj
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
