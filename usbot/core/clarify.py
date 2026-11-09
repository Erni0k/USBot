"""Dopytywanie o brakujący szczegół: przedmiot przy nieobecnościach i ocenach, system przy koncie.

Na takie pytania nie ma jednej odpowiedzi, a model podawał zasadę jednej grupy jako ogólną
(limit nieobecności z WF) albo odpowiadał o innym systemie. Reguły działają bez LLM-a i
przed wyszukiwaniem, tak jak dopytywanie o wydział w `usbot.core.faculties`. Pytanie, które
pyta o procedurę („Co zrobić, gdy…”) albo podaje szczegół, nie jest zatrzymywane.
Pomiar: docs/testy.md (6.10).
"""
import re
from collections.abc import Sequence
from typing import NamedTuple

from usbot.core.faculties import normalize
from usbot.core.models import Message
from usbot.core.prompts import HISTORY_TURNS, user_messages


class Clarification(NamedTuple):
    message: str
    options: list[dict[str, str]]


class _Rule(NamedTuple):
    topic: re.Pattern          # temat pytania (na tekście po normalize())
    detail: re.Pattern         # szczegół, który wystarcza do odpowiedzi
    question: str
    options: tuple[str, ...]


# Pytania o sposób postępowania mają odpowiedź niezależną od przedmiotu
_PROCEDURE = re.compile(r"\bco (?:zrobic|robic|mam|teraz)\b|\bjak\b|\bgdzie\b|\bdo kogo\b|\bkto\b|\bkiedy\b|\bczy\b")
_COURSE = (r"laborator|cwiczen|wyklad|lektorat|\bwf\b|\bw-?f\b|wychowani|seminari|konwersator|projekt"
           r"|przedmiot|\bz [a-z]{3,}|\bna [a-z]{5,}|\bkazd|\bwszystki")

_RULES = (
    _Rule(re.compile(r"nieobecn|opuscic|\bopuszcz"), re.compile(_COURSE),
          "Limit nieobecności zależy od przedmiotu i prowadzącego. O jakie zajęcia chodzi?",
          ("Wykład", "Ćwiczenia lub laboratorium", "Lektorat", "Wychowanie fizyczne")),
    _Rule(re.compile(r"\bnie mam ocen|\bbrak ocen|\bnie widze ocen|\bnie ma ocen|\bnie mam wpisu"),
          re.compile(_COURSE),
          "Z jakiego przedmiotu brakuje oceny i czy termin zaliczenia już minął?",
          ()),
    _Rule(re.compile(r"\bkont\w*\b.*\bnie dziala|\bnie dziala\b.*\bkont\w*\b|\bnie moge sie zalogowac|\bnie loguje"
                     r"|\bzablokowan\w* kont"),
          re.compile(r"usos|\bcas\b|poczt|mail|office|microsoft|365|teams|moodle|e-?learning|wi-?fi|eduroam"
                     r"|\bvpn\b|\birk\b|\bapd\b"),
          "Którego systemu dotyczy problem?",
          ("USOSweb", "Poczta uczelniana", "Microsoft 365", "Platforma e-learningowa", "Wi-Fi / eduroam")),
)


def needs_detail(history: Sequence[Message], turns: int = HISTORY_TURNS) -> Clarification | None:
    """Pytanie o dopowiedzenie, gdy ostatnie pytanie go wymaga, a w rozmowie nie ma szczegółu."""
    recent = [normalize(m) for m in user_messages(history)[-turns:]]
    if not recent or _PROCEDURE.search(recent[-1]):
        return None
    for rule in _RULES:
        if rule.topic.search(recent[-1]) and not any(rule.detail.search(m) for m in recent):
            return Clarification(rule.question, [{"code": o, "name": o} for o in rule.options])
    return None
