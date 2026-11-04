"""USBot — chatbot RAG Uniwersytetu Śląskiego.

Warstwy (opis: docs/architektura.md):
- `usbot.core`     — logika czatu i rankingu, bez bazy, HTTP i modeli;
- `usbot.ports`    — interfejsy (Protocol), przez które rdzeń korzysta z otoczenia;
- `usbot.adapters` — implementacje portów: bge-m3, cross-encoder, pgvector, API OpenAI;
- `usbot.bootstrap`— składanie adapterów według ustawień (`usbot.config`).
"""
