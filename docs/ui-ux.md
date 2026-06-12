# 5. Interfejs UI/UX

## 5.1 Opis interfejsu

USBot posiada interfejs tekstowy (CLI) uruchamiany w terminalu systemowym. Nie
wymaga przeglądarki ani serwera webowego. Interfejs zaprojektowany jest z myślą o
osobach technicznych (administratorzy, zaawansowani studenci) oraz o przyszłej
migracji do interfejsu webowego (planowane rozszerzenie).

## 5.2 Opis nawigacji

| Akcja użytkownika | Zachowanie systemu |
|---|---|
| Wpisanie pytania + Enter | Bot buduje zapytanie z kontekstem, pobiera fragmenty z bazy, generuje odpowiedź (streaming). |
| Pusty Enter | System ignoruje wejście, wyświetla prompt ponownie. |
| `wyjdź` / `exit` / `quit` / `q` | Komunikat „Do widzenia!" i zamknięcie procesu. |
| `Ctrl+C` lub `Ctrl+D` (EOF) | Graceful exit z komunikatem „Do widzenia!". |
| Błąd bazy danych | Komunikat błędu z instrukcją uruchomienia Dockera, pętla kontynuowana. |
| Brak danych w bazie | Bot informuje o braku informacji, sugeruje us.edu.pl lub dziekanat. |

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

!!! note "Kodowanie znaków"
    Na Windows aplikacja jawnie ustawia `sys.stdout.reconfigure(encoding="utf-8")`,
    bo domyślne `cp1252` nie obsługuje polskich znaków. Dzięki temu interfejs działa
    poprawnie na każdym systemie.
