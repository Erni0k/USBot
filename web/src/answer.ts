import type { Source } from "./types";

/**
 * Koniec odpowiedzi z serwera (usbot/core/citations.py): po treści może być ostrzeżenie
 * „**Uwaga:** źródło [2] (komunikat z …) ma ponad rok, więc informacje mogą być nieaktualne…”,
 * a na samym końcu lista „Źródła:\n[1] adres”. Strona pokazuje źródła jako listę pod
 * odpowiedzią, więc stopkę wycinamy z tekstu, a ostrzeżenie wyświetlamy jako osobny blok.
 * Tekst w turze i w historii dla API zostaje bez zmian.
 */
const FOOTER = /\n*Źródła:\n(?:\[\d+\][^\n]*(?:\n|$))*\s*$/;
// Strumień: nagłówek stopki już przyszedł, adresy jeszcze nie
const FOOTER_START = /\n*Źródła:?\s*$/;
const NOTE = /\n*\*\*Uwaga:\*\*\s+([^\n]*nieaktualne[^\n]*)\s*$/;
const CITATION = /\[\s*(\d{1,2}(?:\s*,\s*\d{1,2})*)\s*\](?!\()/g;

export interface ParsedAnswer {
  /** Treść do wyświetlenia (Markdown), bez stopki i ostrzeżenia. */
  body: string;
  /** Ostrzeżenie o starych komunikatach, bez „**Uwaga:**”, od wielkiej litery. */
  note: string | null;
  /** Numery źródeł użyte w treści, rosnąco (numer n = sources[n - 1]). */
  cited: number[];
}

export function parseAnswer(text: string): ParsedAnswer {
  let body = text.replace(FOOTER, "").replace(FOOTER_START, "");
  let note: string | null = null;
  const m = NOTE.exec(body);
  if (m) {
    note = m[1].charAt(0).toUpperCase() + m[1].slice(1);
    body = body.slice(0, m.index);
  }
  return { body: body.trimEnd(), note, cited: citedNumbers(body) };
}

export function citedNumbers(text: string): number[] {
  const out = new Set<number>();
  for (const m of text.matchAll(CITATION)) for (const n of m[1].split(",")) out.add(Number(n));
  return [...out].sort((a, b) => a - b);
}

/** Tytuł strony bez dopisków serwisu („| Student”, „« Uczelniana Sieć…”) i bez krzyku. */
export function sourceTitle(s: Source): string {
  let t = (s.title ?? "").trim().replace(/^FAQ:\s*/i, "");
  if (!t) return domain(s.url);
  t = t.replace(
    /\s+[|«–-]\s+(?:Student|Uczelniana Sieć Komputerowa USNET|eduroam|Nowy serwis rekrutacyjny UŚ)\s*$/i,
    "",
  );
  t = t.replace(/\s+[|«]\s+/g, " – ");
  if (!/\p{Ll}/u.test(t) && t.length > 4) t = t.charAt(0) + t.slice(1).toLocaleLowerCase("pl");
  return t.charAt(0).toLocaleUpperCase("pl") + t.slice(1);
}

export const isFaq = (url: string) => url.startsWith("faq://");

/** Wpis FAQ ma adres `faq://f01#<strona>`: link prowadzi do strony źródłowej, jeśli jest. */
export function sourceHref(url: string): string | undefined {
  if (!isFaq(url)) return /^https?:\/\//i.test(url) ? url : undefined;
  const page = url.split("#")[1];
  return page && /^https?:\/\//i.test(page) ? page : undefined;
}

export function domain(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/**
 * Odpowiedź do schowka: zwykły tekst bez znaczników Markdownu, z ostrzeżeniem i listą
 * cytowanych źródeł z adresami — żeby dało się ją wkleić do notatki albo maila.
 */
export function plainAnswer(answer: string, sources: Source[]): string {
  const { body, note, cited } = parseAnswer(answer);
  const plain = (t: string) =>
    t
      .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, "$1 ($2)")
      .replace(/(\*\*|__)(.+?)\1/g, "$2")
      .replace(/`([^`]+)`/g, "$1")
      .replace(/^#{1,6}\s+/gm, "")
      .replace(/^\s*[*+]\s+/gm, "- ");
  const parts = [plain(body).trim()];
  if (note) parts.push(`Uwaga: ${plain(note)}`);
  const list = cited
    .filter((n) => n >= 1 && n <= sources.length)
    .map((n) => {
      const s = sources[n - 1];
      const href = sourceHref(s.url);
      return `[${n}] ${sourceTitle(s)}${isFaq(s.url) ? " (FAQ, wpis sprawdzony ręcznie)" : ""}${href ? ` – ${href}` : ""}`;
    });
  if (list.length) parts.push(`Źródła:\n${list.join("\n")}`);
  return parts.join("\n\n");
}
