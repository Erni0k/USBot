import { useEffect, useId, useRef, useState } from "react";

type Theme = "system" | "light" | "dark";
const SCALES = [0.875, 1, 1.125, 1.25, 1.375, 1.5];
const THEMES: { value: Theme; label: string }[] = [
  { value: "system", label: "Jak w systemie" },
  { value: "light", label: "Jasny" },
  { value: "dark", label: "Ciemny" },
];

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
function write(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* brak dostępu do localStorage — ustawienie tylko na tę wizytę */
  }
}

/**
 * „Ustawienia” w nagłówku: przycisk rozwijający panel (wzorzec disclosure) z motywem
 * (systemowy / jasny / ciemny) i rozmiarem tekstu — oba zapamiętane w localStorage.
 * Esc albo kliknięcie obok zamyka panel; Esc oddaje fokus przyciskowi.
 */
export function Settings() {
  const [open, setOpen] = useState(false);
  const [theme, setTheme] = useState<Theme>(() => {
    const t = read("usbot.theme");
    return t === "light" || t === "dark" ? t : "system";
  });
  const [scale, setScale] = useState(() => {
    const s = Number(read("usbot.fontScale"));
    return SCALES.includes(s) ? s : 1;
  });
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const panelId = useId();

  useEffect(() => {
    const el = document.documentElement;
    if (theme === "system") delete el.dataset.theme;
    else el.dataset.theme = theme;
    write("usbot.theme", theme === "system" ? null : theme);
  }, [theme]);

  useEffect(() => {
    document.documentElement.style.fontSize = scale === 1 ? "" : `${scale * 100}%`;
    write("usbot.fontScale", scale === 1 ? null : String(scale));
  }, [scale]);

  useEffect(() => {
    if (!open) return;
    const onPointer = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault(); // nie zatrzymuj przy okazji generowania odpowiedzi
      setOpen(false);
      button.current?.focus();
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const idx = SCALES.indexOf(scale);
  return (
    <div
      className="settings"
      ref={root}
      onBlur={(e) => {
        // Tab poza panel zamyka go — nie zostaje otwarty nad rozmową
        if (open && !root.current?.contains(e.relatedTarget as Node | null) && e.relatedTarget) setOpen(false);
      }}
    >
      <button
        ref={button}
        type="button"
        className="button secondary"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((o) => !o)}
      >
        Ustawienia
        <svg className="chevron" viewBox="0 0 12 12" aria-hidden="true" focusable="false">
          <path d="M2 4l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.8" />
        </svg>
      </button>
      <div id={panelId} className="settings-panel" hidden={!open}>
        <fieldset>
          <legend>Motyw</legend>
          {THEMES.map((t) => (
            <label key={t.value} className="radio">
              <input
                type="radio"
                name={`${panelId}-theme`}
                value={t.value}
                checked={theme === t.value}
                onChange={() => setTheme(t.value)}
              />
              {t.label}
            </label>
          ))}
        </fieldset>
        <div role="group" aria-labelledby={`${panelId}-scale`}>
          <span id={`${panelId}-scale`} className="setting-label">
            Rozmiar tekstu
          </span>
          <div className="scale-row">
            <button
              type="button"
              className="button secondary"
              onClick={() => setScale(SCALES[idx - 1])}
              disabled={idx <= 0}
              aria-label="Zmniejsz tekst"
            >
              A−
            </button>
            <span className="scale" aria-live="polite">
              {Math.round(scale * 100)}%
            </span>
            <button
              type="button"
              className="button secondary"
              onClick={() => setScale(SCALES[idx + 1])}
              disabled={idx >= SCALES.length - 1}
              aria-label="Powiększ tekst"
            >
              A+
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
