import { forwardRef, useImperativeHandle, useLayoutEffect, useRef, useState } from "react";
import { MAX_MESSAGE_CHARS } from "../config";
import { useCountdown } from "../useCountdown";

interface Props {
  busy: boolean;
  retryUntil: number;
  onSend: (text: string) => Promise<boolean>;
  onStop: () => void;
}

/** Licznik znaków pokazujemy dopiero blisko limitu — wcześniej to tylko szum. */
const COUNT_FROM = MAX_MESSAGE_CHARS - 200;

/** Pole pytania: Enter wysyła, Shift+Enter — nowa linia, Esc zatrzymuje generowanie.
 *  Rośnie razem z tekstem (do 12rem), potem się przewija. */
export const Composer = forwardRef<HTMLTextAreaElement, Props>(function Composer(
  { busy, retryUntil, onSend, onStop },
  ref,
) {
  const [text, setText] = useState("");
  const field = useRef<HTMLTextAreaElement>(null);
  useImperativeHandle(ref, () => field.current!, []);
  const wait = useCountdown(retryUntil);
  const tooLong = text.length > MAX_MESSAGE_CHARS;
  const canSend = !busy && wait === 0 && text.trim().length > 0 && !tooLong;

  useLayoutEffect(() => {
    const el = field.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight + 2}px`; // + obramowanie (box-sizing: border-box)
  }, [text]);

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
          ref={field}
          rows={1}
          value={text}
          placeholder="Zadaj pytanie o studia na UŚ"
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
            <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
              <rect x="4" y="4" width="8" height="8" rx="1.5" fill="currentColor" />
            </svg>
            Zatrzymaj
          </button>
        ) : (
          <button type="submit" className="button primary send" disabled={!canSend} aria-label="Wyślij" title="Wyślij (Enter)">
            <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
              <path d="M8 13V3M3.5 7.5 8 3l4.5 4.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
        )}
      </div>
      <div className="composer-footer">
        <span id="composer-hint" className="hint">
          {wait > 0 ? (
            <span role="status">Kolejne pytanie możesz wysłać za {wait} s.</span>
          ) : (
            <span className="hint-keys">
              <kbd>Enter</kbd> wysyła, <kbd>Shift</kbd>+<kbd>Enter</kbd> to nowa linia
              {busy && (
                <>
                  , <kbd>Esc</kbd> zatrzymuje odpowiedź
                </>
              )}
            </span>
          )}
        </span>
        <span id="composer-count" className={tooLong ? "count over" : "count"}>
          {tooLong
            ? `Pytanie jest za długie: ${text.length} z ${MAX_MESSAGE_CHARS} znaków`
            : text.length >= COUNT_FROM
              ? `${text.length}/${MAX_MESSAGE_CHARS}`
              : ""}
        </span>
      </div>
    </form>
  );
});
