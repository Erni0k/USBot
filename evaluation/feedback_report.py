"""Raport z ocen użytkowników: odsetek 👎 w czasie i najczęstsze tematy pytań z 👎.

Temat to dopasowanie słów pytania do listy TOPICS (bez diakrytyków, po początku wyrazu —
polskie odmiany). Pytanie może należeć do kilku tematów. Słowa z pytań spoza listy są
wypisane osobno: pokazują, jaki temat warto do niej dopisać. Tematy liczymy tylko z ocen,
którym nie upłynęła retencja treści (FEEDBACK_RETENTION_DAYS); trend — ze wszystkich.

Uruchom:              python evaluation/feedback_report.py
Dzień zamiast tygodnia: python evaluation/feedback_report.py --by day
"""
import argparse
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

# UTF-8 na Windows (cp1252 nie obsługuje polskich znaków i emoji)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import psycopg  # noqa: E402

from usbot.adapters.pgvector import FTS_STOPWORDS  # noqa: E402
from usbot.api.feedback import ensure_schema  # noqa: E402
from usbot.config import get_settings  # noqa: E402

OTHER = "inne"
TOPICS = {
    "dziekanat": r"\bdziekanat",
    "stypendia i zapomogi": r"\b(?:stypend|zapomog)",
    "akademiki": r"\b(?:akademik|dom\w* studenta)",
    "eduroam i Wi-Fi": r"\b(?:eduroam|wi-?fi)",
    "USOS": r"\busos",
    "konto i hasło": r"\b(?:hasl|logow|zaloguj|login|kont[oay]\b)",
    "VPN i poczta": r"\b(?:vpn|poczt|thunderbird|e-?mail)",
    "legitymacja": r"\b(?:legitymac|els\b)",
    "rekrutacja": r"\b(?:rekrut|irk\b|kandydat)",
    "urlopy": r"\burlop",
    "egzaminy i sesja": r"\b(?:egzamin|sesj|zalicz|warunek|warunk)",
    "opłaty": r"\b(?:oplat|czesne|czesnego)",
    "biblioteka": r"\b(?:bibliotek|ciniba)",
    "Erasmus i wyjazdy": r"\b(?:erasmus|wyjazd)",
    "praca dyplomowa": r"\b(?:dyplom|licencjat|magistersk|obron)",
}
_PATTERNS = {name: re.compile(pattern) for name, pattern in TOPICS.items()}


def normalize(text: str) -> str:
    """Małe litery bez diakrytyków; „ł” nie rozkłada się w NFKD."""
    decomposed = unicodedata.normalize("NFKD", text.lower().replace("ł", "l"))
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def topics_of(question: str) -> list[str]:
    text = normalize(question)
    return [name for name, pattern in _PATTERNS.items() if pattern.search(text)] or [OTHER]


def summarize_topics(questions: list[str]) -> tuple[Counter, Counter]:
    """Liczba pytań w tematach i słowa z pytań, które nie pasują do żadnego tematu."""
    topics: Counter = Counter()
    other_words: Counter = Counter()
    for question in questions:
        found = topics_of(question)
        topics.update(found)
        if found == [OTHER]:
            # set: słowo liczy się raz na pytanie
            other_words.update({w for w in re.findall(r"\w{3,}", normalize(question))
                                if w not in FTS_STOPWORDS})
    return topics, other_words


def percent(part: int, total: int) -> str:
    return f"{100 * part / total:.1f}%" if total else "—"


def fetch_periods(conn: psycopg.Connection, by: str) -> list[tuple]:
    """(początek okresu w UTC, liczba ocen, liczba ocen -1), od najstarszego."""
    return conn.execute(
        "SELECT date_trunc(%s, created_at AT TIME ZONE 'UTC')::date AS period, count(*), "
        "count(*) FILTER (WHERE rating = -1) FROM feedback GROUP BY period ORDER BY period",
        (by,),
    ).fetchall()


def fetch_down_questions(conn: psycopg.Connection) -> list[str]:
    return [q for (q,) in conn.execute(
        "SELECT question FROM feedback WHERE rating = -1 AND question IS NOT NULL AND question <> ''")]


def render(periods: list[tuple], questions: list[str], by: str, top: int) -> str:
    if not periods:
        return "Brak ocen w bazie."
    label = "tydzień od" if by == "week" else "dzień"
    lines = [f"Odsetek 👎 w czasie (UTC, {'tygodnie' if by == 'week' else 'dni'})", "",
             f"{label:<12} {'ocen':>6} {'👎':>6} {'% 👎':>8}"]
    for period, total, down in periods:
        lines.append(f"{period.isoformat():<12} {total:>6} {down:>6} {percent(down, total):>8}")
    all_total, all_down = sum(p[1] for p in periods), sum(p[2] for p in periods)
    lines += [f"{'razem':<12} {all_total:>6} {all_down:>6} {percent(all_down, all_total):>8}"]

    topics, other_words = summarize_topics(questions)
    lines += ["", f"Najczęstsze tematy pytań z 👎 (pytań z treścią: {len(questions)})", ""]
    if not questions:
        lines.append("Brak pytań z treścią (nie było 👎 albo upłynęła retencja).")
    for name, count in topics.most_common(top):
        lines.append(f"{count:>5}  {percent(count, len(questions)):>6}  {name}")
    if other_words:
        words = ", ".join(f"{w} ({n})" for w, n in other_words.most_common(top))
        lines += ["", f"Najczęstsze słowa w temacie „{OTHER}”: {words}"]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--by", choices=["day", "week"], default="week")
    ap.add_argument("--top", type=int, default=10, help="ile tematów pokazać")
    args = ap.parse_args()
    with psycopg.connect(get_settings().database_url) as conn:
        ensure_schema(conn)
        print(render(fetch_periods(conn, args.by), fetch_down_questions(conn), args.by, args.top))


if __name__ == "__main__":
    main()
