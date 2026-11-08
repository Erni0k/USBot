"""Test 80 pytań z odpowiedziami wzorcowymi: kilka przebiegów, porównanie wariantów.

Odpowiedzi nie są deterministyczne, więc wariant porównujemy ze średnią i rozrzutem z kilku
przebiegów. Wynik pytania liczy się automatycznie z faktów spisanych przed pierwszym
pomiarem (`expected_facts`, `forbidden_facts` w golden_80.jsonl) — to przybliżenie, które
służy do porównywania wariantów. Końcowa ocena to czytanie odpowiedzi części testowej.

Podział: `tuning` do strojenia, `test` wyłącznie do wyniku końcowego.

  python evaluation/evaluate_golden.py run --split tuning --runs 3 --out wynik.json
  python evaluation/evaluate_golden.py report wynik.json [--split test]
  python evaluation/evaluate_golden.py compare przed.json po.json [--split test]

Metoda i wyniki: docs/testy.md (6.9).
"""
import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evaluate_answers import (  # noqa: E402
    RecordingLLM,
    ask,
    classify,
    context_urls,
    fact_found,
    git_commit,
    norm_url,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

GOLDEN = Path(__file__).resolve().parent / "golden_80.jsonl"
REFUSALS = {"refused_threshold", "refused_llm"}


def load_golden(path: Path, split: str) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if split == "all" or r["split"] == split]


def score(item: dict, gold: dict) -> dict:
    """Zaliczenie pytania według `expect`: answer | clarify | refuse_ok."""
    kind = classify(item)
    answered = kind == "answered"
    missing = [f for f in gold["expected_facts"] if not (answered and fact_found(f, item["answer"]))]
    forbidden = [f for f in gold["forbidden_facts"] if answered and fact_found(f, item["answer"])]
    facts_ok = answered and not missing and not forbidden
    if gold["expect"] == "clarify":
        passed = kind == "clarify"
    elif gold["expect"] == "refuse_ok":
        passed = kind in REFUSALS or kind == "clarify" or facts_ok
    else:
        passed = facts_ok
    urls = {norm_url(u) for u in gold["gold_sources"]}
    return {"passed": passed, "kind": kind, "missing": missing, "forbidden": forbidden,
            "false_refusal": gold["expect"] == "answer" and kind in REFUSALS,
            "gold_in_context": bool(urls & context_urls(item)) if urls else None}


def cmd_run(args) -> None:
    from usbot.bootstrap import build_chat_service
    from usbot.config import get_settings
    settings = get_settings()
    if args.prompt_file:  # wariant promptu do porównania, bez zmiany kodu
        from usbot.core import prompts
        prompts.SYSTEM_PROMPT = args.prompt_file.read_text(encoding="utf-8")
    service = build_chat_service(settings)
    recorder = RecordingLLM(service.llm)
    service.llm = recorder

    golden = load_golden(args.file, args.split)[:args.limit]
    data = {"meta": {"date": date.today().isoformat(), "git": git_commit(), "label": args.label,
                     "split": args.split, "llm_model": settings.llm_model, "rerank": settings.rerank,
                     "temperature": settings.llm_temperature, "top_k": settings.top_k,
                     "old_news_penalty": settings.old_news_penalty},
            "runs": []}
    for number in range(1, args.runs + 1):
        items = []
        data["runs"].append(items)
        for gold in golden:
            item = ask(service, recorder, gold)
            item["score"] = score(item, gold)
            item["category"] = gold["category"]
            item["split"] = gold["split"]
            items.append(item)
            args.out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"przebieg {number}/{args.runs}: {sum(i['score']['passed'] for i in items)}/{len(items)}", flush=True)
    print(f"Zapisano: {args.out}")


def run_stats(items: list[dict]) -> dict:
    return {"zaliczone": sum(i["score"]["passed"] for i in items),
            "niesłuszne odmowy": sum(i["score"]["false_refusal"] for i in items),
            "zakazane treści": sum(bool(i["score"]["forbidden"]) for i in items),
            "mediana czasu [s]": statistics.median(i["seconds"] for i in items)}


def spread(values: list[float]) -> str:
    return f"{statistics.mean(values):.1f} (od {min(values):g} do {max(values):g})"


def load_result(path: Path, split: str = "all") -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if split != "all":
        data["runs"] = [[i for i in items if i["split"] == split] for items in data["runs"]]
    return data


def cmd_report(args) -> None:
    data = load_result(args.result, args.split)
    runs = data["runs"]
    print(json.dumps(data["meta"], ensure_ascii=False))
    print(f"Pytań: {len(runs[0])}, przebiegów: {len(runs)}\n")
    stats = [run_stats(items) for items in runs]
    for key in stats[0]:
        print(f"{key:<22} {spread([s[key] for s in stats])}")

    by_cat = defaultdict(list)
    for items in runs:
        for i in items:
            by_cat[i["category"]].append(i["score"]["passed"])
    print("\nKategorie (odsetek zaliczeń ze wszystkich przebiegów):")
    for cat, flags in by_cat.items():
        print(f"  {cat:<12} {sum(flags)}/{len(flags)}")

    print("\nNiezaliczone (ile razy na przebiegi, rodzaj, brakujące fakty):")
    for position, first in enumerate(runs[0]):
        versions = [items[position] for items in runs]
        fails = [v for v in versions if not v["score"]["passed"]]
        if fails:
            kinds = Counter(v["score"]["kind"] for v in fails)
            print(f"  {first['id']} {len(fails)}/{len(runs)} {dict(kinds)} "
                  f"brak: {fails[0]['score']['missing']} zakaz: {fails[0]['score']['forbidden']}  "
                  f"{first['question'][:60]}")


def cmd_compare(args) -> None:
    before, after = load_result(args.before, args.split), load_result(args.after, args.split)
    stats = [[run_stats(items) for items in d["runs"]] for d in (before, after)]
    print(f"{'':<22} {'przed':<26} po")
    for key in stats[0][0]:
        print(f"{key:<22} {spread([s[key] for s in stats[0]]):<26} {spread([s[key] for s in stats[1]])}")
    rates = []
    for d in (before, after):
        count = Counter()
        for items in d["runs"]:
            count.update(i["id"] for i in items if i["score"]["passed"])
        rates.append({i["id"]: count[i["id"]] / len(d["runs"]) for i in d["runs"][0]})
    better = sorted(q for q in rates[0] if q in rates[1] and rates[1][q] > rates[0][q])
    worse = sorted(q for q in rates[0] if q in rates[1] and rates[1][q] < rates[0][q])
    print(f"\nLepsze ({len(better)}): {' '.join(better)}\nGorsze ({len(worse)}): {' '.join(worse)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run", help="odpowiedzi przez ChatService, kilka przebiegów")
    p.add_argument("--file", type=Path, default=GOLDEN)
    p.add_argument("--split", choices=("tuning", "test", "all"), default="tuning")
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--limit", type=int)
    p.add_argument("--label", default="")
    p.add_argument("--prompt-file", type=Path, help="prompt systemowy zamiast SYSTEM_PROMPT")
    p.add_argument("--out", type=Path, required=True)
    p.set_defaults(func=cmd_run)
    p = sub.add_parser("report", help="wynik jednego pomiaru")
    p.add_argument("result", type=Path)
    p.add_argument("--split", choices=("tuning", "test", "all"), default="tuning")
    p.set_defaults(func=cmd_report)
    p = sub.add_parser("compare", help="porównanie dwóch pomiarów")
    p.add_argument("before", type=Path)
    p.add_argument("after", type=Path)
    p.add_argument("--split", choices=("tuning", "test", "all"), default="tuning")
    p.set_defaults(func=cmd_compare)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
