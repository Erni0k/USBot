import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { Source } from "../types";
import { SourceList } from "./SourceList";

const source = (url: string, extra: Partial<Source> = {}): Source => ({
  url, title: "Tytuł", page_type: url.startsWith("faq://") ? "faq" : "static",
  published_at: null, dist: 0.2, rerank: null, ...extra,
});
const render = (sources: Source[], cited: number[] = [1], final = true) =>
  renderToStaticMarkup(<SourceList sources={sources} id="t" cited={cited} final={final} />);
const html = (...urls: string[]) => render(urls.map((u) => source(u)));

describe("SourceList", () => {
  it("wpis FAQ ze stroną źródłową linkuje do tej strony i ma plakietkę FAQ", () => {
    const out = html("faq://f10#https://us.edu.pl/a");
    expect(out).toContain('href="https://us.edu.pl/a"');
    expect(out).not.toContain('href="faq://');
    expect(out).toContain('<span class="tag">FAQ</span>');
    expect(out).toContain("wpis sprawdzony ręcznie, strona us.edu.pl");
  });

  it("wpis FAQ bez strony nie ma linku, a tytuł jest bez „FAQ:”", () => {
    const out = render([source("faq://f05", { title: "FAQ: Jak zalogować się do USOSweb?" })]);
    expect(out).not.toContain("<a ");
    expect(out).toContain(">Jak zalogować się do USOSweb?</span>");
  });

  it("komunikat ma datę, stary komunikat — wyróżnioną; strona informacyjna nie ma daty", () => {
    const old = render([source("https://us.edu.pl/k", { page_type: "news", published_at: "2024-03-22T10:00:00Z", outdated: true })]);
    expect(old).toContain('class="tag tag-warn"');
    expect(old).toContain("komunikat z ");
    expect(old).toContain("ponad rok temu");
    const page = render([source("https://us.edu.pl/s", { page_type: "student", published_at: "2019-11-01T10:00:00Z" })]);
    expect(page).not.toContain("2019");
  });

  it("numery jak [n] w odpowiedzi; cytowane na wierzchu, pozostałe zwinięte", () => {
    const out = render(["https://a.pl/1", "https://b.pl/2", "https://c.pl/3"].map((u) => source(u)), [2]);
    const [top, rest] = out.split("<details");
    expect(top).toContain('id="t-src-2"');
    expect(top).not.toContain('id="t-src-1"');
    expect(rest).toContain("Pozostałe znalezione strony (2)");
    expect(rest).toContain('id="t-src-1"');
    expect(rest).toContain('id="t-src-3"');
  });

  it("odpowiedź bez cytowań: po zakończeniu wszystkie strony jako „Znalezione strony”", () => {
    const out = render([source("https://a.pl/1"), source("https://b.pl/2")], [], true);
    expect(out).toContain("Znalezione strony");
    expect(out).not.toContain("<details");
  });

  it("tytuł bez dopisku serwisu i bez wersalików, adres skrócony do domeny", () => {
    const out = render([source("https://www.eduroam.us.edu.pl/konfiguracja", { title: "KONFIGURACJA – eduroam" })]);
    expect(out).toContain(">Konfiguracja<");
    expect(out).toContain(">eduroam.us.edu.pl<");
  });
});
