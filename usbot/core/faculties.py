"""Dopytywanie o wydział przy pytaniach o dziekanat.

Każdy wydział ma własny dziekanat z innymi godzinami i kontaktem. Gdy użytkownik
pyta o dziekanat, a w ostatnich pytaniach nie padł żaden wydział, ChatService nie
woła LLM-a, tylko zwraca zdarzenie `clarify` z listą wydziałów. Prompt systemowy
nadal zawiera tę samą instrukcję — na sformułowania, których reguła nie rozpozna.

Reguła patrzy tylko na pytania użytkownika (te same HISTORY_TURNS co build_query()),
po zamianie na małe litery i usunięciu polskich znaków („WNŚiT” → „wnsit”).

Po dopytaniu użytkownik często odpowiada samym skrótem („WNP”), a embedding słabo
rozumie skróty: zapytanie trafiało w dziekanat innego wydziału albo nie przechodziło
progu. Dlatego expand_faculties() dopisuje do zapytania pełną nazwę wydziału
(pomiar: docs/testy.md, 6.4).
"""
import re
import unicodedata
from collections.abc import Sequence
from typing import NamedTuple

from usbot.core.models import Message
from usbot.core.prompts import HISTORY_TURNS, user_messages


class Faculty(NamedTuple):
    code: str
    name: str
    pattern: re.Pattern  # na tekście po normalize()


def _p(*alternatives: str) -> re.Pattern:
    return re.compile("|".join(alternatives))


FACULTIES: tuple[Faculty, ...] = (
    Faculty("WH", "Wydział Humanistyczny", _p(r"\bwh\b", r"humanist")),
    Faculty("WNP", "Wydział Nauk Przyrodniczych", _p(r"\bwnp\b", r"przyrodnicz")),
    Faculty("WNS", "Wydział Nauk Społecznych", _p(r"\bwns\b", r"spoleczn")),
    Faculty("WNŚiT", "Wydział Nauk Ścisłych i Technicznych",
            _p(r"\bwnsit\b", r"\bwnst\b", r"scisl", r"techniczn")),
    Faculty("WPiA", "Wydział Prawa i Administracji",
            _p(r"\bwpia\b", r"\bprawa\b", r"\bprawo\b", r"administracj")),
    Faculty("WSNE", "Wydział Sztuki i Nauk o Edukacji", _p(r"\bwsne\b", r"sztuk", r"edukacj")),
    Faculty("WTL", "Wydział Teologiczny", _p(r"\bwtl\b", r"teolog")),
)

# „dziekanat”, „dziekanatu”, „dziekanaty”… — ale nie „dziekan” ani „urlop dziekański”
_DEANS_OFFICE = re.compile(r"\bdziekanat")

CLARIFY_MESSAGE = (
    "Każdy wydział ma własny dziekanat z innymi godzinami i kontaktem. "
    "O który wydział chodzi?\n"
    + "\n".join(f"- {f.name} ({f.code})" for f in FACULTIES)
)


def faculty_options() -> list[dict[str, str]]:
    """Wydziały jako `{"code", "name"}` — opcje zdarzenia `clarify` i GET /api/faculties."""
    return [{"code": f.code, "name": f.name} for f in FACULTIES]


def normalize(text: str) -> str:
    """Małe litery bez polskich znaków: „Ścisłych” → „scislych”."""
    text = text.lower().replace("ł", "l")
    return "".join(ch for ch in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(ch))


# Skróty (po normalize()) → wydział; WNST to nieoficjalny wariant WNŚiT
_ABBREVIATIONS: dict[str, Faculty] = {
    abbr: f
    for f in FACULTIES
    for abbr in {"WNŚiT": ("wnsit", "wnst")}.get(f.code, (f.code.lower(),))
}
_WORD = re.compile(r"\w+")


def mentioned_faculties(text: str) -> list[Faculty]:
    norm = normalize(text)
    return [f for f in FACULTIES if f.pattern.search(norm)]


def expand_faculties(text: str) -> str:
    """Dopisuje pełną nazwę przed skrótem wydziału: „WNP” → „Wydział Nauk Przyrodniczych (WNP)”.

    Działa na całych słowach, w dowolnej wielkości liter i z polskimi znakami lub bez
    („wnst”, „WNŚiT”). Skrót zostaje w nawiasie — wyszukiwanie pełnotekstowe nadal go
    widzi, a pełna nazwa przybliża zapytanie wektorowo do stron dziekanatu tego wydziału.
    """
    def expand(m: re.Match) -> str:
        f = _ABBREVIATIONS.get(normalize(m.group()))
        return f"{f.name} ({m.group()})" if f else m.group()
    return _WORD.sub(expand, text)


def needs_faculty(history: Sequence[Message], turns: int = HISTORY_TURNS) -> bool:
    """Ostatnie pytanie dotyczy dziekanatu, a w ostatnich `turns` pytaniach nie ma wydziału."""
    recent = user_messages(history)[-turns:]
    if not recent or not _DEANS_OFFICE.search(normalize(recent[-1])):
        return False
    return not any(mentioned_faculties(m) for m in recent)
