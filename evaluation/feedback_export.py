"""Eksport ocen -1 do pliku roboczego w formacie queries.jsonl — do ręcznego oznaczenia.

Każdy wpis ma `id`, `question`, pusty `relevant` i `"source": "feedback"`, a w polu
`feedback` to, czego potrzeba przy oznaczaniu: odpowiedź bota, komentarze, źródła i
konfigurację oraz kandydatów z wyszukiwania wektorowego i po słowach kluczowych.
Procedura: docs/testy.md (6.6).

Kolejne uruchomienia DOPISUJĄ tylko nowe pytania — nie nadpisują oznaczeń. Pomijane są
pytania obecne już w pliku roboczym albo w queries.jsonl; powtórzenia tego samego pytania
łączą się w jeden wpis (bez dopytań: „a telefon?” znaczy co innego w każdej rozmowie).

Plik zawiera treści od użytkowników i jest w .gitignore.

Uruchom:          python evaluation/feedback_export.py
Bez modelu:       python evaluation/feedback_export.py --no-candidates
Inny plik:        python evaluation/feedback_export.py --out roboczy.jsonl
"""
import argparse
import json
import sys
from pathlib import Path

# UTF-8 na Windows (cp1252 nie obsługuje polskich znaków)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import psycopg  # noqa: E402

from usbot.api.feedback import ensure_schema  # noqa: E402
from usbot.config import get_settings  # noqa: E402

QUERIES = Path(__file__).with_name("queries.jsonl")
DEFAULT_OUT = Path(__file__).with_name("feedback_do_oznaczenia.jsonl")
CANDIDATES_PER_LIST = 10

_COLUMNS = ("id", "created_at", "question", "answer", "sources", "comment", "followup",
            "retrieval_mode", "rerank", "threshold", "llm_model")


def norm_question(question: str) -> str:
    return " ".join(question.casefold().split())


def read_jsonl(path: Path) -> list[dict]:
    """Wpisy pliku albo koniec programu — zepsutego ręcznie pliku nie wolno nadpisywać."""
    if not path.exists():
        return []
    entries = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as exc:
                sys.exit(f"{path}, linia {number}: niepoprawny JSON ({exc.msg}) — popraw i uruchom ponownie")
    return entries


def fetch_rows(conn: psycopg.Connection) -> list[dict]:
    """Oceny -1, którym nie upłynęła retencja treści, od najstarszej."""
    rows = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM feedback "
        "WHERE rating = -1 AND question IS NOT NULL AND question <> '' ORDER BY id"
    ).fetchall()
    return [dict(zip(_COLUMNS, row, strict=True)) for row in rows]


def build_entries(rows: list[dict], known: list[dict]) -> list[dict]:
    """Nowe wpisy pliku roboczego: bez znanych pytań, powtórzenia złączone."""
    known_ids = {e["id"] for e in known}
    known_questions = {norm_question(e["question"]) for e in known}
    entries: dict[str, dict] = {}
    for row in rows:
        entry_id = f"fb-{row['id']}"
        key = entry_id if row["followup"] else norm_question(row["question"])
        if entry_id in known_ids or (not row["followup"] and key in known_questions):
            continue
        comment = [row["comment"]] if row["comment"] else []
        if key in entries:
            entries[key]["feedback"]["count"] += 1
            entries[key]["feedback"]["comments"] += comment
            continue
        entries[key] = {
            "id": entry_id,
            "question": row["question"],
            "relevant": {},
            "source": "feedback",
            "feedback": {
                "count": 1,
                "created_at": row["created_at"].isoformat(timespec="seconds"),
                "followup": row["followup"],
                "answer": row["answer"],
                "comments": comment,
                "sources": row["sources"],
                "config": {k: row[k] for k in ("retrieval_mode", "rerank", "threshold", "llm_model")},
            },
        }
    return list(entries.values())


def merge_candidates(bot: list[dict], vector: list, keywords: list) -> list[dict]:
    """Jedna lista stron do przejrzenia: skąd się wzięła każda (bez powtórek)."""
    merged: dict[str, dict] = {}
    for label, items in (("bot", bot), ("wektorowo", vector), ("słowa kluczowe", keywords)):
        for item in items:
            url, title = (item["url"], None) if isinstance(item, dict) else (item.url, item.title)
            entry = merged.setdefault(url, {"url": url, "title": title, "found_by": []})
            entry["title"] = entry["title"] or title
            if label not in entry["found_by"]:
                entry["found_by"].append(label)
    return list(merged.values())


def distinct_pages(chunks: list, n: int) -> list:
    """Pierwsze n fragmentów z różnych stron."""
    return list({c.url: c for c in reversed(chunks)}.values())[::-1][:n]


def add_candidates(entries: list[dict], retriever) -> None:
    for entry in entries:
        # Z zapasem: kilka fragmentów jednej strony liczy się jako jedna strona
        vector, keywords = retriever.candidate_lists(entry["question"], 5 * CANDIDATES_PER_LIST)
        entry["feedback"]["candidates"] = merge_candidates(
            entry["feedback"]["sources"],
            distinct_pages(vector, CANDIDATES_PER_LIST),
            distinct_pages(keywords, CANDIDATES_PER_LIST),
        )


def build_retriever(settings):
    from usbot.adapters.pgvector import PgvectorRetriever
    from usbot.bootstrap import build_embedder
    embedder = build_embedder(settings, load_message=None)
    embedder.load()
    return PgvectorRetriever(settings.database_url, embedder, mode="vector", max_per_url=0)


def append_entries(path: Path, entries: list[dict]) -> None:
    prefix = ""
    if path.exists():
        current = path.read_bytes()
        prefix = "\n" if current and not current.endswith(b"\n") else ""
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(prefix + "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries))


def export(database_url: str, out: Path, queries: Path = QUERIES, retriever=None) -> list[dict]:
    """Dopisuje nowe wpisy do `out` i je zwraca."""
    known = read_jsonl(out) + read_jsonl(queries)
    with psycopg.connect(database_url) as conn:
        ensure_schema(conn)
        rows = fetch_rows(conn)
    entries = build_entries(rows, known)
    if entries:
        if retriever is not None:
            add_candidates(entries, retriever)
        append_entries(out, entries)
    return entries


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--no-candidates", action="store_true",
                    help="bez kandydatów z wyszukiwania (nie ładuje modelu embeddingów)")
    args = ap.parse_args()

    settings = get_settings()
    retriever = None if args.no_candidates else build_retriever(settings)
    entries = export(settings.database_url, args.out, retriever=retriever)
    print(f"Dopisano {len(entries)} pytań do {args.out}" if entries else "Brak nowych ocen -1 do eksportu.")
    followups = sum(e["feedback"]["followup"] for e in entries)
    if followups:
        print(f"W tym dopytań do przeformułowania: {followups}")


if __name__ == "__main__":
    main()
