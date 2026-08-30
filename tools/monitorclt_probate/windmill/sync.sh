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

# The rules become the Variable f/monitorclt/probate_match_rules rather than a
# file in the image, so a weight change is an audited act by a named user
# instead of a silent redeploy. Generated from match_rules.json here so the two
# cannot drift: json.dumps emits a JSON string, and JSON is valid YAML.
#
# is_secret is false on purpose. Windmill defaults new variables to secret, but
# these weights decide who ends up on a lead list -- they are policy, and policy
# that cannot be read cannot be reviewed. Nothing in the file is a credential.
python3 - "$TOOL/match_rules.json" "$DEST/probate_match_rules.variable.yaml" <<'PY'
import json
import sys

source, destination = sys.argv[1], sys.argv[2]
with open(source, encoding="utf-8") as f:
    rules = f.read()
json.loads(rules)  # refuse to push a variable the matcher could not parse
with open(destination, "w", encoding="utf-8") as f:
    f.write("value: {0}\n".format(json.dumps(rules)))
    f.write("is_secret: false\n")
    f.write('description: "match_rules.json -- scoring policy for the probate matcher"\n')
PY
echo "staged probate_match_rules.variable.yaml"

if [ "${1:-}" = "--stage-only" ]; then
    echo "staged only; not pushing"
    exit 0
fi

cd "$HERE"
wmill sync push
