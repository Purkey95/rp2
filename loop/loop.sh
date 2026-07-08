#!/usr/bin/env bash
# The heartbeat. Exit map: 0 quiet/done, 1 iteration cap, 2 reroute, 3 budget.
set -euo pipefail
cd "$(dirname "$0")"
LOOPDIR="$(pwd)"

MAX_ITERS="${MAX_ITERS:-10}"
DAILY_BUDGET_USD="${DAILY_BUDGET_USD:-5}"
CHEAP="${CHEAP:-openrouter/deepseek/deepseek-v4-flash}"   # triage seat: text-only is fine
WORKER_MODEL="${WORKER_MODEL:-claude-sonnet-5}"           # worker seat: must edit files -> agentic CLI

for c in jq git claude llm; do
  command -v "$c" >/dev/null || { echo "loop.sh: missing prerequisite '$c'" >&2; exit 1; }
done

./scripts/cost-check.sh --budget "$DAILY_BUDGET_USD" || exit 3

for ((i=1; i<=MAX_ITERS; i++)); do
  # 1 TRIAGE: quiet-tick gate, ~$0.01. Fresh file per tick; STATE.md is the archive.
  TICK="memory/tick-$(date +%F).md"
  { git log --oneline -20; gh issue list --limit 20 2>/dev/null || true; \
    gh run list --limit 10 2>/dev/null || true; } \
    | llm -m "$CHEAP" -s "$(cat triage.md)" > "$TICK"
  ./scripts/log-cost.sh triage 0.01
  cat "$TICK" >> memory/STATE.md
  grep -q "status: actionable" "$TICK" || { echo quiet; exit 0; }

  # 2 CONDUCT: Fable, effort high (law: never above high in a loop), read-only, JSON out
  claude -p "$(cat conductor.md)
STATE: $(tail -100 memory/STATE.md)
TRUST: $(./scripts/trust-log.sh --render)
CONTRACT: $(cat contract.md)" \
    --model claude-fable-5 --allowedTools "Read" \
    --output-format json > /tmp/c.json
  ./scripts/log-cost.sh conductor 0.35

  # 2a REFUSAL / ERROR: refusals come back as success-shaped responses; check, don't assume
  SUBTYPE=$(jq -r '.subtype // "success"' /tmp/c.json)
  ISERR=$(jq -r '.is_error // false' /tmp/c.json)
  [[ "$SUBTYPE" != success || "$ISERR" == true ]] \
    && { echo "conductor non-success: $SUBTYPE" >> memory/STATE.md; exit 2; }

  # 2b ROUTE-TOLERANCE: never iterate on a model you didn't choose
  SERVED=$(jq -r '.modelUsage | keys[0] // "claude-fable-5"' /tmp/c.json)
  [[ "$SERVED" != *fable* ]] && { echo "rerouted: $SERVED" >> memory/STATE.md; exit 2; }

  jq -r '.result' /tmp/c.json | sed -n '/^{/,$p' > work-order.json
  SKILL=$(jq -r .skill work-order.json); ACTION=$(jq -r .action work-order.json)
  [[ "$ACTION" == stop  ]] && exit 0
  [[ "$ACTION" == queue ]] && { echo "queued: $SKILL" >> memory/STATE.md; continue; }

  # 3 EXECUTE: agentic worker in an isolated worktree (a text-only CLI cannot edit files)
  WT="../wt-$i"; git worktree add "$WT" -b "loop/$SKILL-$i" >/dev/null
  ( cd "$WT" && claude -p "$(cat "$LOOPDIR/workers/implement.md")
WORK ORDER: $(cat "$LOOPDIR/work-order.json")" \
      --model "$WORKER_MODEL" \
      --allowedTools "Read,Edit,Write,Glob,Grep,Bash(git diff:*),Bash(git log:*)" \
      > IMPLEMENTATION.md 2>&1 || true )
  ./scripts/log-cost.sh worker 0.10

  # 4 VERIFY: fresh Fable, no tools, sees only spec + diff
  V=$(claude -p "$(cat workers/verify.md)
SPEC: $(jq -r .spec work-order.json)
DONE_WHEN: $(jq -r '.done_when | join("; ")' work-order.json)
DIFF: $(cd "$WT" && git diff)" \
    --model claude-fable-5 --allowedTools "" \
    --output-format json | jq -r .result)
  ./scripts/log-cost.sh verifier 0.40

  # 5 GATE: deterministic; then ledger; ship only at auto tier
  if [[ "$V" == PASS* ]] && ( cd "$WT" && "$LOOPDIR/guardrails/verify.sh" ); then
    ./scripts/trust-log.sh "$SKILL" pass
    if [[ "$(./scripts/trust-log.sh --tier "$SKILL")" == auto ]]; then
      ( cd "$WT" && git add -A && git commit -qm "loop: $SKILL" \
        && { gh pr create --fill 2>/dev/null || true; } )
      echo "- shipped: $SKILL" >> memory/STATE.md
    else
      echo "- review: $SKILL in $WT" >> memory/STATE.md
    fi
  else
    ./scripts/trust-log.sh "$SKILL" fail
    echo "- FAILED: $SKILL in $WT ($V)" >> memory/STATE.md
  fi
  ./scripts/cost-check.sh --budget "$DAILY_BUDGET_USD" || exit 3
done
exit 1   # iteration cap without stop: check STATE.md
