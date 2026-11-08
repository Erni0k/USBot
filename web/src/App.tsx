import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { getHealth, type Health } from "./api";
import { Composer } from "./components/Composer";
import { Settings } from "./components/Settings";
import { TurnView } from "./components/TurnView";
import { useChat } from "./useChat";

const EXAMPLES = [
  "Jak połączyć się z siecią eduroam?",
  "Jak ubiegać się o stypendium socjalne?",
  "Jak zalogować się do USOSweb?",
  "Jakie są godziny otwarcia dziekanatu?",
];

const CHECK_NAMES: Record<string, string> = {
  database: "baza wiedzy",
  embeddings: "wyszukiwarka",
  reranker: "ranking wyników",
  llm: "model językowy",
};

export default function App() {
  const chat = useChat();
  const input = useRef<HTMLTextAreaElement>(null);
  const log = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const health = useHealth();

  const focusInput = () => input.current?.focus();

  // Przewijanie za odpowiedzią — chyba że użytkownik przewinął wyżej, żeby coś przeczytać
  useLayoutEffect(() => {
    const el = log.current;
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight;
  }, [chat.turns]);

  // Esc zatrzymuje generowanie także poza polem pytania
  useEffect(() => {
    if (!chat.busy) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented) chat.stop();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [chat.busy, chat.stop]);

  const ask = useCallback(
    (text: string) => {
      stickToBottom.current = true;
      return chat.send(text);
    },
    [chat.send],
  );

  const newConversation = () => {
    chat.reset();
    focusInput();
  };

  const retryBlocked = chat.retryUntil > Date.now();

  return (
    <div className="app">
      <a href="#question" className="skip-link">
        Przejdź do pola pytania
      </a>
      <header className="header">
        <div className="brand">
          <span className="logo" aria-hidden="true">
            US
          </span>
          <div>
            <h1>USBot</h1>
            <p className="tagline">Asystent Uniwersytetu Śląskiego w Katowicach</p>
          </div>
        </div>
        <Settings />
        <button
          type="button"
          className="button secondary new-chat"
          onClick={newConversation}
          disabled={chat.turns.length === 0 && !chat.busy}
        >
          Nowa rozmowa
        </button>
      </header>

      {health && health.status !== "ok" && (
        <div className="banner" role="alert">
          <p>
            <strong>Czat może teraz nie działać.</strong>{" "}
            {health.problems.length > 0
              ? `Niedostępne: ${health.problems.join(", ")}.`
              : "Nie udało się połączyć z serwerem czatu."}{" "}
            Spróbuj ponownie za chwilę.
          </p>
          <button type="button" className="button secondary" onClick={health.recheck}>
            Sprawdź ponownie
          </button>
        </div>
      )}

      <main className="main">
        <div
          className="log"
          ref={log}
          tabIndex={0}
          aria-label="Rozmowa"
          onScroll={(e) => {
            const el = e.currentTarget;
            stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
          }}
        >
          {chat.turns.length === 0 ? (
            <section className="welcome" aria-labelledby="welcome-title">
              <h2 id="welcome-title">W czym mogę pomóc?</h2>
              <p>
                Odpowiadam na pytania o studia na Uniwersytecie Śląskim na podstawie stron uczelni
                (us.edu.pl, usnet, eduroam) i podaję źródła.
              </p>
              <h3 className="examples-title">Przykładowe pytania</h3>
              <ul className="examples">
                {EXAMPLES.map((q) => (
                  <li key={q}>
                    <button type="button" className="example" onClick={() => ask(q)} disabled={chat.busy}>
                      {q}
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          ) : (
            <>
              <h2 className="sr-only">Rozmowa</h2>
              <ol className="turns">
                {chat.turns.map((t, i) => (
                  <TurnView
                    key={t.id}
                    turn={t}
                    isLast={i === chat.turns.length - 1}
                    busy={chat.busy}
                    retryBlocked={retryBlocked}
                    onChooseFaculty={(f) => {
                      void ask(f.code);
                      focusInput();
                    }}
                    onRetry={() => {
                      void chat.send(t.question, t.id);
                      focusInput();
                    }}
                    onRate={(r) => chat.rate(t.id, r)}
                  />
                ))}
              </ol>
            </>
          )}
        </div>

        {/* Odpowiedź dla czytników ekranu — zdaniami, gdy się generuje */}
        <div className="sr-only" aria-live="polite" aria-relevant="additions" aria-atomic="false">
          {chat.announcements.map((a) => (
            <p key={a.id}>{a.text}</p>
          ))}
        </div>

        <Composer ref={input} busy={chat.busy} retryUntil={chat.retryUntil} onSend={ask} onStop={chat.stop} />
      </main>

      <footer className="disclaimer">
        <p>
          <strong>Bot może się mylić – sprawdź źródło. Nie wpisuj danych osobowych.</strong>{" "}
          <span className="privacy">
            Pytania, a przy ocenie także odpowiedź i komentarz, są zapisywane bez adresu IP przez 30 dni w celu poprawy
            jakości odpowiedzi.
          </span>
        </p>
      </footer>
    </div>
  );
}

/** GET /api/health przy otwarciu strony; baner, gdy coś nie działa. */
function useHealth() {
  const [state, setState] = useState<{ status: "ok" | "error" | "unreachable"; problems: string[] } | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    const ctrl = new AbortController();
    getHealth(ctrl.signal).then((h: Health | null) => {
      if (ctrl.signal.aborted) return;
      if (!h) setState({ status: "unreachable", problems: [] });
      else
        setState({
          status: h.status,
          problems: Object.entries(h.checks ?? {})
            .filter(([, c]) => !c.ok)
            .map(([name]) => CHECK_NAMES[name] ?? name),
        });
    });
    return () => ctrl.abort();
  }, [nonce]);

  return state && { ...state, recheck: () => setNonce((n) => n + 1) };
}
