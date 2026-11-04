"""Pomiar czasu odpowiedzi API: do źródeł, do pierwszego tokenu i całkowity.

Wysyła kilka pytań do działającego serwera (`uvicorn usbot.api.main:app`) i czyta
strumień SSE z POST /api/chat. Pierwsze żądanie (rozgrzewka: Ollama ładuje model do
pamięci GPU) jest mierzone osobno i nie wchodzi do średnich.

    python evaluation/latency.py                      # http://127.0.0.1:8000
    python evaluation/latency.py --url http://host:8000 --runs 3

Wyniki: docs/api.md (3.2).
"""
import argparse
import json
import statistics
import sys
import time

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

QUESTIONS = [
    "Jak połączyć się z siecią eduroam na uczelni?",
    "Jak ubiegać się o stypendium socjalne?",
    "Jak zalogować się do USOSweb?",
    "Godziny pracy dziekanatu Wydziału Nauk Przyrodniczych",
    "Jak wyrobić elektroniczną legitymację studencką?",
]
WARMUP = "Jak uzyskać urlop dziekański?"


def sse_events(lines):
    """Zdarzenia (słowniki z `data:`) ze strumienia SSE; komentarze (`: ping`) pomijane."""
    data = []
    for line in lines:
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif line == "" and data:
            yield json.loads("\n".join(data))
            data = []


def ask(client: httpx.Client, url: str, question: str) -> dict:
    start = time.perf_counter()
    t_sources = t_first = None
    tokens, end = 0, None
    with client.stream("POST", f"{url}/api/chat", json={"message": question}) as r:
        r.raise_for_status()
        for event in sse_events(r.iter_lines()):
            now = time.perf_counter() - start
            if event["type"] in ("sources", "refused", "clarify") and t_sources is None:
                t_sources = now
            elif event["type"] == "token":
                tokens += 1
                t_first = now if t_first is None else t_first
            elif event["type"] in ("done", "error"):
                end = event
    total = time.perf_counter() - start
    return {"question": question, "sources_s": t_sources, "first_token_s": t_first,
            "total_s": total, "tokens": tokens, "end": end["type"] if end else None,
            "chars": len(end.get("answer", "")) if end else 0}


def fmt(v: float | None) -> str:
    return "—" if v is None else f"{v:.2f}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--runs", type=int, default=1, help="ile razy zadać każde pytanie")
    args = ap.parse_args()

    with httpx.Client(timeout=httpx.Timeout(300, connect=5)) as client:
        health = client.get(f"{args.url}/api/health").json()
        print("health:", health["status"], {k: v["ok"] for k, v in health["checks"].items()})
        w = ask(client, args.url, WARMUP)
        print(f"rozgrzewka: pierwszy token {fmt(w['first_token_s'])} s, "
              f"całość {fmt(w['total_s'])} s")
        results = [ask(client, args.url, q) for _ in range(args.runs) for q in QUESTIONS]

    print("\n| Pytanie | Źródła [s] | Pierwszy token [s] | Całość [s] | Tokeny | Znaki |")
    print("|---|---|---|---|---|---|")
    for r in results:
        print(f"| {r['question']} | {fmt(r['sources_s'])} | {fmt(r['first_token_s'])} | "
              f"{fmt(r['total_s'])} | {r['tokens']} | {r['chars']} |")
    ft = [r["first_token_s"] for r in results if r["first_token_s"] is not None]
    tot = [r["total_s"] for r in results]
    src = [r["sources_s"] for r in results if r["sources_s"] is not None]
    print(f"| **mediana** | {fmt(statistics.median(src))} | "
          f"{fmt(statistics.median(ft) if ft else None)} | {fmt(statistics.median(tot))} | | |")
    bad = [r for r in results if r["end"] != "done"]
    if bad:
        print("\nBez `done`:", [(r["question"], r["end"]) for r in bad])


if __name__ == "__main__":
    main()
