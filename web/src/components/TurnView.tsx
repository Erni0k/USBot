import { US_HOME } from "../config";
import { Markdown } from "../markdown";
import type { FacultyOption, Rating, Turn } from "../types";
import { Feedback } from "./Feedback";
import { SourceList } from "./SourceList";

interface Props {
  turn: Turn;
  /** Ostatnia tura — tylko na niej działają przyciski wydziałów i „Spróbuj ponownie”. */
  isLast: boolean;
  busy: boolean;
  retryBlocked: boolean;
  onChooseFaculty: (f: FacultyOption) => void;
  onRetry: () => void;
  onRate: (r: Rating) => void;
}

export function TurnView({ turn, isLast, busy, retryBlocked, onChooseFaculty, onRetry, onRate }: Props) {
  const streaming = turn.status === "searching" || turn.status === "writing";
  return (
    <li className="turn">
      <article className="msg msg-user" aria-label="Twoje pytanie">
        <p className="msg-text">{turn.question}</p>
      </article>

      <article className="msg msg-bot" aria-label="Odpowiedź USBota" aria-busy={streaming}>
        {turn.status === "searching" && <Indicator text="Szukam w serwisach uczelni…" />}
        {turn.status === "writing" && !turn.answer && <Indicator text="Piszę odpowiedź…" />}

        {(turn.status === "writing" || turn.status === "done" || turn.status === "stopped") && turn.answer && (
          <div className="answer">
            <Markdown text={turn.answer} />
            {turn.status === "writing" && <span className="cursor" aria-hidden="true" />}
          </div>
        )}

        {turn.status === "stopped" && (
          <p className="note">
            {turn.answer ? "Odpowiedź przerwana." : "Zatrzymano, zanim powstała odpowiedź."}
          </p>
        )}

        {turn.status === "refused" && (
          <div className="answer refused">
            <p>{turn.answer}</p>
            <p>
              <a href={US_HOME} target="_blank" rel="noopener noreferrer" className="button-link">
                Przejdź do us.edu.pl
                <span className="sr-only"> (otwiera się w nowej karcie)</span>
              </a>
            </p>
          </div>
        )}

        {turn.status === "clarify" && turn.clarify && (
          <div className="answer clarify">
            <p>{clarifyLead(turn.clarify.message)}</p>
            <div className="faculty-grid" role="group" aria-label="Wybierz odpowiedź">
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
          <div className="answer error" role="alert">
            <p>
              <span className="error-label">Nie udało się uzyskać odpowiedzi.</span> {turn.error.message}
            </p>
            {isLast && (
              <button type="button" className="button secondary" onClick={onRetry} disabled={busy || retryBlocked}>
                Spróbuj ponownie
              </button>
            )}
          </div>
        )}

        {(turn.status === "writing" || turn.status === "done" || turn.status === "stopped") && (
          <SourceList sources={turn.sources} id={turn.id} />
        )}

        {turn.status === "done" && <Feedback turn={turn} onRate={onRate} />}
      </article>
    </li>
  );
}

function Indicator({ text }: { text: string }) {
  return (
    <p className="indicator">
      <span className="dots" aria-hidden="true">
        <span />
        <span />
        <span />
      </span>
      {text}
    </p>
  );
}

/** Komunikat `clarify` zawiera też listę wydziałów — pokazujemy je jako przyciski. */
function clarifyLead(message: string): string {
  const lines = message.split("\n").filter((l) => l.trim() && !/^\s*[-•*]\s/.test(l));
  return lines.join(" ").trim() || message;
}
