# 5. Interfejs UI/UX

## 5.1 Opis interfejsu

USBot posiada interfejs tekstowy (CLI) uruchamiany w terminalu systemowym — dla osób
technicznych (administratorzy, zaawansowani studenci). Dla strony WWW gotowe jest API
HTTP (`usbot/api/`, [API](api.md) 3.1): przeglądarka wysyła pytanie z historią i dostaje
te same zdarzenia, które `cli.py` wypisuje w terminalu. Sam interfejs przeglądarkowy
(frontend) jest w przygotowaniu — niżej zasady, jak zdarzenia mapują się na widoki.

## 5.2 Opis nawigacji

| Akcja użytkownika | Zachowanie systemu |
|---|---|
| Wpisanie pytania + Enter | Bot buduje zapytanie z kontekstem, pobiera fragmenty z bazy, generuje odpowiedź (streaming). |
| Pusty Enter | System ignoruje wejście, wyświetla prompt ponownie. |
| `wyjdź` / `exit` / `quit` / `q` | Komunikat „Do widzenia!" i zamknięcie procesu. |
| `Ctrl+C` lub `Ctrl+D` (EOF) | Graceful exit z komunikatem „Do widzenia!". |
| Błąd bazy danych | Komunikat błędu z instrukcją uruchomienia Dockera, pętla kontynuowana. |
| Brak danych w bazie | Bot informuje o braku informacji, sugeruje us.edu.pl lub dziekanat. |
| Pytanie o dziekanat bez wydziału | Bot dopytuje o wydział i wymienia wszystkie (bez wywołania LLM-a); odpowiedź może być skrótem, np. „WNP”. |

## 5.3 Widoki i stany aplikacji

### Stan: uruchomienie

```text
Ładowanie modelu embeddingów (pierwsze uruchomienie może chwilę potrwać)...
============================================================
   Bot Uniwersytetu Śląskiego w Katowicach
   Wpisz 'wyjdź' lub Ctrl+C aby zakończyć
============================================================
```

### Stan: błąd bazy danych

```text
[Błąd bazy danych: connection refused]
[Upewnij się że Postgres działa i uruchom: docker compose up -d db]
```

### Stan: brak informacji

```text
Bot: Nie mam tej informacji - sprawdź na us.edu.pl lub skontaktuj się z dziekanatem.
```

### Stan: dopytanie o wydział

```text
Ty: Jakie są godziny otwarcia dziekanatu?

Bot: Każdy wydział ma własny dziekanat z innymi godzinami i kontaktem. O który wydział chodzi?
- Wydział Humanistyczny (WH)
- Wydział Nauk Przyrodniczych (WNP)
- Wydział Nauk Społecznych (WNS)
- Wydział Nauk Ścisłych i Technicznych (WNŚiT)
- Wydział Prawa i Administracji (WPiA)
- Wydział Sztuki i Nauk o Edukacji (WSNE)
- Wydział Teologiczny (WTL)

Ty: WNP
```

Po odpowiedzi bot szuka z pełną nazwą wydziału i odpowiada już przez LLM-a.

## 5.4 Strona WWW — zdarzenia API a widoki

`cli.py` tylko wypisuje zdarzenia z `ChatService.stream()`. Strona WWW dostaje te same
zdarzenia z `POST /api/chat` (Server-Sent Events) i może je pokazać tak:

| Zdarzenie | Widok w przeglądarce |
|---|---|
| (wysłanie pytania) | dymek pytania, wskaźnik „szukam…” (wyszukiwanie trwa ~0.1 s) |
| `sources` | lista źródeł (tytuł strony jako link) nad lub pod odpowiedzią; wskaźnik „piszę…” do pierwszego tokenu |
| `token` | dopisywanie tekstu do dymka odpowiedzi (Markdown — model używa nagłówków i list) |
| `done` | koniec odpowiedzi; `answer` trafia do historii w przeglądarce |
| `refused` | dymek ze stałym komunikatem „Nie mam tej informacji…”, bez źródeł |
| `clarify` | pytanie o wydział i przyciski z `options` (te same co `GET /api/faculties`); kliknięcie wysyła skrót, np. „WNP” |
| `error` | komunikat `message` z przyciskiem „Spróbuj ponownie”; w historii zostaje samo pytanie |

Przycisk „Zatrzymaj” przerywa `fetch` (`AbortController`) — serwer kończy wtedy
generowanie. Historię rozmowy trzyma przeglądarka (serwer jest bezstanowy) i przysyła
ją w każdym żądaniu; przed pierwszym pytaniem strona może sprawdzić `GET /api/health`
i pokazać komunikat o niedostępności zamiast pola pytania.

!!! note "Kodowanie znaków"
    Na Windows aplikacja jawnie ustawia `sys.stdout.reconfigure(encoding="utf-8")`,
    bo domyślne `cp1252` nie obsługuje polskich znaków. Dzięki temu interfejs działa
    poprawnie na każdym systemie.
