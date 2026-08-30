#!/bin/sh
# Apply the probate migrations, in order, to any PostgreSQL -- hosted Supabase,
# self-hosted Supabase, or a bare database.
#
#   ./apply.sh "postgresql://postgres:pw@db.<ref>.supabase.co:5432/postgres"
#   ./apply.sh                       # uses $DATABASE_URL
#
# Each file runs in a single transaction (ON_ERROR_STOP + -1), so a migration
# either lands whole or not at all. They are not idempotent: run them once, on a
# fresh database. If you use the Supabase CLI instead, copy migrations/ into
# supabase/migrations/ -- the filenames already sort correctly.
set -eu

DB="${1:-${DATABASE_URL:-}}"
if [ -z "$DB" ]; then
    echo "usage: $0 <postgres-connection-url>   (or set DATABASE_URL)" >&2
    exit 2
fi

DIR="$(cd "$(dirname "$0")/migrations" && pwd)"
for f in "$DIR"/*.sql; do
    echo "==> $(basename "$f")"
    psql "$DB" --set ON_ERROR_STOP=1 -1 -q -f "$f"
done
echo "==> done"
