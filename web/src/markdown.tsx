import type { ReactNode } from "react";

/**
 * Mały renderer Markdownu dla odpowiedzi modelu: nagłówki, listy, akapity, bloki kodu,
 * **pogrubienie**, *kursywa*, `kod`, [linki](url) i gołe adresy URL. Buduje elementy
 * Reacta (bez innerHTML), więc tekst z modelu nie może wstrzyknąć HTML-a; linki tylko
 * http(s). Działa też na niedokończonym tekście w trakcie strumieniowania.
 */
export function Markdown({ text }: { text: string }) {
  return <>{blocks(dropUnclosed(text))}</>;
}

/** Urwana odpowiedź (strumień, „Zatrzymaj”) może mieć niedomknięte `**` albo `` ` `` —
 *  bez tego czytelnik zobaczyłby gołe gwiazdki. */
function dropUnclosed(text: string): string {
  for (const marker of ["**", "`"]) {
    if (text.split(marker).length % 2 === 0) {
      const i = text.lastIndexOf(marker);
      text = text.slice(0, i) + text.slice(i + marker.length);
    }
  }
  return text;
}

type Block =
  | { kind: "h"; level: number; text: string }
  | { kind: "p"; text: string }
  | { kind: "ul" | "ol"; items: string[] }
  | { kind: "code"; text: string };

function parseBlocks(text: string): Block[] {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  const out: Block[] = [];
  let para: string[] = [];
  const flushPara = () => {
    if (para.length) out.push({ kind: "p", text: para.join("\n") });
    para = [];
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {
      flushPara();
      const code: string[] = [];
      while (++i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i]);
      out.push({ kind: "code", text: code.join("\n") });
      continue;
    }
    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    if (h) {
      flushPara();
      out.push({ kind: "h", level: h[1].length, text: h[2].replace(/\s#+\s*$/, "") });
      continue;
    }
    const li = /^\s*(?:([-*+•])|(\d+)[.)])\s+(.*)$/.exec(line);
    if (li) {
      flushPara();
      const kind = li[1] ? "ul" : "ol";
      const last = out[out.length - 1];
      if (last && last.kind === kind) last.items.push(li[3]);
      else out.push({ kind, items: [li[3]] });
      continue;
    }
    if (!line.trim()) {
      flushPara();
      continue;
    }
    // Kontynuacja punktu listy (wcięta linia bezpośrednio pod nim)
    const last = out[out.length - 1];
    if (!para.length && /^\s{2,}\S/.test(line) && last && (last.kind === "ul" || last.kind === "ol")) {
      last.items[last.items.length - 1] += " " + line.trim();
      continue;
    }
    para.push(line);
  }
  flushPara();
  return out;
}

function blocks(text: string): ReactNode[] {
  return parseBlocks(text).map((b, i) => {
    switch (b.kind) {
      case "h": {
        // Nagłówki odpowiedzi są podrzędne wobec nagłówka strony (h1) i dymka
        const level = Math.min(6, b.level + 2);
        const Tag = `h${level}` as "h3";
        return (
          <Tag key={i} className="md-h">
            {inline(b.text)}
          </Tag>
        );
      }
      case "p":
        return <p key={i}>{withBreaks(b.text)}</p>;
      case "ul":
        return (
          <ul key={i}>
            {b.items.map((it, j) => (
              <li key={j}>{inline(it)}</li>
            ))}
          </ul>
        );
      case "ol":
        return (
          <ol key={i}>
            {b.items.map((it, j) => (
              <li key={j}>{inline(it)}</li>
            ))}
          </ol>
        );
      case "code":
        return (
          <pre key={i}>
            <code>{b.text}</code>
          </pre>
        );
    }
  });
}

function withBreaks(text: string): ReactNode[] {
  const lines = text.split("\n");
  return lines.flatMap((l, i) => (i < lines.length - 1 ? [...inline(l, `${i}-`), <br key={`br${i}`} />] : inline(l, `${i}-`)));
}

const INLINE =
  /(`[^`]+`)|(\*\*[^*]+\*\*|__[^_]+__)|(\[[^\]]+\]\([^)\s]+\))|(https?:\/\/[^\s<>()[\]]+)|(\*[^*\s][^*]*\*|_[^_\s][^_]*_)/g;

/** `links=false` wewnątrz tekstu linku — <a> nie może zawierać innego <a>. */
function inline(text: string, prefix = "", links = true): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let k = 0;
  const key = () => `${prefix}${k++}`;
  for (const m of text.matchAll(INLINE)) {
    const start = m.index ?? 0;
    if (start > last) out.push(text.slice(last, start));
    const [all, code, bold, link, url, em] = m;
    if (code) out.push(<code key={key()}>{code.slice(1, -1)}</code>);
    else if (bold) out.push(<strong key={key()}>{inline(bold.slice(2, -2), key(), links)}</strong>);
    else if (link) {
      const lm = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(link)!;
      const label = inline(lm[1], key(), false);
      if (links) out.push(<SafeLink key={key()} href={lm[2]}>{label}</SafeLink>);
      else out.push(...label);
    } else if (url && !links) {
      out.push(url);
    } else if (url) {
      // Kropka/przecinek/nawias na końcu zdania nie należą do adresu
      const trimmed = url.replace(/[.,;:!?'"”’)]+$/, "");
      out.push(<SafeLink key={key()} href={trimmed}>{trimmed}</SafeLink>);
      if (trimmed.length < url.length) out.push(url.slice(trimmed.length));
    } else if (em) out.push(<em key={key()}>{inline(em.slice(1, -1), key(), links)}</em>);
    else out.push(all);
    last = start + all.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function SafeLink({ href, children }: { href: string; children: ReactNode }) {
  if (!/^https?:\/\//i.test(href)) return <>{children}</>;
  return (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
      <span className="sr-only"> (otwiera się w nowej karcie)</span>
    </a>
  );
}
