import type { Source } from "../types";

const PAGE_TYPES: Record<string, string> = {
  news: "Aktualność",
  event: "Wydarzenie",
};

/** Źródła z zdarzenia `sources` (wyniki wyszukiwania, nie tekst modelu). */
export function SourceList({ sources, id }: { sources: Source[]; id: string }) {
  if (sources.length === 0) return null;
  const headingId = `${id}-sources`;
  return (
    <section className="sources" aria-labelledby={headingId}>
      <h3 id={headingId} className="sources-title">
        Źródła ({sources.length})
      </h3>
      <ul className="source-list">
        {sources.map((s) => (
          <li key={s.url} className="source-card">
            {sourceHref(s.url) ? (
              <a href={sourceHref(s.url)} target="_blank" rel="noopener noreferrer" className="source-link">
                <span className="source-name">{s.title?.trim() || s.url}</span>
                <span className="sr-only"> (otwiera się w nowej karcie)</span>
              </a>
            ) : (
              <span className="source-name">{s.title?.trim() || s.url}</span>
            )}
            <span className="source-meta">
              <span className="source-host">{shortUrl(s.url)}</span>
              {PAGE_TYPES[s.page_type] && <span className="source-tag">{PAGE_TYPES[s.page_type]}</span>}
              {s.published_at && (
                <time dateTime={s.published_at}>z {formatDate(s.published_at)}</time>
              )}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Wpis FAQ ma adres `faq://f01#<strona>`: link prowadzi do strony źródłowej, jeśli jest. */
function sourceHref(url: string): string | undefined {
  if (!url.startsWith("faq://")) return url;
  return url.split("#")[1] || undefined;
}

function shortUrl(url: string): string {
  if (url.startsWith("faq://")) return "FAQ, wpis sprawdzony ręcznie";
  try {
    const u = new URL(url);
    const path = decodeURIComponent(u.pathname).replace(/\/$/, "");
    const short = u.hostname.replace(/^www\./, "") + path;
    return short.length > 60 ? short.slice(0, 57) + "…" : short;
  } catch {
    return url;
  }
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString("pl-PL", { day: "numeric", month: "long", year: "numeric" });
}
