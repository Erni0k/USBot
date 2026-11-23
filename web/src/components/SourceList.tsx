import { domain, isFaq, sourceHref, sourceTitle } from "../answer";
import type { Source } from "../types";

const KINDS: Record<string, string> = {
  news: "komunikat",
  event: "wydarzenie",
};

export const sourceDomId = (turnId: string, n: number) => `${turnId}-src-${n}`;

interface Props {
  sources: Source[];
  /** Id tury — prefiks id elementów, do których prowadzą numery [n]. */
  id: string;
  /** Numery źródeł zacytowane w odpowiedzi. */
  cited: number[];
  /** Odpowiedź zakończona — bez cytowań pokazujemy wtedy wszystkie znalezione strony. */
  final: boolean;
  /** Źródło wskazane kliknięciem [n] — chwilowo podświetlone. */
  target?: number | null;
}

/**
 * Źródła ze zdarzenia `sources` (wyniki wyszukiwania, nie tekst modelu), numerowane jak
 * [n] w odpowiedzi. Na wierzchu tylko cytowane; pozostałe znalezione strony są zwinięte.
 */
export function SourceList({ sources, id, cited, final, target = null }: Props) {
  if (sources.length === 0) return null;
  const all = sources.map((s, i) => ({ s, n: i + 1 }));
  const used = new Set(cited);
  let main = all.filter((x) => used.has(x.n));
  let rest = all.filter((x) => !used.has(x.n));
  const uncited = main.length === 0 && final;
  if (uncited) [main, rest] = [all, []];
  const headingId = `${id}-sources`;

  return (
    <section className="sources" aria-labelledby={headingId}>
      <h4 id={headingId} className="sources-title">
        {uncited ? "Znalezione strony" : "Źródła"}
      </h4>
      {main.length > 0 && (
        <ul className="source-list">
          {main.map(({ s, n }) => (
            <SourceItem key={s.url} source={s} n={n} domId={sourceDomId(id, n)} target={target === n} />
          ))}
        </ul>
      )}
      {rest.length > 0 && (
        <details className="sources-more">
          <summary>
            {main.length > 0 ? "Pozostałe znalezione strony" : "Znalezione strony"} ({rest.length})
          </summary>
          <ul className="source-list">
            {rest.map(({ s, n }) => (
              <SourceItem key={s.url} source={s} n={n} domId={sourceDomId(id, n)} target={target === n} />
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

function SourceItem({ source: s, n, domId, target }: { source: Source; n: number; domId: string; target: boolean }) {
  const href = sourceHref(s.url);
  const faq = isFaq(s.url);
  const title = sourceTitle(s);
  const kind = KINDS[s.page_type];
  return (
    <li className={target ? "source-card is-target" : "source-card"} id={domId} tabIndex={-1}>
      <span className="source-num">
        {n}
        <span className="sr-only">.</span>
      </span>
      <div className="source-body">
        {href ? (
          <a href={href} target="_blank" rel="noopener noreferrer" className="source-link">
            {title}
            <span className="sr-only"> (otwiera się w nowej karcie)</span>
          </a>
        ) : (
          <span className="source-name">{title}</span>
        )}
        <span className="source-meta">
          {faq && <span className="tag">FAQ</span>}
          <span className="source-host">
            {faq ? `wpis sprawdzony ręcznie${href ? `, strona ${domain(href)}` : ""}` : domain(s.url)}
          </span>
          {/* Daty tylko przy komunikatach i wydarzeniach: przy stronach informacyjnych to data utworzenia */}
          {kind && s.published_at && (
            <span className={s.outdated ? "tag tag-warn" : undefined}>
              {kind} z <time dateTime={s.published_at}>{formatDate(s.published_at)}</time>
              {s.outdated && ", ponad rok temu"}
            </span>
          )}
        </span>
      </div>
    </li>
  );
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString("pl-PL", { day: "numeric", month: "long", year: "numeric" });
}
