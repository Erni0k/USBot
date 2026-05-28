import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://chatbot:changeme@localhost:5432/chatbot"
)
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:8000/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "not-needed")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3")

EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-m3")
TOP_K = int(os.getenv("TOP_K", "5"))
ENABLE_THINKING = os.getenv("ENABLE_THINKING", "false").lower() == "true"

# Uwaga: jedyny placeholder w tym tekście to {context} (uzupełniany w runtime).
SYSTEM_PROMPT = """Jesteś asystentem Uniwersytetu Śląskiego w Katowicach.
Odpowiadaj WYŁĄCZNIE na podstawie poniższego kontekstu.
Jeśli w kontekście nie ma odpowiedzi, powiedz:
"Nie mam tej informacji - sprawdź na us.edu.pl lub skontaktuj się z dziekanatem."
Podawaj źródłowy adres URL, z którego pochodzi informacja.
Nie zmyślaj dat, nazwisk, godzin ani kwot. Odpowiadaj po polsku, zwięźle i uprzejmie.

KONTEKST:
{context}
"""
