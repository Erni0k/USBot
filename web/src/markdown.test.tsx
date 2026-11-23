import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CiteContext, Markdown } from "./markdown";

const html = (text: string) => renderToStaticMarkup(<Markdown text={text} />);

describe("Markdown", () => {
  it("nie wstrzykuje HTML-a z odpowiedzi modelu", () => {
    expect(html("<img src=x onerror=alert(1)>")).not.toContain("<img");
  });

  it("nie zagnieżdża linków, gdy tekst linku jest adresem", () => {
    const out = html("**Facebook:** [https://fb.com/x](https://fb.com/x)");
    expect(out.match(/<a /g)).toHaveLength(1);
  });

  it("linkuje tylko http(s) i obcina kropkę na końcu zdania", () => {
    expect(html("[x](javascript:alert(1))")).not.toContain("<a");
    expect(html("Zobacz https://us.edu.pl/studia.")).toContain('href="https://us.edu.pl/studia"');
  });

  it("ukrywa niedomknięte pogrubienie w urwanej odpowiedzi", () => {
    expect(html("1. **Złożenie wnios")).toBe("<ol><li>Złożenie wnios</li></ol>");
  });

  it("listy, nagłówki i pogrubienie", () => {
    const out = html("## Kroki\n1. **Wejdź** na stronę\n2. Zaloguj się\n- punkt");
    expect(out).toContain("<h5");
    expect(out).toContain("<ol><li><strong>Wejdź</strong> na stronę</li><li>Zaloguj się</li></ol>");
    expect(out).toContain("<ul><li>punkt</li></ul>");
  });
});

describe("Markdown — numery źródeł", () => {
  const cite = { count: 3, title: (n: number) => `Strona ${n}`, onCite: () => undefined };
  const withSources = (text: string) =>
    renderToStaticMarkup(
      <CiteContext.Provider value={cite}>
        <Markdown text={text} />
      </CiteContext.Provider>,
    );

  it("[1, 2] to przyciski z nazwą źródła", () => {
    const out = withSources("Tak jest [1, 2].");
    expect(out.match(/<button type="button" class="cite"/g)).toHaveLength(2);
    expect(out).toContain('aria-label="Źródło 2: Strona 2"');
  });

  it("numer spoza listy i tekst bez źródeł zostają zwykłym tekstem", () => {
    expect(withSources("Zob. [7].")).not.toContain("<button");
    expect(html("Zob. [1].")).toBe("<p>Zob. [1].</p>");
  });

  it("link Markdownu z liczbą w nawiasie nie jest cytowaniem", () => {
    expect(withSources("[1](https://us.edu.pl)")).toContain('href="https://us.edu.pl"');
  });
});
