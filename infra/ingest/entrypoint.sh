#!/bin/sh
# Usługa `ingest`: harmonogram (supercronic) + pierwszy przebieg na pustej bazie.
# Harmonogram w czasie TZ (domyślnie Europe/Warsaw), składnia cron:
#   INGEST_SCHEDULE          przyrostowo, domyślnie codziennie o 3:00 poza niedzielą
#   INGEST_REFETCH_SCHEDULE  CRAWL_REFETCH=true, domyślnie w niedzielę o 3:00
#   INGEST_ON_EMPTY=true     pusta baza (świeże wdrożenie) → przebieg od razu przy starcie
set -eu
DAILY=${INGEST_SCHEDULE:-0 3 * * 1-6}
WEEKLY=${INGEST_REFETCH_SCHEDULE:-0 3 * * 0}
CRONTAB=/data/state/crontab

cat > "$CRONTAB" <<CRON
# wygenerowane przez entrypoint.sh przy starcie kontenera
$DAILY /app/infra/ingest/run.sh incremental
$WEEKLY /app/infra/ingest/run.sh refetch
CRON
echo "ingest: harmonogram (TZ=${TZ:-UTC}):"
sed '1d' "$CRONTAB"

if [ "${INGEST_ON_EMPTY:-true}" = "true" ]; then
  chunks=$(python - <<'PY'
import psycopg
from usbot.config import get_settings
try:
    with psycopg.connect(get_settings().database_url, connect_timeout=10) as conn:
        print(conn.execute("SELECT count(*) FROM chunks").fetchone()[0])
except Exception as exc:  # brak tabeli / bazy — potraktuj jak pustą, ingest zgłosi błąd
    print(0)
PY
)
  if [ "$chunks" = "0" ]; then
    echo "ingest: baza jest pusta — pierwszy przebieg teraz (pełny crawl trwa ok. 1,5 h)"
    /app/infra/ingest/run.sh incremental &
  else
    echo "ingest: w bazie $chunks fragmentów — czekam na harmonogram"
  fi
fi

# -passthrough-logs: wyjście zadań bez prefiksów supercronica (dalej w logach kontenera)
exec supercronic -passthrough-logs "$CRONTAB"
