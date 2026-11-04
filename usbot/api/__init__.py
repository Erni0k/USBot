"""API HTTP (FastAPI) — adapter wejściowy na ChatService, tak jak cli.py.

Uruchomienie: `uvicorn usbot.api.main:app`. Endpointy i format zdarzeń SSE: docs/api.md.
Logika rozmowy jest w `usbot.core`; tu są tylko: walidacja żądań, zamiana zdarzeń na
Server-Sent Events, zasoby ładowane raz przy starcie (lifespan) i sprawdzenie stanu.
"""
