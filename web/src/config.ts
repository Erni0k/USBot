const raw = (import.meta.env.VITE_API_BASE_URL ?? "").trim();

/** Adres API bez końcowego „/”; pusty = ten sam origin (proxy Vite albo reverse proxy). */
export const API_BASE = raw.replace(/\/+$/, "");

/** Oceny odpowiedzi (POST /api/feedback); wyłącza je VITE_FEEDBACK_ENABLED=false. */
export const FEEDBACK_ENABLED = import.meta.env.VITE_FEEDBACK_ENABLED !== "false";

/** Limity API (docs/api.md, 3.1) — klient pilnuje ich, zanim wyśle żądanie. */
export const MAX_MESSAGE_CHARS = 2000;
export const HISTORY_TURNS = 5; // API_HISTORY_TURNS — starszych tur serwer i tak nie użyje
export const MAX_HISTORY_MESSAGES = 40;
export const MAX_HISTORY_CHARS = 40_000;
export const MAX_FEEDBACK_COMMENT_CHARS = 500; // API_MAX_FEEDBACK_COMMENT_CHARS

export const US_HOME = "https://us.edu.pl/";
