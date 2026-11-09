"""Które strony pasują do pytania: aktualność treści i odbiorca. Czysta logika, bez LLM-a.

Bliskość wektorowa nie widzi ani daty, ani odbiorcy. Na pytania o bieżące zasady trafiały
więc komunikaty COVID i rekrutacje sprzed lat, a na pytania studentów strony dla kandydatów
(„Nie mam oceny” — wgrywanie dyplomu). Reguły działają na kandydatach przed wyborem
TOP_K, więc zwolnione miejsca zajmują kolejne fragmenty. Pomiary: docs/testy.md (6.10).
"""
import re
from collections.abc import Sequence
from datetime import date
from typing import Literal

from usbot.core.faculties import normalize
from usbot.core.models import Chunk

OutdatedFilter = Literal["off", "covid", "all"]

# Wszystkie wzorce działają na tekście po normalize(): małe litery, bez polskich znaków.
_COVID = re.compile(r"covid|koronawirus|sars-?cov|pandemi|epidemi")
_ASKS_ABOUT_PAST = re.compile(
    r"covid|koronawirus|pandemi|epidemi|\bkiedys\b|\bdawniej\b|\bhistori|\bw przeszlosci\b"
    r"|\bprzed laty\b|\bpoprzedni\w* (?:rok|lat)|\bubiegl\w* rok|\bzeszl\w* rok")
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")


def academic_year_start(today: date) -> int:
    return today.year if today.month >= 10 else today.year - 1


def asks_about_past(question: str, today: date) -> bool:
    """Pytanie o przeszłość: pandemia, „kiedyś”, „w zeszłym roku”, rok przed bieżącym akademickim."""
    norm = normalize(question)
    if _ASKS_ABOUT_PAST.search(norm):
        return True
    return any(int(year) < academic_year_start(today) for year in _YEAR.findall(norm))


def is_covid(chunk: Chunk) -> bool:
    return bool(_COVID.search(normalize(f"{chunk.url} {chunk.title or ''} {chunk.content}")))


def is_old_news(chunk: Chunk, today: date, days: int) -> bool:
    """Aktualność albo wydarzenie starsze niż `days` dni."""
    return (chunk.page_type in ("news", "event") and chunk.published_at is not None
            and (today - chunk.published_at.date()).days > days)


def without_outdated(chunks: Sequence[Chunk], question: str, mode: OutdatedFilter, today: date,
                     days: int) -> list[Chunk]:
    """Bez treści nieaktualnych, chyba że pytanie dotyczy przeszłości.

    `covid` pomija fragmenty o pandemii; `all` także stare aktualności i wydarzenia.
    """
    if mode == "off" or asks_about_past(question, today):
        return list(chunks)
    return [c for c in chunks
            if not is_covid(c) and not (mode == "all" and is_old_news(c, today, days))]


# Strony serwisu rekrutacji, które dotyczą też osób już studiujących
_FOR_STUDENTS_TOO = ("/akademiki", "/twoj-niezbednik-na-i-roku", "/oplaty-zwiazane-ze-studiami")
_RECRUITMENT_HOST = "https://rekrutacja.us.edu.pl"
_COURSE_CARD = "/karta-kierunku"
_CANDIDATE_URL = re.compile(
    r"/kandydat|kandydat-|dla-kandydatow|dni-otwarte|dzien-otwarty|po-maturze"
    r"|rekrutacj[ai]-na-(?:rok|studia)|rusza-rekrutacja|ruszyla-rekrutacja|wyniki-rekrutacji"
    r"|ture-rekrutacji|prowadzi-rekrutacje")
_CANDIDATE_QUESTION = re.compile(
    r"rekrutac|kandyd|matur|\birk\b|\bdosta\w* sie\b|\bprzyjm\w* na\b|\bprzyjec\w* na\b"
    r"|prog\w* punkt|kierunki|jaki kierunek|\boferuj|\bofert\w* studiow|dzien otwart|dni otwart"
    r"|podyplomow|\baplik|cudzoziem|obcokrajow|z zagranicy|\bforeign")
# Karta kierunku opisuje program studiów (np. wymiar praktyk), więc przydaje się też studentom
_COURSE_QUESTION = re.compile(r"kierunk|praktyk|specjalnos|program\w* stud")


def asks_about_recruitment(question: str) -> bool:
    return bool(_CANDIDATE_QUESTION.search(normalize(question)))


def for_candidates_only(chunk: Chunk, question: str) -> bool:
    """Czy strona jest przeznaczona dla kandydatów i nie przyda się przy tym pytaniu."""
    url = chunk.url.lower()
    if url.startswith(_RECRUITMENT_HOST):
        path = url.removeprefix(_RECRUITMENT_HOST)
        if path.startswith(_FOR_STUDENTS_TOO):
            return False
        return not (path.startswith(_COURSE_CARD) and _COURSE_QUESTION.search(normalize(question)))
    return bool(_CANDIDATE_URL.search(url))


def for_audience(chunks: Sequence[Chunk], question: str) -> list[Chunk]:
    """Strony dla kandydatów zostają tylko przy pytaniach o rekrutację."""
    if asks_about_recruitment(question):
        return list(chunks)
    return [c for c in chunks if not for_candidates_only(c, question)]
