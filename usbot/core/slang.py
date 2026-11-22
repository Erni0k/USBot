"""Studencki slang w zapytaniu do wyszukiwarki: „pała”, „komis”, „dziekanka”. Bez LLM-a.

Model embeddingów nie łączy slangu z językiem urzędowym stron: „Dostałem pałę na poprawce,
mogę iść na komisa?” jest od strony o egzaminie komisyjnym w odległości 0,41, a ta sama treść
napisana urzędowo — 0,29 (docs/testy.md, 6.12). expand_slang() dopisuje w nawiasie urzędowy
odpowiednik po słowie ze slangu, tak jak expand_faculties() dopisuje nazwę wydziału. Zmienia
tylko zapytanie do wyszukiwarki; pytanie w prompcie LLM-a zostaje bez zmian.

Słownik obejmuje tylko słowa, które w języku urzędowym nie występują albo znaczą co innego
(„warun” tak, „warunek” nie; „komis” tak, „komisja” nie).
"""
import re

from usbot.core.faculties import normalize

# Wzorzec całego słowa po normalize() (małe litery, bez polskich znaków) → odpowiednik
_SLANG: list[tuple[re.Pattern, str]] = [(re.compile(p), term) for p, term in [
    (r"pal(?:a|e|y|ka|ke|ki|ce)|luf(?:a|e|y)", "ocena niedostateczna"),
    (r"komis(?:a|ie|u|em)?", "egzamin komisyjny"),
    (r"warun(?:a|ie|u|em)?", "warunkowe zaliczenie semestru"),
    (r"dziekank[aei]|dziekance|dziekanek", "urlop dziekański"),
    (r"legitk[aei]|legitce|legitek", "legitymacja studencka"),
    (r"wf(?:u|ie|em)?", "wychowanie fizyczne"),
    (r"zerowk(?:a|e|i)|zerowce", "termin zerowy egzaminu"),
    (r"kolos(?:a|ie|u|y|ow|em)?", "kolokwium"),
    (r"labk(?:i|ach|ow)|labork(?:i|ach|ow)", "laboratoria"),
    (r"cwik(?:i|ach|ow)|cwiczk(?:i|ach|ow)", "ćwiczenia"),
    (r"nieob(?:k[aei]|kow|ek)|nieobeck(?:a|e|i|ow)", "nieobecność"),
    (r"l4", "zwolnienie lekarskie"),
    (r"magisterk[aei]|magisterce", "praca magisterska"),
    (r"inzynierk(?:a|e|i|ce)", "praca inżynierska"),
    (r"dyplomowk(?:a|e|i|ce)", "praca dyplomowa"),
    (r"socjal(?:u|em|ie|a)?", "stypendium socjalne"),
    (r"egz", "egzamin"),
    (r"erazmus(?:a|ie|em)?", "Erasmus+"),
]]
_WORD = re.compile(r"\w+")


def expand_slang(text: str) -> str:
    """Dopisuje urzędowy odpowiednik po słowie ze slangu: „pałę” → „pałę (ocena niedostateczna)”.

    Każdy odpowiednik najwyżej raz w tekście; słowa spoza słownika bez zmian.
    """
    used: set[str] = set()

    def expand(m: re.Match) -> str:
        word = normalize(m.group())
        for pattern, term in _SLANG:
            if pattern.fullmatch(word):
                if term in used:
                    return m.group()
                used.add(term)
                return f"{m.group()} ({term})"
        return m.group()
    return _WORD.sub(expand, text)
