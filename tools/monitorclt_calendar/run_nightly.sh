#!/usr/bin/env bash
# MonitorCLT nightly calendar round trip. Cron entry (5:15am, host local time):
#
#   15 5 * * *  /path/to/tools/monitorclt_calendar/run_nightly.sh >> /var/log/monitorclt-calendar.log 2>&1
#
# Order matters: PULL first so a date you typed in yesterday is an explicit
# catalyst before the engine runs, then PUSH so the calendar reflects tonight's
# scoring. Both halves are idempotent, so a re-run after a failure is safe.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS="$(dirname "$HERE")"
OUT="${MONITORCLT_OUT:-$HERE/out}"
TODAY="${MONITORCLT_TODAY:-$(date +%F)}"
YEAR="${MONITORCLT_YEAR:-$(date +%Y)}"

# Signal inputs produced upstream by the sourcing run; override to match the host.
SIGNALS="${MONITORCLT_SIGNALS:-$OUT/signals.jsonl}"
PARCELS="${MONITORCLT_PARCELS:-$OUT/parcels.csv}"
COMPS="${MONITORCLT_COMPS:-$OUT/comps.jsonl}"
CATALYST_EVENTS="${MONITORCLT_CATALYST_EVENTS:-$OUT/catalyst_events.jsonl}"

mkdir -p "$OUT"
echo "=== MonitorCLT calendar sync $(date -Is) ==="

# 1. PULL — hand-entered dates become explicit catalyst rows.
python3 "$HERE/sync.py" pull --today "$TODAY" --out "$OUT/calendar_catalysts.jsonl"

# 2. Merge them with the catalyst events the sourcing run produced. Explicit
#    calendar dates and derived signals both feed catalyst.py, which prefers explicit.
: > "$OUT/catalyst_events.merged.jsonl"
if [ -f "$CATALYST_EVENTS" ]; then cat "$CATALYST_EVENTS" >> "$OUT/catalyst_events.merged.jsonl"; fi
cat "$OUT/calendar_catalysts.jsonl" >> "$OUT/catalyst_events.merged.jsonl"

# 3. Project the calendar (and, when the pipeline inputs are present, score/value
#    the leads so each event carries owner, score and offer band).
python3 "$TOOLS/monitorclt_catalyst/catalyst.py" \
  --events "$OUT/catalyst_events.merged.jsonl" --today "$TODAY" --outdir "$OUT"

OFFER_SHEET_ARG=()
if [ -f "$SIGNALS" ] && [ -f "$PARCELS" ]; then
  python3 "$TOOLS/monitorclt_pipeline/pipeline.py" \
    --signals "$SIGNALS" --parcels "$PARCELS" \
    ${COMPS:+--comps "$COMPS"} \
    --catalyst-events "$OUT/catalyst_events.merged.jsonl" \
    --today "$TODAY" --year "$YEAR" --outdir "$OUT"
  OFFER_SHEET_ARG=(--offer-sheet "$OUT/offer_sheet.jsonl")
else
  echo "note: $SIGNALS / $PARCELS not found — pushing catalysts without lead context"
fi

# 4. PUSH — reconcile the calendar against tonight's catalysts.
python3 "$HERE/sync.py" push --catalysts "$OUT/catalysts.jsonl" \
  ${OFFER_SHEET_ARG[@]+"${OFFER_SHEET_ARG[@]}"} --today "$TODAY"

echo "=== done $(date -Is) ==="
