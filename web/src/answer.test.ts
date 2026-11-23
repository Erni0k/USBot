import { describe, expect, it } from "vitest";
import { citedNumbers, parseAnswer, plainAnswer, sourceTitle } from "./answer";
import type { Source } from "./types";

const ANSWER =
  "Próg wynosi **2099,70 zł** [4]. Wcześniej podawano inną kwotę [3].\n\n" +
  "**Uwaga:** źródło [3] (komunikat z 24 marca 2025 r.) ma ponad rok, więc informacje mogą być nieaktualne. Sprawdź je na us.edu.pl.\n\n" +
  "Źródła:\n[3] https://us.edu.pl/a\n[4] https://us.edu.pl/b";

const src = (url: string, title: string): Source => ({
  url, title, page_type: "news", published_at: null, dist: 0.2, rerank: null,
});

describe("parseAnswer", () => {
  it("wycina stopkę „Źródła:” i wydziela ostrzeżenie", () => {
    const p = parseAnswer(ANSWER);
    expect(p.body).toBe("Próg wynosi **2099,70 zł** [4]. Wcześniej podawano inną kwotę [3].");
    expect(p.note).toMatch(/^Źródło \[3\] \(komunikat z 24 marca 2025 r\.\) ma ponad rok/);
    expect(p.cited).toEqual([3, 4]);
  });

  it("stopka z wpisem FAQ i bez ostrzeżenia", () => {
    const p = parseAnswer("Zaloguj się przez CAS [2].\n\nŹródła:\n[2] FAQ (wpis sprawdzony ręcznie)");
    expect(p.body).toBe("Zaloguj się przez CAS [2].");
    expect(p.note).toBeNull();
  });

  it("w trakcie strumienia: sam nagłówek stopki nie jest pokazywany", () => {
    expect(parseAnswer("Tekst [1].\n\nŹródła:").body).toBe("Tekst [1].");
    expect(parseAnswer("Tekst [1].\n\nŹródła:\n").body).toBe("Tekst [1].");
  });

  it("tekst bez stopki zostaje bez zmian", () => {
    expect(parseAnswer("Źródła są w regulaminie.").body).toBe("Źródła są w regulaminie.");
  });
});

describe("citedNumbers", () => {
  it("[1, 2] i [3] — bez linków Markdownu", () => {
    expect(citedNumbers("a [1, 2] b [3] c [2] [x](https://y.pl) [5](https://z.pl)")).toEqual([1, 2, 3]);
  });
});

describe("sourceTitle", () => {
  it("usuwa „| Student” i zamienia separatory", () => {
    expect(sourceTitle(src("https://a", "stypendium socjalne | Student"))).toBe("Stypendium socjalne");
    expect(sourceTitle(src("https://a", "dziekanat | Wydział Nauk Przyrodniczych"))).toBe("Dziekanat – Wydział Nauk Przyrodniczych");
    expect(sourceTitle(src("https://a", "Sieć bezprzewodowa « Uczelniana Sieć Komputerowa USNET"))).toBe("Sieć bezprzewodowa");
  });
});

describe("plainAnswer (kopiowanie)", () => {
  it("bez znaczników Markdownu, z ostrzeżeniem i adresami cytowanych źródeł", () => {
    const sources = [src("https://x/1", "A"), src("https://x/2", "B"), src("https://us.edu.pl/a", "Stary | Student"), src("https://us.edu.pl/b", "Nowy")];
    const out = plainAnswer(ANSWER, sources);
    expect(out).not.toContain("**");
    expect(out).toContain("Próg wynosi 2099,70 zł [4].");
    expect(out).toContain("Uwaga: Źródło [3]");
    expect(out).toMatch(/Źródła:\n\[3\] Stary – https:\/\/us\.edu\.pl\/a\n\[4\] Nowy – https:\/\/us\.edu\.pl\/b$/);
  });
});
