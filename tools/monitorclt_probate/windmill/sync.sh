#!/bin/sh
# Stage the shared modules into the Windmill workspace tree and push.
#
# crossref.py and load_run.py have exactly one home -- the tool directory one
# level up -- and are copied here at push time rather than kept in two places.
# The matcher a reviewer reads and the matcher the schedule runs must be the same
# file, or the evidence lists stop meaning anything.
#
#   ./sync.sh                       # stage, then wmill sync push
#   ./sync.sh --stage-only          # stage and stop (to inspect the tree)
#
# Requires the Windmill CLI: npm install -g windmill-cli, then `wmill workspace add`.
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
TOOL="$(cd "$HERE/.." && pwd)"
DEST="$HERE/f/monitorclt"

for module in crossref.py load_run.py; do
    cp "$TOOL/$module" "$DEST/$module"
    echo "staged $module"
done

# match_rules.json is deliberately NOT copied: in Windmill it lives as the
# Variable f/monitorclt/probate_match_rules, so a weight change is an audited act
# by a named user instead of a silent redeploy. Seed it once with:
#
#   wmill variable create f/monitorclt/probate_match_rules \
#       --value "$(cat "$TOOL/match_rules.json")"
#
# and update it the same way when calibration moves a weight.

if [ "${1:-}" = "--stage-only" ]; then
    echo "staged only; not pushing"
    exit 0
fi

cd "$HERE"
wmill sync push
