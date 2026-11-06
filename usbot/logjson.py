"""Logi w formacie JSON (jeden obiekt na linię) — `LOG_FORMAT=json` w kontenerach.

Docker zbiera stdout/stderr kontenera; JSON da się bez parsowania tekstu przefiltrować
(`docker compose logs api | jq 'select(.level=="ERROR")'`) albo wysłać do Loki/ELK.
Pola: `ts` (UTC, ISO 8601), `level`, `logger`, `msg`, przy wyjątku `exc`; dla dziennika
dostępu uvicorna także `method`, `path`, `status` (bez adresu IP klienta — RODO).
"""
import json
import logging
from datetime import UTC, datetime

_ACCESS = "uvicorn.access"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        args = record.args
        if record.name == _ACCESS and isinstance(args, tuple) and len(args) == 5:
            _client, method, path, _http, status = args
            out.update(method=method, path=path, status=status)
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False, default=str)


def use_json(logger_names: tuple[str, ...]) -> None:
    """Podmienia formatter we wszystkich handlerach podanych loggerów (i dodaje handler,
    jeśli logger go nie ma i nie przekazuje rekordów wyżej)."""
    formatter = JsonFormatter()
    for name in logger_names:
        logger = logging.getLogger(name)
        if not logger.handlers and not logger.propagate:
            logger.addHandler(logging.StreamHandler())
        for handler in logger.handlers:
            handler.setFormatter(formatter)
