"""Mini-indeks do CI: oznaczone strony plus losowe dystraktory, razem z embeddingami.

Pełna baza (kilkadziesiąt tysięcy fragmentów) nie nadaje się do CI, a evaluate.py
potrzebuje bazy z danymi. Skrypt wybiera wszystkie strony z queries.jsonl i dokłada
losowe strony (stały seed), więc ten sam stan bazy daje ten sam plik.

Plik NIE zawiera treści ani tytułów stron — repozytorium jest publiczne, a strony uczelni
mają nazwiska, e-maile i telefony. Wyszukiwanie wektorowe potrzebuje tylko adresu, typu,
daty, hashy (duplikaty) i embeddingu. Bez treści nie działa więc RETRIEVAL_MODE=hybrid.

Budowanie (z pełnej bazy, DATABASE_URL):  python evaluation/build_fixture.py build
Wczytanie (do pustej bazy):               python evaluation/build_fixture.py load

Procedura aktualizacji i wybór formatu: docs/testy.md (6.8).
"""
import argparse
import gzip
import random
import sys
from pathlib import Path

# UTF-8 na Windows (cp1252 nie obsługuje polskich znaków)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import psycopg  # noqa: E402

from usbot.config import get_settings  # noqa: E402

QUERIES = HERE / "queries.jsonl"
FIXTURE = HERE / "fixture" / "mini_index.sql.gz"
INIT_SQL = HERE.parent / "infra" / "init.sql"
SEED = 20261108
DISTRACTORS = 500
INSERT_BATCH = 200

_COLUMNS = ("url", "page_type", "published_at", "content", "content_hash", "body_hash", "embedding")
_SELECT = ("SELECT url, page_type, published_at::text, '' AS content, content_hash, body_hash, embedding::text "
           "FROM chunks WHERE url = ANY(%s) ORDER BY url, id")


def select_pages(urls: list[str], labeled: set[str], distractors: int, seed: int) -> tuple[list[str], list[str]]:
    """(oznaczone strony z bazy, losowe dystraktory). `labeled` po norm_url()."""
    from evaluate import norm_url
    ordered = sorted(urls)  # kolejność z bazy nie jest stała, a sample() od niej zależy
    chosen = [u for u in ordered if norm_url(u) in labeled]
    rest = [u for u in ordered if norm_url(u) not in labeled]
    if len(rest) < distractors:
        sys.exit(f"Za mało stron na dystraktory: {len(rest)} < {distractors} (to na pewno pełna baza?)")
    return chosen, sorted(random.Random(seed).sample(rest, distractors))


def literal(value: str | None) -> str:
    if value is None:
        return "NULL"
    return "'" + value.replace("'", "''") + "'"


def render_sql(rows: list[tuple]) -> list[str]:
    """Wiersze jako INSERT-y, jeden na linię pliku."""
    statements = []
    for start in range(0, len(rows), INSERT_BATCH):
        values = ",".join("(" + ",".join(literal(v) for v in row) + ")" for row in rows[start:start + INSERT_BATCH])
        statements.append(f"INSERT INTO chunks ({', '.join(_COLUMNS)}) VALUES {values};")
    return statements


def write_fixture(path: Path, header: list[str], statements: list[str]) -> None:
    """Gzip bez czasu i nazwy pliku: te same dane dają identyczny plik."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as f:
        f.write("\n".join([*header, *statements, ""]).encode("utf-8"))


def cmd_build(args) -> None:
    from evaluate import load_queries, norm_url
    labeled = {norm_url(u) for q in load_queries(QUERIES) for u in q["relevant"]}
    with psycopg.connect(get_settings().database_url) as conn:
        urls = [u for (u,) in conn.execute("SELECT DISTINCT url FROM chunks")]
        chosen, distractors = select_pages(urls, labeled, args.distractors, args.seed)
        rows = conn.execute(_SELECT, (chosen + distractors,)).fetchall()
    header = [f"-- Mini-indeks USBot: {len(chosen)} oznaczonych stron, {len(distractors)} dystraktorów "
              f"(seed {args.seed}), {len(rows)} fragmentów. Generuje evaluation/build_fixture.py."]
    write_fixture(args.out, header, render_sql(rows))
    print(f"Oznaczone strony w bazie: {len(chosen)}/{len(labeled)}, dystraktory: {len(distractors)}, "
          f"fragmenty: {len(rows)}, plik: {args.out} ({args.out.stat().st_size / 1e6:.1f} MB)")


def load_fixture(database_url: str, path: Path) -> int:
    """Schemat z init.sql i dane z pliku; zwraca liczbę fragmentów. Tylko do pustej bazy."""
    with psycopg.connect(database_url) as conn:
        conn.execute(INIT_SQL.read_text(encoding="utf-8"))
        if conn.execute("SELECT EXISTS (SELECT 1 FROM chunks)").fetchone()[0]:
            sys.exit("Tabela chunks nie jest pusta — mini-indeks wczytuje się tylko do pustej bazy")
        # HNSW jest przybliżony i buduje się losowo: bez niego wynik jest powtarzalny, a przy
        # kilku tysiącach fragmentów pełny skan trwa milisekundy
        conn.execute("DROP INDEX IF EXISTS chunks_embedding_idx")
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                if line.startswith("INSERT"):
                    conn.execute(line)
        conn.execute("ANALYZE chunks")
        return conn.execute("SELECT count(*) FROM chunks").fetchone()[0]


def cmd_load(args) -> None:
    print(f"Wczytano fragmentów: {load_fixture(get_settings().database_url, args.file)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("build", help="z pełnej bazy do pliku")
    p.add_argument("--distractors", type=int, default=DISTRACTORS)
    p.add_argument("--seed", type=int, default=SEED)
    p.add_argument("--out", type=Path, default=FIXTURE)
    p.set_defaults(run=cmd_build)
    p = sub.add_parser("load", help="z pliku do pustej bazy")
    p.add_argument("--file", type=Path, default=FIXTURE)
    p.set_defaults(run=cmd_load)
    args = ap.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
