import { describe, expect, it } from "vitest";
import { buildHistory } from "./history";
import { SseParser } from "./sse";
import type { Turn } from "./types";

describe("SseParser", () => {
  it("składa zdarzenia z kawałków urwanych w dowolnym miejscu", () => {
    const stream =
      'event: sources\ndata: {"type":"sources","sources":[]}\n\n' +
      ": ping\n\n" +
      'event: token\ndata: {"type":"token","text":"Zażółć"}\n\n' +
      'event: done\r\ndata: {"type":"done","answer":"Zażółć"}\r\n\r\n';
    const p = new SseParser();
    const events = [];
    for (let i = 0; i < stream.length; i += 7) events.push(...p.feed(stream.slice(i, i + 7)));
    events.push(...p.flush());
    expect(events.map((e) => e.type)).toEqual(["sources", "token", "done"]);
    expect(events[1]).toEqual({ type: "token", text: "Zażółć" });
  });

  it("pomija komentarze i uszkodzony JSON", () => {
    const p = new SseParser();
    expect(p.feed(": ping\n\ndata: {nie-json\n\n")).toEqual([]);
  });

  it("oddaje ostatnie zdarzenie bez pustej linii na końcu", () => {
    const p = new SseParser();
    expect(p.feed('data: {"type":"done","answer":"x"}')).toEqual([]);
    expect(p.flush()).toEqual([{ type: "done", answer: "x" }]);
  });
});

const turn = (over: Partial<Turn>): Turn => ({
  id: Math.random().toString(),
  question: "q",
  status: "done",
  sources: [],
  answer: "a",
  ...over,
});

describe("buildHistory", () => {
  it("mapuje stany tur zgodnie z kontraktem API", () => {
    const h = buildHistory([
      turn({ question: "q1", answer: "a1" }),
      turn({ question: "q2", status: "error", answer: "" }),
      turn({ question: "q3", status: "clarify", answer: "?", clarify: { message: "Który wydział?", options: [] } }),
      turn({ question: "q4", status: "stopped", answer: "  część " }),
      turn({ question: "q5", status: "writing", answer: "w toku" }),
    ]);
    expect(h).toEqual([
      { role: "user", content: "q1" },
      { role: "assistant", content: "a1" },
      { role: "user", content: "q2" },
      { role: "user", content: "q3" },
      { role: "assistant", content: "Który wydział?" },
      { role: "user", content: "q4" },
      { role: "assistant", content: "część" },
    ]);
  });

  it("wysyła najwyżej 5 ostatnich tur i mieści się w limicie znaków", () => {
    const many = Array.from({ length: 8 }, (_, i) => turn({ question: `q${i}` }));
    expect(buildHistory(many)[0]).toEqual({ role: "user", content: "q3" });

    const long = [turn({ answer: "x".repeat(39_000) }), turn({ question: "ostatnie", answer: "y".repeat(2_000) })];
    expect(buildHistory(long).map((m) => m.content)).toEqual(["ostatnie", "y".repeat(2_000)]);
  });
});
