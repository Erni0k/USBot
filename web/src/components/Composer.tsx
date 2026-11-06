import { forwardRef, useEffect, useState } from "react";
import { MAX_MESSAGE_CHARS } from "../config";

interface Props {
  busy: boolean;
  retryUntil: number;
  onSend: (text: string) => Promise<boolean>;
  onStop: () => void;
}

/** Pole pytania: Enter wysyła, Shift+Enter — nowa linia, Esc zatrzymuje generowanie. */
export const Composer = forwardRef<HTMLTextAreaElement, Props>(function Composer(
  { busy, retryUntil, onSend, onStop },
  ref,
) {
  const [text, setText] = useState("");
  const wait = useCountdown(retryUntil);
  const tooLong = text.length > MAX_MESSAGE_CHARS;
  const canSend = !busy && wait === 0 && text.trim().length > 0 && !tooLong;

  const submit = async () => {
    if (!canSend) return;
    const value = text;
    setText("");
    const sent = await onSend(value);
    if (!sent) setText(value);
  };

  return (
    <form
      className="composer"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <label htmlFor="question" className="sr-only">
        Twoje pytanie
      </label>
      <div className="composer-box">
        <textarea
          id="question"
          ref={ref}
          rows={2}
          value={text}
          placeholder="Zadaj pytanie, np. „Jak połączyć się z siecią eduroam?”"
          aria-describedby="composer-hint composer-count"
          aria-invalid={tooLong || undefined}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void submit();
            } else if (e.key === "Escape" && busy) {
              e.preventDefault();
              onStop();
            }
          }}
        />
        {busy ? (
          <button type="button" className="button stop" onClick={onStop}>
            <span aria-hidden="true">■ </span>Zatrzymaj
          </button>
        ) : (
          <button type="submit" className="button primary" disabled={!canSend}>
            Wyślij
          </button>
        )}
      </div>
      <div className="composer-footer">
        <span id="composer-hint" className="hint">
          {wait > 0 ? (
            <span role="status">Kolejne pytanie możesz wysłać za {wait} s.</span>
          ) : (
            <>
              <kbd>Enter</kbd> — wyślij, <kbd>Shift</kbd>+<kbd>Enter</kbd> — nowa linia
              {busy && (
                <>
                  , <kbd>Esc</kbd> — zatrzymaj
                </>
              )}
            </>
          )}
        </span>
        <span id="composer-count" className={tooLong ? "count over" : "count"}>
          {tooLong ? `Pytanie jest za długie: ${text.length} z ${MAX_MESSAGE_CHARS} znaków` : `${text.length}/${MAX_MESSAGE_CHARS}`}
        </span>
      </div>
    </form>
  );
});

function useCountdown(until: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (until <= Date.now()) return;
    setNow(Date.now());
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [until]);
  return Math.max(0, Math.ceil((until - now) / 1000));
}
