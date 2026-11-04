"""Rdzeń aplikacji (logika czatu i rankingu).

Bez importów bazy, HTTP ani modeli — z otoczeniem rozmawia wyłącznie przez porty
z `usbot.ports`. Pakiet celowo nic nie importuje przy starcie, żeby `usbot.ports`
mógł używać `usbot.core.models` bez cyklicznych importów.
"""
