"""Ewaluacja samego retrievalu (bez LLM-a) na zbiorze pytań z oznaczonymi URL-ami.

Każde pytanie w `queries.jsonl` ma listę stron, które powinny zostać zwrócone,
z oceną trafności: 2 = strona odpowiada na pytanie, 1 = częściowo / pomocniczo.
Pytania z pustym `relevant` są spoza zakresu bazy — liczymy dla nich tylko
odległość najlepszego wyniku (przyda się do dobrania progu odcięcia).

Trafność oceniamy na poziomie URL, nie chunka: ID chunków zmieniają się przy
każdym re-crawlu, a URL-e są stabilne. Kilka chunków z tej samej strony liczy się
w nDCG tylko raz (pierwsze wystąpienie).

Oznaczone strony nie muszą być w bazie — skrypt osobno raportuje pokrycie, żeby
odróżnić błąd crawlera (strony nie ma w bazie) od błędu wyszukiwania (jest, ale
nie została znaleziona).

Uruchom:            python evaluation/evaluate.py
Inne k / szczegóły: python evaluation/evaluate.py --k 1 3 5 10 20 --verbose
Zapis wyników:      python evaluation/evaluate.py --out wyniki.json
Dobór MAX_DISTANCE: python evaluation/evaluate.py --threshold-sweep
"""
import argparse
import json
import math
import statistics
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cli import DATABASE_URL, MAX_DISTANCE, TOP_K, retrieve  # noqa: E402

QUERIES = Path(__file__).with_name("queries.jsonl")


def norm_url(url: str) -> str:
    """Ujednolica URL: bez schematu, 'www.', końcowego '/' i fragmentu."""
    url = url.split("#", 1)[0].strip().lower()
    url = url.removeprefix("https://").removeprefix("http://").removeprefix("www.")
    return url.rstrip("/")


def load_queries(path: Path) -> list[dict]:
    queries = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                q = json.loads(line)
                q["relevant"] = {norm_url(u): g for u, g in q.get("relevant", {}).items()}
                queries.append(q)
    return queries


def indexed_urls() -> set[str]:
    with psycopg.connect(DATABASE_URL) as conn:
        return {norm_url(r[0]) for r in conn.execute("SELECT DISTINCT url FROM chunks")}


def score(ranked_urls: list[str], relevant: dict[str, int], k: int) -> dict:
    top = ranked_urls[:k]
    found = {u for u in top if u in relevant}

    # nDCG: każda strona liczy się tylko przy pierwszym wystąpieniu
    seen: set[str] = set()
    dcg = 0.0
    for i, u in enumerate(top):
        if u in relevant and u not in seen:
            dcg += (2 ** relevant[u] - 1) / math.log2(i + 2)
        seen.add(u)
    ideal = sorted(relevant.values(), reverse=True)[:k]
    idcg = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(ideal))

    return {
        "hit": 1.0 if found else 0.0,
        "recall": len(found) / len(relevant),
        "precision": sum(u in relevant for u in top) / k,
        "ndcg": dcg / idcg,
    }


def reciprocal_rank(ranked_urls: list[str], relevant: dict[str, int]) -> float:
    for i, u in enumerate(ranked_urls):
        if u in relevant:
            return 1 / (i + 1)
    return 0.0


def threshold_sweep(answerable: list[dict], out_of_scope: list[dict], queries: list[dict], k: int) -> None:
    """Skutki progu MAX_DISTANCE dla progów 0.30–0.60.

    Pytanie jest odrzucane (bot odpowiada bez LLM-a), gdy nawet najlepszy fragment
    jest dalej niż próg. Strata = odrzucone pytanie w zakresie, które miało trafną
    stronę w top-k. Próg może też odciąć trafne fragmenty pytania, które nie zostało
    odrzucone (zostają tylko bliższe, nietrafne) — ostatnia kolumna liczy oba przypadki.
    """
    by_id = {q["id"]: q for q in queries}
    print(f"\nPróg odcięcia (k = {k}; obecnie MAX_DISTANCE = {MAX_DISTANCE}):")
    print(f"{'próg':>6} {'spoza odrzucone':>16} {'w zakresie odrzucone':>21} "
          f"{'w tym z trafieniem (strata)':>28} {'trafienia utracone łącznie':>27}")
    for step in range(30, 61):
        t = step / 100
        oos = sum(r["top1_dist"] is None or r["top1_dist"] > t for r in out_of_scope)
        rejected = lost = lost_total = 0
        for r in answerable:
            rel = by_id[r["id"]]["relevant"]
            top = list(zip(r["retrieved"][:k], r["dists"][:k]))
            had_hit = any(u in rel for u, _ in top)
            kept_hit = any(u in rel and d <= t for u, d in top)
            if r["top1_dist"] is None or r["top1_dist"] > t:
                rejected += 1
                lost += had_hit
            lost_total += had_hit and not kept_hit
        print(f"{t:>6.2f} {f'{oos}/{len(out_of_scope)}':>16} {f'{rejected}/{len(answerable)}':>21} "
              f"{lost:>28} {lost_total:>27}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", type=Path, default=QUERIES)
    ap.add_argument("--k", type=int, nargs="+", default=[1, 3, 5, 10])
    ap.add_argument("--verbose", action="store_true", help="pokaż zwrócone URL-e dla pytań bez trafienia")
    ap.add_argument("--out", type=Path, help="zapisz wyniki per pytanie do pliku JSON")
    ap.add_argument("--threshold-sweep", action="store_true",
                    help=f"pokaż skutki progu MAX_DISTANCE 0.30–0.60 (straty liczone w top-TOP_K = {TOP_K})")
    args = ap.parse_args()

    queries = load_queries(args.file)
    ks = sorted(args.k)
    max_k = ks[-1]
    # Sweep patrzy na tyle fragmentów, ile dostaje LLM — może to być więcej niż max_k
    fetch_k = max(max_k, TOP_K) if args.threshold_sweep else max_k

    per_query = []
    for q in queries:
        chunks = retrieve(q["question"], k=fetch_k, max_distance=None)
        ranked = [norm_url(c["url"]) for c in chunks]
        row = {
            "id": q["id"],
            "question": q["question"],
            "top1_dist": float(chunks[0]["dist"]) if chunks else None,
            "retrieved": ranked,
            "dists": [round(float(c["dist"]), 4) for c in chunks],
        }
        if q["relevant"]:
            row["rr"] = reciprocal_rank(ranked[:max_k], q["relevant"])
            row["scores"] = {k: score(ranked, q["relevant"], k) for k in ks}
        per_query.append(row)

    answerable = [r for r in per_query if "scores" in r]
    out_of_scope = [r for r in per_query if "scores" not in r]

    # Pokrycie: czy oznaczone strony w ogóle trafiły do bazy
    indexed = indexed_urls()
    by_id = {q["id"]: q for q in queries}
    reachable = [r for r in answerable if indexed & by_id[r["id"]]["relevant"].keys()]

    print(f"\nPytania: {len(answerable)} w zakresie, {len(out_of_scope)} spoza zakresu\n")
    print(f"{'k':>4} {'Hit@k':>8} {'Recall@k':>9} {'Prec@k':>8} {'nDCG@k':>8}   {'Hit@k (odp. w bazie)':>20}")
    for k in ks:
        avg = {m: statistics.mean(r["scores"][k][m] for r in answerable)
               for m in ("hit", "recall", "precision", "ndcg")}
        hit_reachable = statistics.mean(r["scores"][k]["hit"] for r in reachable) if reachable else 0.0
        print(f"{k:>4} {avg['hit']:>8.3f} {avg['recall']:>9.3f} {avg['precision']:>8.3f} {avg['ndcg']:>8.3f}"
              f"   {hit_reachable:>20.3f}")
    print(f"\nMRR@{max_k}: {statistics.mean(r['rr'] for r in answerable):.3f}")

    # Odległość kosinusowa najlepszego wyniku — podstawa do dobrania progu odcięcia
    print("\nOdległość top-1 (min / mediana / max):")
    for name, rows in (("w zakresie", answerable), ("spoza zakresu", out_of_scope)):
        d = [r["top1_dist"] for r in rows if r["top1_dist"] is not None]
        if d:
            print(f"  {name:<14} {min(d):.3f} / {statistics.median(d):.3f} / {max(d):.3f}")

    if args.threshold_sweep:
        threshold_sweep(answerable, out_of_scope, queries, TOP_K)
        if args.verbose:
            print("\nSpoza zakresu wg odległości top-1:")
            for r in sorted(out_of_scope, key=lambda r: r["top1_dist"] or 1):
                print(f"  {r['top1_dist']:.3f}  [{r['id']}] {r['question']}")

    labeled = {u for q in queries for u in q["relevant"]}
    best = {u for q in queries for u, g in q["relevant"].items() if g == max(q["relevant"].values())}
    print(f"\nPokrycie bazy: {len(labeled & indexed)}/{len(labeled)} oznaczonych stron, "
          f"{len(best & indexed)}/{len(best)} najlepszych odpowiedzi; "
          f"{len(reachable)}/{len(answerable)} pytań ma w bazie choć jedną trafną stronę")

    misses = [r for r in answerable if r["rr"] == 0]
    if misses:
        print(f"\nBez trafienia w top-{max_k} ({len(misses)}):")
        for r in misses:
            in_db = indexed & by_id[r["id"]]["relevant"].keys()
            reason = "retrieval: strona jest w bazie" if in_db else "crawl: brak stron w bazie"
            print(f"  [{r['id']}] {r['question']}  ({reason})")
            if args.verbose:
                for u in dict.fromkeys(r["retrieved"]):
                    print(f"        -> {u}")

    if args.out:
        args.out.write_text(json.dumps(per_query, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nZapisano: {args.out}")


if __name__ == "__main__":
    main()
