"""Diagnoza błędów: model dostaje strony wzorcowe zamiast wyszukanych fragmentów.

Pytania z części `tuning`, ten sam ChatService i prompt, inny Retriever: fragmenty stron
z `gold_sources`, najbliższe pytaniu, tyle samo co przy zwykłym wyszukiwaniu (TOP_K), bez
limitu na stronę. Porównanie ze zwykłym pomiarem rozdziela przyczyny błędów:

  brak treści       pytanie nie ma stron wzorcowych albo tych stron nie ma w bazie
  złe wyszukiwanie  zwykły pomiar źle, ze stronami wzorcowymi dobrze
  błąd modelu       fakty są w podanym kontekście, a odpowiedź i tak jest zła
  fragment          strona jest w bazie, ale faktów nie ma w podanych fragmentach

  python evaluation/diagnose_gold.py run --runs 3 --out gold.json
  python evaluation/diagnose_gold.py table zwykly.json gold.json

Metoda i wyniki: docs/testy.md (6.10).
"""
import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evaluate_answers import RecordingLLM, ask, fact_found, git_commit, norm_url  # noqa: E402
from evaluate_golden import GOLDEN, load_golden, score  # noqa: E402


def _chunk(row: tuple):
    from usbot.core.models import Chunk
    return Chunk(id=row[0], url=row[1], title=row[2], content=row[3], page_type=row[4],
                 published_at=row[5], body_hash=row[6], dist=0.0, score=row[7])


class GoldRetriever:
    """Port Retriever: najbliższe pytaniu fragmenty stron wzorcowych, bez wyszukiwania."""

    def __init__(self, database_url: str, embedder, gold_urls: dict[str, list[str]]) -> None:
        import psycopg
        from pgvector.psycopg import register_vector
        self.conn = psycopg.connect(database_url, autocommit=True)
        register_vector(self.conn)
        self.embedder = embedder
        self.gold_urls = gold_urls
        self.db_urls = {norm_url(r[0]): r[0] for r in self.conn.execute("SELECT DISTINCT url FROM chunks")}

    def in_db(self, urls: list[str]) -> list[str]:
        return [self.db_urls[norm_url(u)] for u in urls if norm_url(u) in self.db_urls]

    def search(self, queries, k: int):
        urls = self.in_db(self.gold_urls.get(queries[-1], []))
        if not urls:
            return []
        rows = self.conn.execute(
            "SELECT id, url, title, content, page_type, published_at, body_hash, "
            "embedding <=> %s::vector AS d FROM chunks WHERE url = ANY(%s) ORDER BY d LIMIT %s",
            (self.embedder.embed(queries[-1]), urls, k)).fetchall()
        return [_chunk(r) for r in rows]


def cmd_run(args) -> None:
    from usbot.bootstrap import build_chat_service, build_embedder
    from usbot.config import get_settings
    settings = get_settings()
    golden = load_golden(GOLDEN, "tuning")
    embedder = build_embedder(settings)
    service = build_chat_service(settings)
    retriever = GoldRetriever(settings.database_url, embedder, {g["question"]: g["gold_sources"] for g in golden})
    service.retriever = retriever
    recorder = RecordingLLM(service.llm)
    service.llm = recorder
    data = {"meta": {"date": date.today().isoformat(), "git": git_commit(), "label": "gold-context",
                     "split": "tuning", "llm_model": settings.llm_model, "top_k": settings.top_k},
            "runs": []}
    for number in range(1, args.runs + 1):
        items = []
        data["runs"].append(items)
        for gold in golden:
            item = ask(service, recorder, gold)
            item["score"] = score(item, gold)
            item["category"] = gold["category"]
            item["split"] = gold["split"]
            item["gold_in_db"] = bool(retriever.in_db(gold["gold_sources"]))
            items.append(item)
            args.out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"przebieg {number}/{args.runs}: {sum(i['score']['passed'] for i in items)}/{len(items)}", flush=True)


def diagnosis(gold: dict, plain: list[dict], ctx: list[dict]) -> str:
    """Przyczyna dla jednego pytania: większość z przebiegów decyduje o zaliczeniu."""
    half = len(plain) / 2
    if sum(i["score"]["passed"] for i in plain) > half:
        return "ok"
    if not gold["gold_sources"]:
        return "brak treści (bez źródła)"
    if not ctx[0].get("gold_in_db"):
        return "brak treści (strony nie ma w bazie)"
    if sum(i["score"]["passed"] for i in ctx) > half:
        return "złe wyszukiwanie"
    in_context = all(fact_found(f, ctx[0]["context"]) for f in gold["expected_facts"])
    return "błąd modelu" if in_context else "fragment bez faktu"


def cmd_table(args) -> None:
    golden = {g["id"]: g for g in load_golden(GOLDEN, "tuning")}
    plain, ctx = (json.loads(p.read_text(encoding="utf-8"))["runs"] for p in (args.plain, args.gold))
    counts = Counter()
    print("| id | zwykły | wzorcowe | w kontekście | diagnoza | pytanie |\n|---|---|---|---|---|---|")
    for position, first in enumerate(plain[0]):
        a = [items[position] for items in plain]
        b = [items[position] for items in ctx]
        gold = golden[first["id"]]
        kind = diagnosis(gold, a, b)
        counts[kind] += 1
        retrieved = sum(bool(i["score"]["gold_in_context"]) for i in a)
        # Bez stron wzorcowych w bazie model dostaje pusty kontekst: wynik nic nie mówi
        with_gold = (f"{sum(i['score']['passed'] for i in b)}/{len(b)}"
                     if gold["gold_sources"] and b[0].get("gold_in_db") else "—")
        print(f"| {first['id']} | {sum(i['score']['passed'] for i in a)}/{len(a)} "
              f"| {with_gold} | {retrieved}/{len(a)} "
              f"| {kind} | {first['question'][:60]} |")
    print()
    for kind, n in counts.most_common():
        print(f"{kind}: {n}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run", help="odpowiedzi ze stronami wzorcowymi w kontekście")
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--out", type=Path, required=True)
    p.set_defaults(func=cmd_run)
    p = sub.add_parser("table", help="tabela przyczyn błędów")
    p.add_argument("plain", type=Path)
    p.add_argument("gold", type=Path)
    p.set_defaults(func=cmd_table)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
