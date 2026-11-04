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
Tryb wyszukiwania:  python evaluation/evaluate.py --mode hybrid   (domyślnie RETRIEVAL_MODE)
Reranking:          python evaluation/evaluate.py --rerank        (domyślnie RERANK; --no-rerank)
                    z --threshold-sweep: próg na odległości i na wyniku rerankera
"""
import argparse
import json
import math
import statistics
import sys
from pathlib import Path

# UTF-8 na Windows (cp1252 nie obsługuje polskich znaków)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from usbot.adapters.pgvector import distinct_urls  # noqa: E402
from usbot.bootstrap import build_retriever  # noqa: E402
from usbot.config import get_settings  # noqa: E402

SETTINGS = get_settings()
TOP_K = SETTINGS.top_k

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
    return {norm_url(u) for u in distinct_urls(SETTINGS.database_url)}


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
        "pages": len(set(top)),
    }


def reciprocal_rank(ranked_urls: list[str], relevant: dict[str, int]) -> float:
    for i, u in enumerate(ranked_urls):
        if u in relevant:
            return 1 / (i + 1)
    return 0.0


# Skale progu dla --threshold-sweep: (pole wiersza, progi, czy wartość musi być ≥ progu)
DIST_SCALE = ("dists", [s / 100 for s in range(30, 61)], False)
RERANK_SCALE = ("rerank", [0.001, 0.002, 0.005, 0.01, 0.02, 0.03, 0.05, 0.07, 0.1, 0.15,
                           0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9], True)


def threshold_sweep(answerable: list[dict], out_of_scope: list[dict], queries: list[dict], k: int,
                    scale: tuple, current: str) -> None:
    """Skutki progu na wybranej skali: odległość (DIST_SCALE) albo wynik rerankera (RERANK_SCALE).

    Pytanie jest odrzucane (bot odpowiada bez LLM-a), gdy żaden fragment z top-k nie
    przechodzi progu. Strata = odrzucone pytanie w zakresie, które miało trafną
    stronę w top-k. Próg może też odciąć trafne fragmenty pytania, które nie zostało
    odrzucone (zostają tylko inne, nietrafne) — kolumna „utracone łącznie” liczy oba
    przypadki. Hit@1 i Hit@k po progu liczone są na tym, co zostaje — to dostaje LLM.
    """
    key, thresholds, at_least = scale
    by_id = {q["id"]: q for q in queries}
    print(f"\nPróg odcięcia na skali {key!r} (k = {k}; obecnie {current}):")
    print(f"{'próg':>6} {'spoza odrzucone':>16} {'w zakresie odrzucone':>21} "
          f"{'w tym z trafieniem (strata)':>28} {'trafienia utracone łącznie':>27} "
          f"{'Hit@1 po progu':>15} {f'Hit@{k} po progu':>15}")
    for t in thresholds:
        def kept(r: dict) -> list[str]:
            return [u for u, v in zip(r["retrieved"][:k], r[key][:k])
                    if (v >= t if at_least else v <= t)]

        oos = sum(not kept(r) for r in out_of_scope)
        rejected = lost = lost_total = hit1 = hitk = 0
        for r in answerable:
            rel = by_id[r["id"]]["relevant"]
            had_hit = any(u in rel for u in r["retrieved"][:k])
            left = kept(r)
            if not left:
                rejected += 1
                lost += had_hit
            lost_total += had_hit and not any(u in rel for u in left)
            hit1 += bool(left) and left[0] in rel
            hitk += any(u in rel for u in left)
        n = len(answerable)
        print(f"{t:>6g} {f'{oos}/{len(out_of_scope)}':>16} {f'{rejected}/{n}':>21} "
              f"{lost:>28} {lost_total:>27} {hit1 / n:>15.3f} {hitk / n:>15.3f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", type=Path, default=QUERIES)
    ap.add_argument("--k", type=int, nargs="+", default=[1, 3, 5, 10])
    ap.add_argument("--verbose", action="store_true", help="pokaż zwrócone URL-e dla pytań bez trafienia")
    ap.add_argument("--out", type=Path, help="zapisz wyniki per pytanie do pliku JSON")
    ap.add_argument("--threshold-sweep", action="store_true",
                    help=f"pokaż skutki progu MAX_DISTANCE 0.30–0.60, z --rerank także progu na "
                         f"wyniku rerankera (straty liczone w top-TOP_K = {TOP_K})")
    ap.add_argument("--mode", choices=["vector", "hybrid"], default=SETTINGS.retrieval_mode,
                    help=f"tryb wyszukiwania (domyślnie RETRIEVAL_MODE = {SETTINGS.retrieval_mode})")
    ap.add_argument("--rerank", action=argparse.BooleanOptionalAction, default=SETTINGS.rerank,
                    help=f"reranking cross-encoderem (domyślnie RERANK = {SETTINGS.rerank})")
    args = ap.parse_args()

    queries = load_queries(args.file)
    ks = sorted(args.k)
    max_k = ks[-1]
    # Sweep patrzy na tyle fragmentów, ile dostaje LLM — może to być więcej niż max_k
    fetch_k = max(max_k, TOP_K) if args.threshold_sweep else max_k

    # Te same adaptery co w cli.py, ale bez progu odcięcia: Retriever.search() zwraca
    # wyniki przed progiem, a skutki progu liczy --threshold-sweep.
    retriever = build_retriever(SETTINGS.model_copy(
        update={"retrieval_mode": args.mode, "rerank": args.rerank}))
    per_query = []
    for q in queries:
        chunks = retriever.search([q["question"]], fetch_k)
        ranked = [norm_url(c.url) for c in chunks]
        row = {
            "id": q["id"],
            "question": q["question"],
            # Najmniejsza odległość, nie odległość pierwszego wyniku: przy karze za stare
            # aktualności (OLD_NEWS_PENALTY) kolejność nie musi iść według odległości.
            "top1_dist": min(float(c.dist) for c in chunks) if chunks else None,
            "retrieved": ranked,
            "dists": [round(float(c.dist), 4) for c in chunks],
        }
        if args.rerank:
            row["rerank"] = [round(c.rerank, 5) for c in chunks]
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

    print(f"\nTryb: {args.mode}{' + rerank' if args.rerank else ''}. Pytania: {len(answerable)} w zakresie, "
          f"{len(out_of_scope)} spoza zakresu\n")
    print(f"{'k':>4} {'Hit@k':>8} {'Recall@k':>9} {'Prec@k':>8} {'nDCG@k':>8}   {'Hit@k (odp. w bazie)':>20}"
          f" {'różnych stron':>14}")
    for k in ks:
        avg = {m: statistics.mean(r["scores"][k][m] for r in answerable)
               for m in ("hit", "recall", "precision", "ndcg", "pages")}
        hit_reachable = statistics.mean(r["scores"][k]["hit"] for r in reachable) if reachable else 0.0
        print(f"{k:>4} {avg['hit']:>8.3f} {avg['recall']:>9.3f} {avg['precision']:>8.3f} {avg['ndcg']:>8.3f}"
              f"   {hit_reachable:>20.3f} {avg['pages']:>14.2f}")
    print(f"\nMRR@{max_k}: {statistics.mean(r['rr'] for r in answerable):.3f}")

    # Odległość kosinusowa najlepszego wyniku — podstawa do dobrania progu odcięcia
    print("\nOdległość top-1 (min / mediana / max):")
    for name, rows in (("w zakresie", answerable), ("spoza zakresu", out_of_scope)):
        d = [r["top1_dist"] for r in rows if r["top1_dist"] is not None]
        if d:
            print(f"  {name:<14} {min(d):.3f} / {statistics.median(d):.3f} / {max(d):.3f}")
    if args.rerank:
        print("Najwyższy wynik rerankera (min / mediana / max):")
        for name, rows in (("w zakresie", answerable), ("spoza zakresu", out_of_scope)):
            s = [max(r["rerank"]) for r in rows if r["rerank"]]
            if s:
                print(f"  {name:<14} {min(s):.4f} / {statistics.median(s):.4f} / {max(s):.4f}")

    if args.threshold_sweep:
        threshold_sweep(answerable, out_of_scope, queries, TOP_K, DIST_SCALE,
                        f"MAX_DISTANCE = {SETTINGS.max_distance}")
        if args.rerank:
            threshold_sweep(answerable, out_of_scope, queries, TOP_K, RERANK_SCALE,
                            f"MIN_RERANK_SCORE = {SETTINGS.min_rerank_score}")
        if args.verbose:
            print("\nSpoza zakresu wg odległości top-1 (z --rerank także najwyższy wynik rerankera):")
            for r in sorted(out_of_scope, key=lambda r: r["top1_dist"] or 1):
                best = f"  {max(r['rerank']):.4f}" if r.get("rerank") else ""
                print(f"  {r['top1_dist']:.3f}{best}  [{r['id']}] {r['question']}")

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
