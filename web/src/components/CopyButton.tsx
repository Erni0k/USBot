import { useEffect, useState } from "react";

/** „Kopiuj odpowiedź” — zwykły tekst ze źródłami; wynik w komunikacie obok (role="status"). */
export function CopyButton({ text }: { text: () => string }) {
  const [state, setState] = useState<"" | "ok" | "fail">("");

  useEffect(() => {
    if (!state) return;
    const t = window.setTimeout(() => setState(""), 3000);
    return () => window.clearTimeout(t);
  }, [state]);

  const copy = async () => {
    const value = text();
    try {
      await navigator.clipboard.writeText(value);
      setState("ok");
    } catch {
      // Clipboard API wymaga HTTPS (albo localhost) i zgody przeglądarki — wtedy stary sposób
      setState(legacyCopy(value) ? "ok" : "fail");
    }
  };

  return (
    <div className="copy">
      <button type="button" className="button ghost" onClick={() => void copy()}>
        <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
          <rect x="5.5" y="5.5" width="8" height="8" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
          <path d="M10.5 3.5v-.5A1.5 1.5 0 0 0 9 1.5H4A1.5 1.5 0 0 0 2.5 3v5A1.5 1.5 0 0 0 4 9.5h.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
        </svg>
        Kopiuj odpowiedź
      </button>
      <span className="action-status" role="status">
        {state === "ok" ? "Skopiowano." : state === "fail" ? "Nie udało się skopiować. Zaznacz tekst i skopiuj ręcznie." : ""}
      </span>
    </div>
  );
}

function legacyCopy(value: string): boolean {
  const area = document.createElement("textarea");
  area.value = value;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.append(area);
  const focused = document.activeElement as HTMLElement | null;
  area.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  area.remove();
  focused?.focus();
  return ok;
}
