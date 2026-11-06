#!/bin/sh
# Jeden przebieg ingestii; tylko jeden naraz (blokada na wolumenie /data/state, wspólna
# dla harmonogramu i ręcznego `docker compose run --rm ingest run.sh`).
#   run.sh           przyrostowo (strony bez zmian są pomijane)
#   run.sh refetch   CRAWL_REFETCH=true: pobiera wszystko od nowa, embeduje tylko zmienione
set -eu
LOCK=/data/state/ingest.lock
mode=${1:-incremental}
case "$mode" in
  incremental) export CRAWL_REFETCH=false ;;
  refetch)     export CRAWL_REFETCH=true ;;
  *) echo "użycie: run.sh [incremental|refetch]" >&2; exit 2 ;;
esac

exec 9>"$LOCK"
if ! flock -n 9; then
  echo "ingest: poprzedni przebieg jeszcze trwa — pomijam ($mode)"
  exit 0
fi
start=$(date +%s)
echo "ingest: start ($mode, CRAWL_LIMIT=${CRAWL_LIMIT:-domyślny})"
cd /app/ingestion
if python ingest.py; then
  echo "ingest: koniec ($mode) po $(( $(date +%s) - start )) s"
  date -Iseconds > /data/state/last-success
else
  code=$?
  echo "ingest: BŁĄD ($mode), kod $code, po $(( $(date +%s) - start )) s" >&2
  exit "$code"
fi
