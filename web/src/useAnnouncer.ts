import { useMemo, useRef, useState } from "react";

export interface Announcement {
  id: number;
  text: string;
}

/**
 * Komunikaty dla czytników ekranu (region aria-live="polite"). Strumieniowana odpowiedź
 * jest czytana zdaniami: tokeny zbieramy, a do regionu dopisujemy każde zakończone zdanie
 * jako nowy element (aria-relevant="additions" — czytnik kolejkuje je zamiast przerywać).
 * Czytanie każdego tokenu osobno byłoby nieużywalne.
 */
export function useAnnouncer() {
  const [items, setItems] = useState<Announcement[]>([]);
  const pending = useRef("");
  const nextId = useRef(0);

  const actions = useMemo(() => {
    const push = (text: string) => {
      const clean = plain(text);
      if (!clean) return;
      const id = nextId.current++;
      // Starsze wpisy już przeczytane — trzymamy krótki ogon, żeby DOM nie rósł
      setItems((xs) => [...xs.slice(-30), { id, text: clean }]);
    };
    return {
      say: push,
      /** Kolejny token odpowiedzi; zakończone zdania od razu trafiają do regionu. */
      stream(text: string) {
        pending.current += text;
        // Zdanie kończy znak .!? przed odstępem (nie kropka w „1.5” czy w adresie) albo
        // koniec linii; flaga `y` — kolejne zdania muszą przylegać do siebie
        const re = /[\s\S]*?(?:[.!?]+(?=\s)|\n)/y;
        const buf = pending.current;
        let pos = 0;
        for (;;) {
          re.lastIndex = pos;
          const m = re.exec(buf);
          if (!m || !m[0]) break;
          push(m[0]);
          pos = re.lastIndex;
        }
        if (pos) pending.current = buf.slice(pos);
      },
      /** Koniec odpowiedzi: reszta tekstu bez kropki na końcu. */
      flush() {
        push(pending.current);
        pending.current = "";
      },
      reset() {
        pending.current = "";
        setItems([]);
      },
    };
  }, []);
  return { items, actions };
}

/** Bez znaczników Markdownu, które czytnik przeczytałby jako „gwiazdka”, „krzyżyk”. */
function plain(text: string): string {
  return text
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/[*_`#>]+/g, "")
    .replace(/^\s*[-•]\s+/gm, "")
    .replace(/\s+/g, " ")
    .trim();
}
