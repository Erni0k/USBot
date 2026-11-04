"""Bot Uniwersytetu Śląskiego — aplikacja terminalowa.

Cienki adapter na `usbot.core.chat.ChatService`: czyta pytania z terminala, wypisuje
strumień zdarzeń i prowadzi historię rozmowy. Logika RAG: pakiet `usbot`
(docs/architektura.md).
"""
import sys

# UTF-8 na Windows (cp1252 nie obsługuje polskich znaków)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from collections.abc import Iterable  # noqa: E402

import psycopg  # noqa: E402

from usbot.bootstrap import build_chat_service  # noqa: E402
from usbot.config import get_settings  # noqa: E402
from usbot.core.chat import Event  # noqa: E402
from usbot.core.models import Message  # noqa: E402

EXIT_COMMANDS = ("wyjdź", "wyjdz", "exit", "quit", "q")


def print_reply(events: Iterable[Event]) -> str:
    """Wypisuje odpowiedź ze strumienia zdarzeń; zwraca tekst do historii (`done.answer`).

    Źródła (`sources`) nie są wypisywane osobno — LLM podaje URL-e w treści odpowiedzi.
    „Bot: ” pojawia się przed wywołaniem LLM-a (zdarzenie `sources`), tak jak wcześniej.
    """
    answer = ""
    streaming = False
    for event in events:
        if event["type"] in ("refused", "clarify"):
            print(f"\nBot: {event['message']}\n")
        elif event["type"] == "sources":
            print("\nBot: ", end="", flush=True)
            streaming = True
        elif event["type"] == "token":
            print(event["text"], end="", flush=True)
        elif event["type"] == "done":
            answer = event["answer"]
            if streaming:
                print("\n")
    return answer


def main() -> None:
    service = build_chat_service(get_settings())
    history: list[Message] = []

    print()
    print("=" * 60)
    print("   Bot Uniwersytetu Śląskiego w Katowicach")
    print("   Wpisz 'wyjdź' lub Ctrl+C aby zakończyć")
    print("=" * 60)
    print()

    while True:
        try:
            user_input = input("Ty: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nDo widzenia!")
            sys.exit(0)

        if not user_input:
            continue
        if user_input.lower() in EXIT_COMMANDS:
            print("Do widzenia!")
            sys.exit(0)

        question: Message = {"role": "user", "content": user_input}
        try:
            answer = print_reply(service.stream(user_input, history))
            history += [question, {"role": "assistant", "content": answer}]
        except psycopg.OperationalError as e:
            history.append(question)  # jak dotąd: pytanie zostaje w historii także po błędzie
            print(f"\n[Błąd bazy danych: {e}]")
            print("[Upewnij się że Postgres działa i uruchom: docker compose up -d db]\n")
        except Exception as e:
            history.append(question)
            print(f"\n[Błąd: {e}]\n")


if __name__ == "__main__":
    main()
