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
      <button type="button" className="button quiet" onClick={() => void copy()}>
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
