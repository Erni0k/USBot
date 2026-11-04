"""Prompt systemowy, kontekst dla LLM-a i zapytanie do wyszukiwarki z historii."""
from collections.abc import Sequence

from usbot.core.models import Chunk, Message

NO_INFO = "Nie mam tej informacji – sprawdź na us.edu.pl lub skontaktuj się z dziekanatem."

SYSTEM_PROMPT = """Jesteś asystentem Uniwersytetu Śląskiego w Katowicach.
Odpowiadaj WYŁĄCZNIE na podstawie poniższego kontekstu.
Jeśli w kontekście nie ma odpowiedzi, powiedz:
"Nie mam tej informacji – sprawdź na us.edu.pl lub skontaktuj się z dziekanatem."
Podawaj źródłowy adres URL, z którego pochodzi informacja.
Nie zmyślaj dat, nazwisk, godzin ani kwot. Odpowiadaj po polsku, zwięźle i uprzejmie.

DOPYTYWANIE O DZIEKANAT:
Każdy wydział ma własny dziekanat z innymi godzinami i kontaktem. Jeśli użytkownik
pyta o dziekanat (godziny otwarcia, kontakt, lokalizację) i NIE wskazał wyraźnie
którego wydziału dotyczy pytanie, NIE zgaduj i NIE podawaj godzin pierwszego
lepszego dziekanatu z kontekstu. Zamiast tego dopytaj, o który wydział chodzi, i
wymień dostępne wydziały:
- Wydział Humanistyczny (WH)
- Wydział Nauk Przyrodniczych (WNP)
- Wydział Nauk Społecznych (WNS)
- Wydział Nauk Ścisłych i Technicznych (WNŚiT)(WNST)
- Wydział Prawa i Administracji (WPiA)
- Wydział Sztuki i Nauk o Edukacji (WSNE)
- Wydział Teologiczny (WTL)
Dopiero gdy użytkownik wskaże wydział, podaj informacje o jego dziekanacie.

KONTEKST:
{context}
"""

# Ile ostatnich pytań użytkownika skleja build_query()
HISTORY_TURNS = 3


def build_context(chunks: Sequence[Chunk]) -> str:
    """Kontekst dla LLM-a: fragmenty z nagłówkiem `[Źródło: <url>]`, w kolejności wyników."""
    if not chunks:
        return "(brak pasujących informacji w bazie — uruchom najpierw ingestion/ingest.py)"
    return "\n\n".join(f"[Źródło: {c.url}]\n{c.content}" for c in chunks)


def build_system_prompt(chunks: Sequence[Chunk]) -> str:
    return SYSTEM_PROMPT.format(context=build_context(chunks))


def user_messages(history: Sequence[Message]) -> list[str]:
    return [m["content"] for m in history if m["role"] == "user"]


def build_query(history: Sequence[Message], turns: int = HISTORY_TURNS) -> str:
    """Buduje zapytanie do wyszukiwarki z kilku ostatnich pytań użytkownika.

    Krótkie follow-upy ("a wnst?", "dziekanatu tam") same w sobie nic nie znaczą
    dla wyszukiwarki wektorowej — doklejamy poprzednie pytania, by zachować temat.
    """
    return "\n".join(user_messages(history)[-turns:])


def search_queries(history: Sequence[Message], turns: int = HISTORY_TURNS) -> list[str]:
    """Zapytania do wyszukiwarki: z historii (build_query) i samo ostatnie pytanie.

    Historia ratuje follow-upy ("a wnst?" samo jest daleko od wszystkiego), ale po
    zmianie tematu wcześniejsze pytania psują zapytanie — po "przepis na pierogi"
    pytanie o urlop dziekański z historią byłoby odrzucone, choć samo pasuje dobrze.
    Gdy oba są takie same (pierwsze pytanie rozmowy), zostaje jedno.
    """
    last = user_messages(history)[-1]
    return list(dict.fromkeys([build_query(history, turns), last]))
