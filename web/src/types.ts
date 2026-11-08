// Kontrakt POST /api/chat — docs/api.md (3.1).

export interface Source {
  url: string;
  title: string;
  page_type: string;
  published_at: string | null;
  dist: number;
  rerank: number | null;
}

export interface FacultyOption {
  code: string;
  name: string;
}

export type ChatEvent =
  | { type: "sources"; sources: Source[] }
  | { type: "token"; text: string }
  | { type: "refused"; message: string }
  | { type: "clarify"; message: string; options: FacultyOption[] }
  | { type: "done"; answer: string }
  | { type: "error"; code: string; message: string };

export interface HistoryMessage {
  role: "user" | "assistant";
  content: string;
}

/** Stan jednej tury (pytanie + odpowiedź) w interfejsie. */
export type TurnStatus =
  | "searching" // wysłane, czekamy na `sources` / `refused` / `clarify`
  | "writing" // po `sources`, płyną tokeny
  | "done"
  | "refused"
  | "clarify"
  | "error"
  | "stopped"; // przerwane przyciskiem „Zatrzymaj”

export type Rating = "up" | "down";

export interface Turn {
  id: string;
  question: string;
  status: TurnStatus;
  sources: Source[];
  answer: string;
  clarify?: { message: string; options: FacultyOption[] };
  error?: { code: string; message: string; retryAfter?: number };
  rating?: Rating;
  /** Pytanie zadane w trakcie rozmowy (wysłane z historią) — samo może nie mieć sensu. */
  followup?: boolean;
}
