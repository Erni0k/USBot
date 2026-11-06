import { useEffect, useState } from "react";

type Theme = "system" | "light" | "dark";
const SCALES = [0.875, 1, 1.125, 1.25, 1.375, 1.5];

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

/** Motyw (systemowy / jasny / ciemny) i rozmiar tekstu — zapamiętane w localStorage. */
export function Settings() {
  const [theme, setTheme] = useState<Theme>(() => {
    const t = read("usbot.theme");
    return t === "light" || t === "dark" ? t : "system";
  });
  const [scale, setScale] = useState(() => {
    const s = Number(read("usbot.fontScale"));
    return SCALES.includes(s) ? s : 1;
  });

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") delete root.dataset.theme;
    else root.dataset.theme = theme;
    write("usbot.theme", theme === "system" ? null : theme);
  }, [theme]);

  useEffect(() => {
    document.documentElement.style.fontSize = scale === 1 ? "" : `${scale * 100}%`;
    write("usbot.fontScale", scale === 1 ? null : String(scale));
  }, [scale]);

  const idx = SCALES.indexOf(scale);
  return (
    <div className="settings">
      <div className="setting">
        <label htmlFor="theme">Motyw</label>
        <select id="theme" value={theme} onChange={(e) => setTheme(e.target.value as Theme)}>
          <option value="system">systemowy</option>
          <option value="light">jasny</option>
          <option value="dark">ciemny</option>
        </select>
      </div>
      <div className="setting" role="group" aria-label="Rozmiar tekstu">
        <span aria-hidden="true">Tekst</span>
        <button
          type="button"
          className="icon-button"
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
          className="icon-button"
          onClick={() => setScale(SCALES[idx + 1])}
          disabled={idx >= SCALES.length - 1}
          aria-label="Powiększ tekst"
        >
          A+
        </button>
      </div>
    </div>
  );
}
