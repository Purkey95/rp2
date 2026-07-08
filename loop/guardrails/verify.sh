#!/usr/bin/env bash
# The deterministic gate. Final vote on every piece of work. Exit 0 = ship-eligible.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"

PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || { echo "verify.sh: no .venv - run 'make' first" >&2; exit 1; }
export PATH="$REPO/.venv/bin:$PATH"   # ODS diff tests spawn rp2_us/rp2_jp as subprocesses

# Gate = the repo's own `make check` (full pytest). mypy is NOT in the gate:
# 140 pre-existing errors under current mypy (2024-era fork, unpinned dev
# deps). Re-add it here once fix-lint-debt clears the debt to zero.
"$PY" -m pytest --tb=native -q
