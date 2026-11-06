// Test E2E i zrzuty ekranu czatu przez Chrome DevTools Protocol (bez zależności).
// Wymaga działającego API i `npm run dev`. Wysyła ~6 pytań do API (limit 10/min).
//   npm run e2e                         → zrzuty do ../docs/img/ui
//   node scripts/e2e.mjs <katalog> [scenariusz...]   np. clarify stop
// Zmienne: CHROME (ścieżka do Chrome/Edge), WEB_URL (domyślnie http://localhost:5173/).
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const OUT = process.argv[2] || fileURLToPath(new URL("../../docs/img/ui", import.meta.url));
const ONLY = process.argv.slice(3);
const URL_ = process.env.WEB_URL || "http://localhost:5173/";
const CHROME =
  process.env.CHROME ||
  (process.platform === "win32"
    ? "C:/Program Files/Google/Chrome/Application/chrome.exe"
    : process.platform === "darwin"
      ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
      : "google-chrome");
const PORT = 9333;
mkdirSync(OUT, { recursive: true });
const profile = join(tmpdir(), "usbot-e2e-profile-" + Date.now());

const chrome = spawn(CHROME, [
  "--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
  "--no-first-run", "--hide-scrollbars", "--lang=pl-PL", "about:blank",
], { stdio: "ignore" });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function getWs() {
  for (let i = 0; i < 50; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      const page = list.find((t) => t.type === "page");
      if (page) return page.webSocketDebuggerUrl;
    } catch {}
    await sleep(200);
  }
  throw new Error("brak Chrome");
}

const ws = new WebSocket(await getWs());
await new Promise((r) => ws.addEventListener("open", r));
let id = 0;
const pending = new Map();
const listeners = [];
ws.addEventListener("message", (m) => {
  const msg = JSON.parse(m.data);
  if (msg.id && pending.has(msg.id)) {
    const { res, rej } = pending.get(msg.id);
    pending.delete(msg.id);
    msg.error ? rej(new Error(JSON.stringify(msg.error))) : res(msg.result);
  } else if (msg.method) listeners.forEach((l) => l(msg));
});
const send = (method, params = {}) =>
  new Promise((res, rej) => {
    const i = ++id;
    pending.set(i, { res, rej });
    ws.send(JSON.stringify({ id: i, method, params }));
  });
const evaluate = async (expr) => {
  const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || "eval error");
  return r.result.value;
};
const waitFor = async (expr, timeout = 90000) => {
  const t0 = Date.now();
  while (Date.now() - t0 < timeout) {
    if (await evaluate(expr)) return;
    await sleep(250);
  }
  throw new Error("timeout: " + expr);
};
const viewport = (width, height, mobile = false) =>
  send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile });
const scheme = (value) => send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value }] });
const shot = async (name) => {
  await sleep(400);
  const { data } = await send("Page.captureScreenshot", { format: "png" });
  writeFileSync(join(OUT, name + ".png"), Buffer.from(data, "base64"));
  console.log("  zrzut:", name);
};
const key = async (k, code, keyCode, modifiers = 0) => {
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: k, code, windowsVirtualKeyCode: keyCode, modifiers });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: k, code, windowsVirtualKeyCode: keyCode, modifiers });
};
const ask = async (text) => {
  await evaluate(`document.getElementById('question').focus()`);
  await send("Input.insertText", { text });
  await key("Enter", "Enter", 13);
};
const status = () => evaluate(`[...document.querySelectorAll('.msg-bot')].at(-1)?.getAttribute('aria-busy')`);
const waitIdle = () => waitFor(`!document.querySelector('.button.stop')`);
const load = async () => {
  await send("Page.navigate", { url: URL_ });
  await waitFor(`!!document.getElementById('question')`);
  await sleep(600);
};
const newChat = async () => {
  await evaluate(`[...document.querySelectorAll('button')].find(b=>b.textContent==='Nowa rozmowa')?.click()`);
  await sleep(200);
};
const failures = [];
const check = (cond, msg) => {
  console.log((cond ? "  OK   " : "  FAIL ") + msg);
  if (!cond) failures.push(msg);
};

await send("Page.enable");
await send("Runtime.enable");
const consoleErrors = [];
listeners.push((m) => {
  if (m.method === "Runtime.exceptionThrown") consoleErrors.push(m.params.exceptionDetails.text);
  if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error")
    consoleErrors.push(m.params.args.map((a) => a.value).join(" "));
});

const scenarios = {
  async welcome() {
    await viewport(1280, 860);
    await scheme("light");
    await load();
    await shot("01-powitanie");
  },
  async keyboard() {
    await load();
    // Pierwszy Tab → link „Przejdź do pola pytania”
    await key("Tab", "Tab", 9);
    check((await evaluate(`document.activeElement.className`)) === "skip-link", "Tab 1: skip link");
    await key("Enter", "Enter", 13);
    await sleep(100);
    check((await evaluate(`document.activeElement.id`)) === "question", "Enter na skip link → pole pytania");
    // Shift+Enter dodaje nową linię, nie wysyła
    await send("Input.insertText", { text: "linia1" });
    await key("Enter", "Enter", 13, 8);
    check(!(await evaluate(`!!document.querySelector('.turn')`)), "Shift+Enter nie wysyła");
    await evaluate(`(()=>{const t=document.getElementById('question');const s=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;s.call(t,'');t.dispatchEvent(new Event('input',{bubbles:true}))})()`);
    const order = [];
    await evaluate(`document.body.focus(); document.activeElement.blur()`);
    for (let i = 0; i < 12; i++) {
      await key("Tab", "Tab", 9);
      order.push(await evaluate(`(e=>e.id||e.getAttribute('aria-label')||e.textContent.trim().slice(0,30))(document.activeElement)`));
    }
    console.log("  kolejność Tab:", order.join(" → "));
    check(order.includes("question"), "pole pytania osiągalne Tabem");
  },
  async answer() {
    await viewport(1280, 860);
    await scheme("light");
    await load();
    await newChat();
    await ask("Jak połączyć się z siecią eduroam?");
    await waitFor(`!!document.querySelector('.indicator')`, 5000).catch(() => {});
    check(await evaluate(`!!document.querySelector('.button.stop')`), "w trakcie: przycisk Zatrzymaj");
    await waitFor(`!!document.querySelector('.cursor')`, 60000);
    await sleep(1500);
    await shot("02-strumieniowanie");
    await waitIdle();
    check((await status()) === "false", "po done aria-busy=false");
    const n = await evaluate(`document.querySelectorAll('.source-card').length`);
    check(n > 0, `karty źródeł: ${n}`);
    check(await evaluate(`[...document.querySelectorAll('.source-card a')].every(a=>a.href.startsWith('http'))`), "źródła mają linki");
    check(await evaluate(`document.querySelectorAll('.feedback button[disabled]').length === 2`), "oceny wyłączone bez endpointu");
    const live = await evaluate(`document.querySelector('[aria-live=polite][aria-relevant]').children.length`);
    check(live > 2, `region aria-live: ${live} komunikatów (zdania)`);
    await evaluate(`document.querySelector('.feedback').scrollIntoView({block:'end'})`);
    await shot("03-zrodla-i-oceny");
    const stored = await evaluate(`JSON.parse(sessionStorage.getItem('usbot.conversation')||'[]').length`);
    check(stored === 1, "rozmowa w sessionStorage");
    await load();
    check(await evaluate(`document.querySelectorAll('.turn').length === 1`), "rozmowa przetrwała odświeżenie");
  },
  async clarify() {
    await viewport(1280, 860);
    await load();
    await newChat();
    await ask("Jakie są godziny otwarcia dziekanatu?");
    await waitIdle();
    const n = await evaluate(`document.querySelectorAll('.faculty-button').length`);
    check(n === 7, `clarify: ${n} przycisków wydziałów`);
    await shot("04-dopytanie-o-wydzial");
    await evaluate(`[...document.querySelectorAll('.faculty-button')].find(b=>b.textContent.includes('WNP')).click()`);
    await sleep(200);
    check((await evaluate(`[...document.querySelectorAll('.msg-user')].at(-1).textContent`)) === "WNP", "kliknięcie wysyła skrót WNP");
    await waitIdle();
    check(await evaluate(`[...document.querySelectorAll('.msg-bot')].at(-1).querySelectorAll('.source-card').length > 0`), "po WNP odpowiedź ze źródłami");
    await shot("05-odpowiedz-po-wyborze-wydzialu");
  },
  async refused() {
    await viewport(1280, 860);
    await load();
    await newChat();
    await ask("Podaj przepis na sernik z rodzynkami");
    await waitIdle();
    const refused = await evaluate(`!!document.querySelector('.refused')`);
    check(refused, "refused: komunikat");
    if (refused) {
      check(await evaluate(`document.querySelector('.refused a').href === 'https://us.edu.pl/'`), "refused: link do us.edu.pl");
      await shot("06-brak-informacji");
    }
  },
  async stop() {
    await viewport(1280, 860);
    await load();
    await newChat();
    await ask("Jak ubiegać się o stypendium socjalne? Opisz dokładnie wszystkie kroki.");
    await waitFor(`!!document.querySelector('.cursor') && document.querySelector('.answer').textContent.length > 120`, 60000);
    await key("Escape", "Escape", 27);
    await waitIdle();
    check(await evaluate(`!!document.querySelector('.note')`), "Esc: odpowiedź przerwana");
    await shot("07-przerwana-odpowiedz");
  },
  async ratelimit() {
    await viewport(1280, 860);
    await load();
    await newChat();
    await send("Fetch.enable", { patterns: [{ urlPattern: "*/api/chat", requestStage: "Request" }] });
    const handler = (m) => {
      if (m.method === "Fetch.requestPaused")
        send("Fetch.fulfillRequest", {
          requestId: m.params.requestId, responseCode: 429,
          responseHeaders: [{ name: "Content-Type", value: "application/json" }, { name: "Retry-After", value: "42" }],
          body: Buffer.from(JSON.stringify({ detail: "Rate limit exceeded: 10 per 1 minute" })).toString("base64"),
        });
    };
    listeners.push(handler);
    await ask("Jak zalogować się do USOSweb?");
    await waitIdle();
    check(await evaluate(`document.querySelector('.error')?.textContent.includes('42 s')`), "429: komunikat z Retry-After");
    const hint = await evaluate(`document.getElementById('composer-hint').textContent`);
    check(/za (4[0-2]) s/.test(hint), "429: licznik " + hint);
    check(await evaluate(`document.querySelector('.composer button[type=submit]').disabled`), "429: wysyłanie zablokowane");
    await shot("08-limit-zapytan");
    listeners.splice(listeners.indexOf(handler), 1);
    await send("Fetch.disable");
  },
  async dark() {
    await viewport(1280, 860);
    await scheme("dark");
    await load();
    await newChat();
    await ask("Jak zalogować się do USOSweb?");
    await waitIdle();
    await evaluate(`document.querySelector('.log').scrollTop = 0`);
    await shot("09-tryb-ciemny");
    await scheme("light");
  },
  async mobile() {
    await viewport(390, 844, true);
    await scheme("light");
    await load();
    await shot("10-telefon");
    await viewport(1280, 860);
  },
  async zoom() {
    await viewport(1280, 860);
    await load();
    await evaluate(`[...document.querySelectorAll('button')].find(b=>b.getAttribute('aria-label')==='Powiększ tekst').click()`);
    await evaluate(`[...document.querySelectorAll('button')].find(b=>b.getAttribute('aria-label')==='Powiększ tekst').click()`);
    await sleep(200);
    const fs = await evaluate(`getComputedStyle(document.documentElement).fontSize`);
    check(fs === "20px", `A+ ×2 → ${fs}`);
    check(await evaluate(`document.documentElement.scrollWidth <= window.innerWidth`), "bez poziomego przewijania przy 125%");
    await shot("11-wiekszy-tekst");
    await evaluate(`localStorage.removeItem('usbot.fontScale')`);
  },
};

try {
  for (const [name, fn] of Object.entries(scenarios)) {
    if (ONLY.length && !ONLY.includes(name)) continue;
    console.log("== " + name);
    try {
      await fn();
    } catch (e) {
      check(false, `${name}: ${e.message}`);
    }
  }
  check(consoleErrors.length === 0, "brak błędów w konsoli" + (consoleErrors.length ? ": " + consoleErrors.join(" | ") : ""));
} finally {
  ws.close();
  chrome.kill();
}
console.log(failures.length ? `\n${failures.length} FAIL` : "\nwszystko OK");
process.exit(failures.length ? 1 : 0);
