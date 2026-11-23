import { useEffect, useMemo, useState } from "react";
import { parseAnswer, plainAnswer, sourceTitle } from "../answer";
import { US_HOME } from "../config";
import { CiteContext, Markdown, type CiteTarget } from "../markdown";
import type { FacultyOption, Rating, Turn } from "../types";
import { useCountdown } from "../useCountdown";
import { CopyButton } from "./CopyButton";
import { Feedback } from "./Feedback";
import { SourceList, sourceDomId } from "./SourceList";

interface Props {
  turn: Turn;
  /** Ostatnia tura — tylko na niej działają przyciski wydziałów i „Spróbuj ponownie”. */
  isLast: boolean;
  busy: boolean;
  /** Do kiedy (ms) serwer prosi o przerwę po 429/503. */
  retryUntil: number;
  onChooseFaculty: (f: FacultyOption) => void;
  onRetry: () => void;
  onRate: (r: Rating) => void;
}

const reducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

export function TurnView({ turn, isLast, busy, retryUntil, onChooseFaculty, onRetry, onRate }: Props) {
  const streaming = turn.status === "searching" || turn.status === "writing";
  const hasAnswer = turn.status === "writing" || turn.status === "done" || turn.status === "stopped";
  const parsed = useMemo(() => parseAnswer(turn.answer), [turn.answer]);
  const [target, setTarget] = useState<number | null>(null);

  // Podświetlenie źródła po kliknięciu [n] gaśnie po chwili
  useEffect(() => {
    if (target === null) return;
    const t = window.setTimeout(() => setTarget(null), 2000);
    return () => window.clearTimeout(t);
  }, [target]);

  const cite = useMemo<CiteTarget>(
    () => ({
      count: turn.sources.length,
      title: (n) => sourceTitle(turn.sources[n - 1]),
      onCite: (n) => {
        const el = document.getElementById(sourceDomId(turn.id, n));
        if (!el) return;
        const details = el.closest("details");
        if (details && !details.open) details.open = true;
        el.scrollIntoView({ block: "nearest", behavior: reducedMotion() ? "auto" : "smooth" });
        (el.querySelector<HTMLElement>("a") ?? el).focus({ preventScroll: true });
        setTarget(n);
      },
    }),
    [turn.id, turn.sources],
  );

  return (
    <li className="turn">
      <h3 className="msg msg-user">{turn.question}</h3>

      <article className="msg msg-bot" aria-label="Odpowiedź USBota" aria-busy={streaming}>
        {turn.status === "searching" && <p className="indicator">Szukam na stronach uczelni…</p>}
        {turn.status === "writing" && !parsed.body && (
          <p className="indicator">Piszę odpowiedź na podstawie {pages(turn.sources.length)}…</p>
        )}

        <CiteContext.Provider value={cite}>
          {hasAnswer && parsed.body && (
            <div className="answer">
              <Markdown text={parsed.body} />
            </div>
          )}
          {turn.status === "writing" && parsed.body && <p className="indicator">Piszę dalej…</p>}

          {hasAnswer && parsed.note && (
            <div className="callout callout-warn" role="note">
              <p className="callout-title">Informacja może być nieaktualna</p>
              <p>
                <Markdown text={parsed.note} inlineOnly />
              </p>
            </div>
          )}
        </CiteContext.Provider>

        {turn.status === "stopped" && (
          <p className="note">{turn.answer ? "Odpowiedź przerwana." : "Zatrzymano, zanim powstała odpowiedź."}</p>
        )}

        {turn.status === "refused" && (
          <div className="callout callout-info refused">
            <p className="callout-title">Nie znalazłem tego na stronach UŚ</p>
            <p>{turn.answer}</p>
            <p>Możesz też zapytać inaczej, np. podać nazwę wydziału albo sprawy.</p>
            <a href={US_HOME} target="_blank" rel="noopener noreferrer" className="button-link">
              Przejdź do us.edu.pl
              <span className="sr-only"> (otwiera się w nowej karcie)</span>
            </a>
          </div>
        )}

        {turn.status === "clarify" && turn.clarify && (
          <div className="answer clarify">
            <p>{clarifyLead(turn.clarify.message)}</p>
            <div className="faculty-grid" role="group" aria-label="Wybierz wydział">
              {turn.clarify.options.map((f) => (
                <button
                  key={f.code}
                  type="button"
                  className="faculty-button"
                  disabled={!isLast || busy}
                  onClick={() => onChooseFaculty(f)}
                >
                  <span className="faculty-name">{f.name}</span>
                  {f.code !== f.name && <span className="faculty-code">{f.code}</span>}
                </button>
              ))}
            </div>
          </div>
        )}

        {turn.status === "error" && turn.error && (
          <ErrorBlock error={turn.error} canRetry={isLast} busy={busy} retryUntil={retryUntil} onRetry={onRetry} />
        )}

        {hasAnswer && (
          <SourceList
            sources={turn.sources}
            id={turn.id}
            cited={parsed.cited}
            final={!streaming}
            target={target}
          />
        )}

        {turn.status === "done" && (
          <div className="answer-actions">
            <CopyButton text={() => plainAnswer(turn.answer, turn.sources)} />
            <Feedback turn={turn} onRate={onRate} />
          </div>
        )}
      </article>
    </li>
  );
}

function ErrorBlock({
  error,
  canRetry,
  busy,
  retryUntil,
  onRetry,
}: {
  error: NonNullable<Turn["error"]>;
  canRetry: boolean;
  busy: boolean;
  retryUntil: number;
  onRetry: () => void;
}) {
  const wait = useCountdown(retryUntil);
  const limited = error.code === "rate_limited" || error.code === "busy";
  return (
    <div className={`callout ${limited ? "callout-info" : "callout-error"} error`} role="alert">
      <p className="callout-title">{limited ? "Chwila przerwy" : "Nie udało się uzyskać odpowiedzi"}</p>
      <p>{error.message}</p>
      {canRetry && (
        <button type="button" className="button secondary" onClick={onRetry} disabled={busy || wait > 0}>
          {wait > 0 ? `Spróbuj ponownie za ${wait} s` : "Spróbuj ponownie"}
        </button>
      )}
    </div>
  );
}

/** „1 strony”, „3 stron” — dopełniacz po „na podstawie”. */
function pages(n: number): string {
  return n === 1 ? "1 strony" : `${n} stron`;
}

/** Komunikat `clarify` zawiera też listę wydziałów — pokazujemy je jako przyciski. */
function clarifyLead(message: string): string {
  const lines = message.split("\n").filter((l) => l.trim() && !/^\s*[-•*]\s/.test(l));
  return lines.join(" ").trim() || message;
}
