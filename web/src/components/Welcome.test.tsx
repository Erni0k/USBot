import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { EXAMPLE_GROUPS, Welcome } from "./Welcome";

const html = (disabled = false) => renderToStaticMarkup(<Welcome disabled={disabled} onAsk={() => undefined} />);

describe("Welcome", () => {
  it("6–8 przykładowych pytań w grupach: dziekanat, USOSweb, stypendia, akademiki, sieć i konta", () => {
    expect(EXAMPLE_GROUPS.map((g) => g.title)).toEqual(["Dziekanat", "USOSweb", "Stypendia", "Akademiki", "Sieć i konta"]);
    const n = EXAMPLE_GROUPS.flatMap((g) => g.questions).length;
    expect(n).toBeGreaterThanOrEqual(6);
    expect(n).toBeLessThanOrEqual(8);
  });

  it("każde pytanie to przycisk pod nagłówkiem grupy", () => {
    const out = html();
    expect(out.match(/<button type="button" class="example"/g)).toHaveLength(8);
    expect(out.match(/<h4>/g)).toHaveLength(EXAMPLE_GROUPS.length);
    expect(out).toContain(">Kiedy jest otwarty dziekanat?</button>");
  });

  it("w trakcie odpowiedzi pytania są wyłączone", () => {
    expect(html(true).match(/disabled=""/g)).toHaveLength(8);
  });

  it("bez sloganu „Jak mogę Ci pomóc”", () => {
    expect(html()).not.toMatch(/mogę (Ci )?pomóc/i);
  });
});
