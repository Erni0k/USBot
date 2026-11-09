import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { Source } from "../types";
import { SourceList } from "./SourceList";

const source = (url: string): Source => ({
  url, title: "Tytuł", page_type: url.startsWith("faq://") ? "faq" : "static",
  published_at: null, dist: 0.2, rerank: null,
});
const html = (...urls: string[]) =>
  renderToStaticMarkup(<SourceList sources={urls.map(source)} id="t" />);

describe("SourceList", () => {
  it("wpis FAQ ze stroną źródłową linkuje do tej strony", () => {
    const out = html("faq://f10#https://us.edu.pl/a");
    expect(out).toContain('href="https://us.edu.pl/a"');
    expect(out).not.toContain('href="faq://');
    expect(out).toContain("FAQ, wpis sprawdzony ręcznie");
  });

  it("wpis FAQ bez strony nie ma linku", () => {
    const out = html("faq://f05");
    expect(out).not.toContain("<a ");
    expect(out).toContain("Tytuł");
  });

  it("zwykła strona linkuje do swojego adresu", () => {
    expect(html("https://us.edu.pl/b")).toContain('href="https://us.edu.pl/b"');
  });
});
