"""Metryki API w formacie tekstowym Prometheusa (`GET /api/metrics`), bez zależności.

Dane pochodzą z tych samych rekordów co dziennik rozmów (`ChatTrace.close()` →
`ChatLog.publish()`), więc liczą się także przy wyłączonym dzienniku. Liczniki żyją w
pamięci procesu: restart je zeruje (Prometheus to rozumie — `rate()` na licznikach), a przy
kilku procesach (`--workers`) każdy ma własne. Wybór i opis: docs/uruchomienie.md (4.5).

Endpoint nie jest publiczny: Caddy (`infra/Caddyfile`) odpowiada na `/api/metrics` 404.
"""
import statistics
import threading
import time
from collections import Counter, deque
from collections.abc import Callable, Iterable

OUTCOMES = ("ok", "refused", "clarify", "error", "cancelled")
# Czas do pierwszego tokenu: wyszukiwanie ~0.1 s + LLM (0.8 s bez rozumowania, 5–10 s z nim)
FIRST_TOKEN_BUCKETS = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0)
# Cała odpowiedź: 3–12 s typowo (docs/api.md, 3.2)
DURATION_BUCKETS = (0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0)
RECENT = 500  # z ilu ostatnich odpowiedzi liczyć medianę i p95 (dla człowieka)


class Histogram:
    def __init__(self, buckets: Iterable[float]) -> None:
        self.buckets = tuple(buckets)
        self.counts = [0] * len(self.buckets)
        self.count = 0
        self.sum = 0.0
        self.recent: deque[float] = deque(maxlen=RECENT)

    def observe(self, value: float) -> None:
        self.count += 1
        self.sum += value
        self.recent.append(value)
        for i, le in enumerate(self.buckets):
            if value <= le:
                self.counts[i] += 1

    def quantile(self, q: float) -> float | None:
        if not self.recent:
            return None
        if len(self.recent) == 1:
            return self.recent[0]
        return statistics.quantiles(self.recent, n=100, method="inclusive")[round(q * 100) - 1]


class Metrics:
    """Liczniki odpowiedzi czatu; `observe()` dostaje rekord dziennika rozmów."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self.started = clock()
        self.outcomes: Counter[str] = Counter()
        self.errors: Counter[str] = Counter()
        self.first_token = Histogram(FIRST_TOKEN_BUCKETS)
        self.duration = Histogram(DURATION_BUCKETS)
        self._lock = threading.Lock()

    def observe(self, record: dict) -> None:
        outcome = record.get("outcome") or "error"
        if record.get("cancelled"):  # dziennik: wynik do chwili przerwania + cancelled=True
            outcome = "cancelled"
        with self._lock:
            self.outcomes[outcome] += 1
            if outcome == "error":
                self.errors[record.get("error_code") or "unknown"] += 1
            if record.get("ms_first_token") is not None:
                self.first_token.observe(record["ms_first_token"] / 1000)
            if record.get("ms_total") is not None and outcome != "cancelled":
                self.duration.observe(record["ms_total"] / 1000)

    def refused_ratio(self) -> float:
        """Odsetek odmów wśród odpowiedzi, które się zakończyły (bez przerwanych i błędów)."""
        answered = self.outcomes["ok"] + self.outcomes["refused"] + self.outcomes["clarify"]
        return self.outcomes["refused"] / answered if answered else 0.0

    def render(self, *, active_streams: int = 0) -> str:
        with self._lock:
            lines: list[str] = []

            def metric(name: str, kind: str, help_: str, samples: list[tuple[str, float]]):
                lines.append(f"# HELP {name} {help_}")
                lines.append(f"# TYPE {name} {kind}")
                lines.extend(f"{name}{labels} {_num(v)}" for labels, v in samples)

            metric("usbot_chat_requests_total", "counter",
                   "Odpowiedzi POST /api/chat według wyniku.",
                   [(f'{{outcome="{o}"}}', self.outcomes[o]) for o in OUTCOMES])
            metric("usbot_chat_errors_total", "counter", "Zdarzenia error według kodu.",
                   [(f'{{code="{c}"}}', n) for c, n in sorted(self.errors.items())])
            metric("usbot_chat_refused_ratio", "gauge",
                   "Odsetek refused wśród zakończonych odpowiedzi od startu procesu.",
                   [("", self.refused_ratio())])
            for name, hist, help_ in (
                ("usbot_chat_first_token_seconds", self.first_token,
                 "Czas od żądania do pierwszego tokenu odpowiedzi LLM-a."),
                ("usbot_chat_duration_seconds", self.duration,
                 "Czas całej odpowiedzi (bez przerwanych przez klienta)."),
            ):
                samples = [(f'_bucket{{le="{_num(le)}"}}', n) for le, n in zip(hist.buckets, hist.counts)]
                samples += [('_bucket{le="+Inf"}', hist.count), ("_sum", hist.sum), ("_count", hist.count)]
                lines.append(f"# HELP {name} {help_}")
                lines.append(f"# TYPE {name} histogram")
                lines.extend(f"{name}{suffix} {_num(v)}" for suffix, v in samples)
                for q in (0.5, 0.95):
                    value = hist.quantile(q)
                    if value is not None:
                        lines.append(f'{name}_recent{{quantile="{q}"}} {_num(value)}')
            metric("usbot_chat_active_streams", "gauge", "Odpowiedzi generowane w tej chwili.",
                   [("", active_streams)])
            metric("usbot_uptime_seconds", "gauge", "Czas od startu procesu API.",
                   [("", self.clock() - self.started)])
        return "\n".join(lines) + "\n"


def _num(v: float) -> str:
    if isinstance(v, int) or float(v).is_integer():
        return str(int(v))
    return f"{v:.6g}"
