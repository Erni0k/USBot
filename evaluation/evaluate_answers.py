"""Ewaluacja odpowiedzi bota: odmowy, poprawność źródła, wierność i fakty oczekiwane.

evaluate.py mierzy, czy właściwe strony trafiają do top-k. Ten skrypt sprawdza to, co
dostaje użytkownik: odpowiedź z prawdziwego ChatService (ta sama ścieżka co API).

Etapy są osobno, żeby sędziego dało się zmienić bez generowania odpowiedzi od nowa:

  python evaluation/evaluate_answers.py generate    pytanie, kontekst, odpowiedź, źródła → JSON
  python evaluation/evaluate_answers.py judge       LLM-sędzia ocenia wierność
  python evaluation/evaluate_answers.py report      metryki
  python evaluation/evaluate_answers.py sample      próbka 20 odpowiedzi BEZ werdyktów sędziego
  python evaluation/evaluate_answers.py calibrate   zgodność sędziego z oceną ręczną

Metoda i wyniki: docs/testy.md (6.7). Prompt sędziego: evaluation/judge_prompt.md.
"""
import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
import unicodedata
from collections import Counter
from datetime import date
from pathlib import Path

# UTF-8 na Windows (cp1252 nie obsługuje polskich znaków)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HERE = Path(__file__).resolve().parent
QUERIES = HERE / "queries.jsonl"
DEFAULT_RESULTS = HERE / "answers_results.json"
DEFAULT_CALIBRATION = HERE / "judge_calibration.jsonl"
JUDGE_PROMPT = HERE / "judge_prompt.md"
JUDGE_ATTEMPTS = 2


def norm_url(url: str) -> str:
    """Jak w evaluate.py: bez schematu, 'www.', końcowego '/' i fragmentu."""
    url = url.split("#", 1)[0].strip().lower()
    url = url.removeprefix("https://").removeprefix("http://").removeprefix("www.")
    return url.rstrip("/")


def load_queries(path: Path) -> list[dict]:
    """Pytania bez wpisów z ocen, które czekają na oznaczenie (pusty relevant ≠ spoza zakresu)."""
    queries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            q = json.loads(line)
            if not (q.get("source") == "feedback" and not q.get("relevant") and not q.get("out_of_scope")):
                queries.append(q)
    return queries


# --- odmowa ----------------------------------------------------------------------------

def plain(text: str) -> str:
    """Małe litery bez diakrytyków; „ł” nie rozkłada się w NFKD."""
    decomposed = unicodedata.normalize("NFKD", text.replace("ł", "l").replace("Ł", "l"))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


_REFUSAL = re.compile(
    r"nie mam (?:\w+ )?informacj"
    r"|brak (?:\w+ )?informacj"
    r"|kontekst\w* nie (?:zawiera|ma|podaje)|w kontekscie nie ma"
    r"|nie (?:moge|potrafie|jestem w stanie) (?:\w+ )?(?:pomoc|odpowiedziec|udzielic|podac|wykonac|spelnic|ujawnic|wypisac|tego)"
    r"|nie znalazlem|nie znaleziono"
    r"|\bi (?:do not|don't|cannot|can't|am unable)\b"
)
# Odmowa to krótka odpowiedź, która zaczyna się od braku informacji. „Nie mam godzin, ale
# dziekanat jest przy ul. X” zawiera twierdzenia, więc liczy się jako odpowiedź.
_REFUSAL_HEAD_CHARS = 160
_REFUSAL_MAX_CHARS = 400


def is_llm_refusal(answer: str) -> bool:
    text = plain(answer.strip())
    return len(text) <= _REFUSAL_MAX_CHARS and bool(_REFUSAL.search(text[:_REFUSAL_HEAD_CHARS]))


def classify(item: dict) -> str:
    """refused_threshold | refused_llm | clarify | answered | error."""
    if item["event"] == "refused":
        return "refused_threshold"
    if item["event"] in ("clarify", "error"):
        return item["event"]
    return "refused_llm" if is_llm_refusal(item["answer"]) else "answered"


# --- źródła w tekście odpowiedzi -------------------------------------------------------

_URL = re.compile(r"https?://[^\s<>()\[\]\"'`]+")


def cited_urls(answer: str) -> list[str]:
    """Adresy podane w tekście odpowiedzi, każdy raz, w kolejności, znormalizowane."""
    return list(dict.fromkeys(norm_url(u.rstrip(".,;:!?*_")) for u in _URL.findall(answer)))


def context_urls(item: dict) -> set[str]:
    """Strony, których fragmenty dostał LLM (adresów w samym kontekście nie ma)."""
    return {norm_url(s["url"]) for s in item["sources"]}


def source_score(item: dict) -> dict:
    """Czy adres podany w odpowiedzi należy do oznaczonych jako trafne (po URL-u)."""
    relevant = {norm_url(u) for u in item["relevant"]}
    cited = cited_urls(item["answer"])
    in_context = context_urls(item)
    good = [u for u in cited if u in relevant]
    return {"cited": cited, "n_cited": len(cited), "any_relevant": bool(good),
            "first_relevant": bool(cited) and cited[0] in relevant, "n_relevant": len(good),
            # Szukane też w treści kontekstu: adres bywa w tekście fragmentu
            "not_in_context": [u for u in cited if u not in in_context and u not in item["context"].lower()],
            "relevant_in_context": bool(relevant & in_context)}


# --- fakty oczekiwane ------------------------------------------------------------------

_TIME = r"\d{1,2}:\d{2}"


def normalize_fact_text(text: str) -> str:
    """Wspólna postać godzin, myślników i diakrytyków dla dopasowania faktów."""
    text = re.sub(r"[*`|]", " ", plain(text))
    text = re.sub(r"[‐-―−]", "-", text)
    # 9.00 / 09:00 → 9:00
    text = re.sub(r"(?<![\d.:])(\d{1,2})[.:](\d{2})(?![\d.:])", lambda m: f"{int(m[1])}:{m[2]}", text)
    # „od 10:00 do 14:00”, „10:00 do 14:00” → 10:00-14:00
    text = re.sub(rf"(?:\bod )?({_TIME})\s*(?:-|\bdo\b)\s*({_TIME})", r"\1-\2", text)
    # „10-14”, „od 10 do 14” → 10:00-14:00
    text = re.sub(r"(?:\bod )(\d{1,2}) do (\d{1,2})(?![\d:.])", r"\1:00-\2:00", text)
    text = re.sub(r"(?<![\d:.-])(\d{1,2})\s*-\s*(\d{1,2})(?![\d:.-])", r"\1:00-\2:00", text)
    return " ".join(text.split())


def fact_found(fact: str | list[str], answer: str) -> bool:
    """Fakt to tekst albo lista wariantów (wystarczy jeden), dopasowane jako całe słowa."""
    text = normalize_fact_text(answer)
    variants = [fact] if isinstance(fact, str) else fact
    return any(re.search(rf"(?<!\w){re.escape(normalize_fact_text(v))}(?!\w)", text) for v in variants)


def facts_score(item: dict) -> dict | None:
    facts = item.get("expected_facts")
    if not facts:
        return None
    # Odmowa to brak faktu, nawet jeśli komunikat przypadkiem zawiera szukane słowo
    answered = classify(item) == "answered"
    found = [answered and fact_found(f, item["answer"]) for f in facts]
    return {"found": sum(found), "total": len(facts), "all": all(found),
            "missing": [f for f, ok in zip(facts, found, strict=True) if not ok]}


# --- sędzia ----------------------------------------------------------------------------

def build_judge_prompt(template: str, item: dict, today: str) -> str:
    # Jeden przebieg: znacznik wpisany w pytanie albo odpowiedź nie zostanie rozwinięty
    values = {"TODAY": today, "QUESTION": item["question"], "CONTEXT": item["context"], "ANSWER": item["answer"]}
    return re.sub(r"\{\{(TODAY|QUESTION|CONTEXT|ANSWER)\}\}", lambda m: values[m[1]], template)


def parse_verdict(raw: str) -> dict:
    """Werdykt z odpowiedzi sędziego; ValueError, gdy to nie jest poprawny JSON z claims."""
    text = re.sub(r"^```(?:json)?|```$", "", raw.strip()).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"niepoprawny JSON: {exc.msg}") from exc
    claims = data.get("claims") if isinstance(data, dict) else None
    if not isinstance(claims, list):
        raise ValueError("brak listy claims")
    for claim in claims:
        if not isinstance(claim, dict) or not isinstance(claim.get("supported"), bool) or not claim.get("claim"):
            raise ValueError("niepoprawne twierdzenie")
    supported = sum(c["supported"] for c in claims)
    return {"claims": claims, "n_claims": len(claims), "n_supported": supported,
            # Bez twierdzeń nie ma czego weryfikować — to nie jest „wierna” odpowiedź
            "faithful": supported == len(claims) if claims else None}


class Judge:
    def __init__(self, base_url: str, api_key: str, model: str, reasoning_effort: str | None,
                 timeout: float = 300.0) -> None:
        from openai import OpenAI
        self.client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=1)
        self.model = model
        self.reasoning_effort = reasoning_effort

    def _call(self, prompt: str, seed: int):
        extra = {"reasoning_effort": self.reasoning_effort} if self.reasoning_effort else {}
        return self.client.chat.completions.create(
            model=self.model, messages=[{"role": "user", "content": prompt}], temperature=0,
            seed=seed, response_format={"type": "json_object"}, **extra)

    def __call__(self, prompt: str) -> dict:
        """Werdykt; po nieudanych próbach {"error": ...} — taka ocena nie liczy się jako wierna."""
        error = ""
        for attempt in range(JUDGE_ATTEMPTS):
            response = self._call(prompt, seed=attempt)
            try:
                verdict = parse_verdict(response.choices[0].message.content or "")
            except ValueError as exc:
                error = str(exc)
                continue
            verdict["prompt_tokens"] = response.usage.prompt_tokens if response.usage else None
            return verdict
        return {"error": error, "faithful": None}


# --- zapis -----------------------------------------------------------------------------

def save(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def load(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"Brak pliku {path} — najpierw uruchom etap generate")
    return json.loads(path.read_text(encoding="utf-8"))


def git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              check=True, cwd=HERE).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


# --- generate --------------------------------------------------------------------------

_CONTEXT = re.compile(r"<context>\n(.*)\n</context>\n\nPAMIĘTAJ", re.DOTALL)


def extract_context(system_prompt: str) -> str:
    """Kontekst dokładnie w tej postaci, w jakiej dostał go LLM."""
    match = _CONTEXT.search(system_prompt)
    if not match:
        raise ValueError("w prompcie systemowym nie ma kontekstu")
    return match[1]


class RecordingLLM:
    """Port ChatLLM zapamiętujący wiadomości, z którymi wywołano model."""

    def __init__(self, llm) -> None:
        self.llm = llm
        self.messages: list[dict] | None = None

    def stream(self, messages):
        self.messages = list(messages)
        yield from self.llm.stream(messages)


def ask(service, recorder: RecordingLLM, query: dict) -> dict:
    """Jedno pytanie przez ChatService, bez historii."""
    recorder.messages = None
    item = {"id": query["id"], "question": query["question"], "relevant": query.get("relevant", {}),
            "in_scope": bool(query.get("relevant")), "event": "answered", "answer": "", "context": "",
            "sources": []}
    if query.get("expected_facts"):
        item["expected_facts"] = query["expected_facts"]
    start = time.perf_counter()
    try:
        for event in service.stream(query["question"]):
            if event["type"] in ("refused", "clarify"):
                item["event"] = event["type"]
            elif event["type"] == "sources":
                item["sources"] = [{k: s[k] for k in ("url", "title", "dist", "rerank")} for s in event["sources"]]
            elif event["type"] == "done":
                item["answer"] = event["answer"]
    except Exception as exc:  # noqa: BLE001 — jedno pytanie nie może przerwać pomiaru
        item.update(event="error", error=f"{type(exc).__name__}: {exc}")
    item["seconds"] = round(time.perf_counter() - start, 2)
    if recorder.messages:
        item["context"] = extract_context(recorder.messages[0]["content"])
    return item


def cmd_generate(args) -> None:
    from usbot.bootstrap import build_chat_service
    from usbot.config import get_settings
    settings = get_settings()
    service = build_chat_service(settings)
    recorder = RecordingLLM(service.llm)
    service.llm = recorder

    queries = load_queries(args.queries)[:args.limit]
    data = {"meta": {"date": date.today().isoformat(), "git": git_commit(), "llm_model": settings.llm_model,
                     "retrieval_mode": settings.retrieval_mode, "rerank": settings.rerank,
                     "top_k": settings.top_k, "max_distance": settings.max_distance,
                     "min_rerank_score": settings.min_rerank_score, "max_per_url": settings.max_per_url},
            "items": []}
    for number, query in enumerate(queries, 1):
        item = ask(service, recorder, query)
        data["items"].append(item)
        save(args.file, data)  # po każdym pytaniu: awaria nie traci całego przebiegu
        print(f"[{number}/{len(queries)}] {query['id']}: {classify(item)} ({item['seconds']} s)", flush=True)
    print(f"Zapisano: {args.file}")


# --- judge -----------------------------------------------------------------------------

def cmd_judge(args) -> None:
    from usbot.config import get_settings
    settings = get_settings()
    data = load(args.file)
    model = args.judge_model or settings.llm_model
    effort = settings.llm_reasoning_effort if args.judge_reasoning is None else (args.judge_reasoning or None)
    judge = Judge(args.judge_base_url or settings.llm_base_url, settings.llm_api_key, model, effort)
    template = JUDGE_PROMPT.read_text(encoding="utf-8")
    data["meta"]["judge"] = {"model": model, "reasoning_effort": effort, "temperature": 0}
    todo = [i for i in data["items"] if classify(i) == "answered" and (args.force or "judge" not in i)]
    for number, item in enumerate(todo, 1):
        item["judge"] = judge(build_judge_prompt(template, item, data["meta"]["date"]))
        save(args.file, data)
        # Bez werdyktu: kalibracja wymaga oceny ręcznej bez znajomości wyników sędziego
        print(f"[{number}/{len(todo)}] {item['id']}", flush=True)
    print(f"Zapisano: {args.file}")


# --- report ----------------------------------------------------------------------------

def percent(part: int, total: int) -> str:
    return f"{part}/{total} ({100 * part / total:.1f}%)" if total else f"{part}/0 (—)"


def summarize(items: list[dict]) -> dict:
    """Metryki z zapisanych odpowiedzi — czyste funkcje, bez modelu i bazy."""
    for item in items:
        item["outcome"] = classify(item)
    scope = {name: [i for i in items if i["in_scope"] is flag] for name, flag in (("in", True), ("out", False))}
    refusals = {name: Counter(i["outcome"] for i in group) for name, group in scope.items()}

    answered = [i for i in scope["in"] if i["outcome"] == "answered"]
    scored = [(i, source_score(i)) for i in answered]
    reachable = [s for _, s in scored if s["relevant_in_context"]]
    all_cited = sum(s["n_cited"] for _, s in scored)

    judged = [i for i in items if i.get("judge") and "error" not in i["judge"]]
    with_claims = [i for i in judged if i["judge"]["faithful"] is not None]
    facts = [(i, facts_score(i)) for i in items if i.get("expected_facts")]
    return {
        "refusals": {k: dict(v) for k, v in refusals.items()},
        "n_in": len(scope["in"]), "n_out": len(scope["out"]),
        "sources": {
            "answered": len(answered),
            "with_citation": sum(s["n_cited"] > 0 for _, s in scored),
            "any_relevant": sum(s["any_relevant"] for _, s in scored),
            "first_relevant": sum(s["first_relevant"] for _, s in scored),
            "reachable": len(reachable),
            "reachable_any_relevant": sum(s["any_relevant"] for s in reachable),
            "urls": all_cited,
            "urls_relevant": sum(s["n_relevant"] for _, s in scored),
            "urls_not_in_context": sum(len(s["not_in_context"]) for _, s in scored),
        },
        "faithfulness": {
            "to_judge": sum(i["outcome"] == "answered" for i in items),
            "errors": sum(1 for i in items if i.get("judge") and "error" in i["judge"]),
            "no_claims": len(judged) - len(with_claims),
            "judged": len(with_claims),
            "faithful": sum(i["judge"]["faithful"] for i in with_claims),
            "in_scope_judged": sum(i["in_scope"] for i in with_claims),
            "in_scope_faithful": sum(i["judge"]["faithful"] for i in with_claims if i["in_scope"]),
            "claims": sum(i["judge"]["n_claims"] for i in with_claims),
            "claims_supported": sum(i["judge"]["n_supported"] for i in with_claims),
        },
        "facts": {
            "questions": len(facts), "all_found": sum(s["all"] for _, s in facts),
            "facts_found": sum(s["found"] for _, s in facts), "facts_total": sum(s["total"] for _, s in facts),
            "failed": [{"id": i["id"], "outcome": i["outcome"], "missing": s["missing"]}
                       for i, s in facts if not s["all"]],
        },
    }


def render(summary: dict) -> str:
    ref, src, faith, facts = (summary["refusals"], summary["sources"], summary["faithfulness"], summary["facts"])
    n_in, n_out = summary["n_in"], summary["n_out"]
    out_refused = ref["out"].get("refused_threshold", 0) + ref["out"].get("refused_llm", 0)
    in_refused = ref["in"].get("refused_threshold", 0) + ref["in"].get("refused_llm", 0)
    lines = [
        "ODMOWY",
        f"  spoza zakresu odrzucone:  {percent(out_refused, n_out)}  (próg: {ref['out'].get('refused_threshold', 0)}, "
        f"LLM: {ref['out'].get('refused_llm', 0)}, odpowiedział: {ref['out'].get('answered', 0)}, "
        f"dopytanie: {ref['out'].get('clarify', 0)}, błąd: {ref['out'].get('error', 0)})",
        f"  w zakresie odrzucone:     {percent(in_refused, n_in)}  (próg: {ref['in'].get('refused_threshold', 0)}, "
        f"LLM: {ref['in'].get('refused_llm', 0)}, odpowiedział: {ref['in'].get('answered', 0)}, "
        f"dopytanie: {ref['in'].get('clarify', 0)}, błąd: {ref['in'].get('error', 0)})",
        "",
        f"ŹRÓDŁA (pytania w zakresie z odpowiedzią: {src['answered']})",
        f"  podany choć jeden URL:          {percent(src['with_citation'], src['answered'])}",
        f"  choć jeden podany URL trafny:   {percent(src['any_relevant'], src['answered'])}",
        f"  pierwszy podany URL trafny:     {percent(src['first_relevant'], src['answered'])}",
        f"  gdy trafna strona była w kontekście: {percent(src['reachable_any_relevant'], src['reachable'])}",
        f"  podane URL-e trafne:            {percent(src['urls_relevant'], src['urls'])}",
        f"  podane URL-e spoza kontekstu:   {percent(src['urls_not_in_context'], src['urls'])}",
        "",
        "WIERNOŚĆ (LLM-sędzia)",
        f"  odpowiedzi do oceny: {faith['to_judge']}, błędy sędziego: {faith['errors']}, "
        f"bez twierdzeń: {faith['no_claims']}",
        f"  w pełni wierne:            {percent(faith['faithful'], faith['judged'])}",
        f"  w pełni wierne (w zakresie): {percent(faith['in_scope_faithful'], faith['in_scope_judged'])}",
        f"  twierdzenia potwierdzone:  {percent(faith['claims_supported'], faith['claims'])}",
        "",
        "FAKTY OCZEKIWANE",
        f"  pytania ze wszystkimi faktami: {percent(facts['all_found'], facts['questions'])}",
        f"  znalezione fakty:              {percent(facts['facts_found'], facts['facts_total'])}",
    ]
    lines += [f"  brak [{f['id']}] ({f['outcome']}): {f['missing']}" for f in facts["failed"]]
    return "\n".join(lines)


def cmd_report(args) -> None:
    data = load(args.file)
    summary = summarize(data["items"])
    print(render(summary))
    data["summary"] = summary
    save(args.file, data)


# --- kalibracja ------------------------------------------------------------------------

def select_sample(items: list[dict], n: int, seed: int) -> list[dict]:
    """Po połowie z odpowiedzi uznanych przez sędziego za wierne i niewierne.

    Losowa próbka miałaby mało niewiernych i wysoką zgodność także u sędziego, który
    zawsze mówi „wierna”. Kolejność jest tasowana, a plik nie zawiera werdyktów.
    """
    rng = random.Random(seed)
    judged = sorted((i for i in items if i.get("judge", {}).get("faithful") is not None), key=lambda i: i["id"])
    flagged = [i for i in judged if not i["judge"]["faithful"]]
    passed = [i for i in judged if i["judge"]["faithful"]]
    take_flagged = min(n // 2, len(flagged))
    take_passed = min(n - take_flagged, len(passed))
    sample = rng.sample(flagged, take_flagged) + rng.sample(passed, take_passed)
    rng.shuffle(sample)
    return sample


def cmd_sample(args) -> None:
    data = load(args.file)
    if args.calibration.exists() and not args.force:
        sys.exit(f"{args.calibration} już istnieje — ręczne oceny nie mogą zostać nadpisane (--force)")
    sample = select_sample(data["items"], args.n, args.seed)
    with args.calibration.open("w", encoding="utf-8", newline="\n") as f:
        for item in sample:
            f.write(json.dumps({"id": item["id"], "question": item["question"], "context": item["context"],
                                "answer": item["answer"], "manual_faithful": None, "manual_note": ""},
                               ensure_ascii=False) + "\n")
    print(f"Zapisano {len(sample)} odpowiedzi bez werdyktów: {args.calibration}")


def cohen_kappa(pairs: list[tuple[bool, bool]]) -> float | None:
    """Zgodność ponad przypadek; None, gdy oceny nie różnicują (mianownik 0)."""
    n = len(pairs)
    observed = sum(a == b for a, b in pairs) / n
    p_a, p_b = sum(a for a, _ in pairs) / n, sum(b for _, b in pairs) / n
    expected = p_a * p_b + (1 - p_a) * (1 - p_b)
    return (observed - expected) / (1 - expected) if expected < 1 else None


def calibration_summary(entries: list[dict], items: dict[str, dict]) -> dict:
    """Zgodność sędziego z oceną ręczną, także przeliczona na proporcje całego zbioru."""
    rows = []
    for entry in entries:
        if not isinstance(entry.get("manual_faithful"), bool):
            sys.exit(f"[{entry['id']}] brak oceny ręcznej (manual_faithful)")
        item = items[entry["id"]]
        if item["answer"] != entry["answer"]:
            sys.exit(f"[{entry['id']}] odpowiedź różni się od ocenianej — wygenerowano ją od nowa?")
        rows.append((entry["manual_faithful"], item["judge"]["faithful"], entry["id"]))
    clear = {e["id"] for e in entries if not e.get("manual_borderline")}

    def agreement_within(flag: bool) -> tuple[int, int]:
        group = [r for r in rows if r[1] is flag]
        return sum(r[0] == r[1] for r in group), len(group)

    judged = [i for i in items.values() if i.get("judge", {}).get("faithful") is not None]
    population = {flag: sum(i["judge"]["faithful"] is flag for i in judged) for flag in (True, False)}
    within = {flag: agreement_within(flag) for flag in (True, False)}
    weighted = None
    if all(within[f][1] for f in within):
        weighted = sum(population[f] * within[f][0] / within[f][1] for f in within) / sum(population.values())
    return {
        "n": len(rows),
        "agree": sum(r[0] == r[1] for r in rows),
        "n_clear": len(clear),  # bez przypadków oznaczonych przy ocenie ręcznej jako graniczne
        "agree_clear": sum(r[0] == r[1] for r in rows if r[2] in clear),
        "kappa": cohen_kappa([(r[0], r[1]) for r in rows]),
        "matrix": {"manual_faithful_judge_faithful": sum(r[0] and r[1] for r in rows),
                   "manual_faithful_judge_unfaithful": sum(r[0] and not r[1] for r in rows),
                   "manual_unfaithful_judge_faithful": sum(not r[0] and r[1] for r in rows),
                   "manual_unfaithful_judge_unfaithful": sum(not r[0] and not r[1] for r in rows)},
        "within_judge_faithful": within[True], "within_judge_unfaithful": within[False],
        "population": {"faithful": population[True], "unfaithful": population[False]},
        "weighted_agreement": weighted,
        "disagreements": [r[2] for r in rows if r[0] != r[1]],
    }


def cmd_calibrate(args) -> None:
    data = load(args.file)
    entries = [json.loads(line) for line in args.calibration.read_text(encoding="utf-8").splitlines() if line.strip()]
    summary = calibration_summary(entries, {i["id"]: i for i in data["items"]})
    m = summary["matrix"]
    print(f"Zgodność: {percent(summary['agree'], summary['n'])}, kappa Cohena: "
          f"{'—' if summary['kappa'] is None else format(summary['kappa'], '.2f')}")
    print(f"ręcznie wierna:    sędzia wierna {m['manual_faithful_judge_faithful']}, "
          f"niewierna {m['manual_faithful_judge_unfaithful']}")
    print(f"ręcznie niewierna: sędzia wierna {m['manual_unfaithful_judge_faithful']}, "
          f"niewierna {m['manual_unfaithful_judge_unfaithful']}")
    print(f"Wśród uznanych przez sędziego za wierne zgodnych: {percent(*summary['within_judge_faithful'])}")
    print(f"Wśród uznanych przez sędziego za niewierne zgodnych: {percent(*summary['within_judge_unfaithful'])}")
    if summary["weighted_agreement"] is not None:
        print(f"Zgodność przeliczona na proporcje całego zbioru: {100 * summary['weighted_agreement']:.1f}%")
    print(f"Zgodność bez przypadków granicznych: {percent(summary['agree_clear'], summary['n_clear'])}")
    print(f"Rozbieżności: {summary['disagreements']}")
    data["calibration"] = summary
    save(args.file, data)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", type=Path, default=DEFAULT_RESULTS, help="plik z odpowiedziami")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("generate", help="odpowiedzi przez ChatService")
    p.add_argument("--queries", type=Path, default=QUERIES)
    p.add_argument("--limit", type=int, default=None, help="tylko pierwsze N pytań")
    p.set_defaults(run=cmd_generate)

    p = sub.add_parser("judge", help="ocena wierności przez LLM-sędziego")
    p.add_argument("--judge-model", help="domyślnie LLM_MODEL")
    p.add_argument("--judge-base-url", help="domyślnie LLM_BASE_URL")
    p.add_argument("--judge-reasoning", help="reasoning_effort sędziego; domyślnie LLM_REASONING_EFFORT, '' = nie wysyłaj")
    p.add_argument("--force", action="store_true", help="oceń od nowa także już ocenione")
    p.set_defaults(run=cmd_judge)

    p = sub.add_parser("report", help="metryki")
    p.set_defaults(run=cmd_report)

    p = sub.add_parser("sample", help="próbka do oceny ręcznej, bez werdyktów")
    p.add_argument("--calibration", type=Path, default=DEFAULT_CALIBRATION)
    p.add_argument("--n", type=int, default=20)
    p.add_argument("--seed", type=int, default=20261108)
    p.add_argument("--force", action="store_true", help="nadpisz istniejący plik z ocenami")
    p.set_defaults(run=cmd_sample)

    p = sub.add_parser("calibrate", help="zgodność sędziego z oceną ręczną")
    p.add_argument("--calibration", type=Path, default=DEFAULT_CALIBRATION)
    p.set_defaults(run=cmd_calibrate)

    args = ap.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
