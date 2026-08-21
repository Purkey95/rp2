# The Loop: an agentic OS for this repo

Built from the "Agentic OS on Fable 5" pattern: a conductor (Fable 5) makes
every decision but writes almost no tokens, cheap models do the work, a
fresh-context verifier grades it, and `guardrails/verify.sh` (full pytest)
holds the final, deterministic vote. Autonomy is granted per skill by measured
pass rate, and everything ever finished keeps being re-verified daily as a
standing goal.

## Layout
```
CLAUDE.md                 constitution (laws, dispatch table, definitions)
loop/loop.sh              heartbeat: triage -> conduct -> execute -> verify -> gate
loop/contract.md          acts-alone / queues-for-me / wakes-me-up
loop/guardrails/verify.sh deterministic gate (full pytest via .venv)
loop/scripts/trust-log.sh per-skill trust ledger (watch -> queue -> auto)
loop/scripts/cost-check.sh, log-cost.sh   budget enforcement
loop/verify-goals.sh      re-verifies every goals/*.md predicate
loop/goals/               standing goals (one file per finished thing)
loop/skills/              one directory per recurring chore
loop/memory/              STATE.md, trust.tsv, goal-ledger.tsv, usage.log
```

## Prerequisites on the machine that runs the cron
- `claude` CLI with Fable 5 access, `jq`, `git`, `make`
- `llm` CLI + OpenRouter key (`llm install llm-openrouter`) — triage seat only
- `gh` CLI (optional: issue triage and PR creation degrade gracefully without it)
- `make` once, to build `.venv` (the gate refuses to run without it)

## Daily ops
```
make tick     # one heartbeat by hand (Week 1: do this daily, read everything)
make queue    # what is waiting for you
make trust    # per-skill pass rates and tiers
make goals    # re-verify all standing goals now
make audit    # 7-day spend by stage
make clean-worktrees
```

## Cron (start in Week 2, not before)
```
0 7 * * 1-5  cd <repo>/loop && ./loop.sh >> memory/cron.log 2>&1
30 7 * * *   cd <repo>/loop && ./verify-goals.sh >> memory/cron.log 2>&1
```

## Exit map for loop.sh
0 quiet/done · 1 iteration cap (read STATE.md) · 2 reroute or refusal
(never iterate on the swapped output; re-run tomorrow) · 3 budget breached
(`make audit`, fix the effort map, not the budget).

## Runbook
| Signal | Meaning | Action |
|---|---|---|
| exit 2 | router swap or refusal-shaped success | read STATE.md; re-run item tomorrow; if it recurs on one skill, audit that skill for reasoning-echo phrasing |
| exit 3 | daily spend hit the line | `make audit`; find which stage grew |
| ALERT demoted | established skill dropped below 90% | read its last 3 fails; usually the spec pattern, not the worker |
| goal VIOLATED | something finished stopped being true | `git log --since=<last-pass>` for suspects; fix goes through the pipeline; NEVER auto-fix tax-number goals |
| maker/checker standoff x2 | neither is presumed right | human decides |
| verify-goals timeout | predicate too expensive | that is a violation; cheapen the predicate |

## 30-day trust schedule
| Week | Level | You do | Graduate when |
|---|---|---|---|
| 1 | report | `make tick` by hand daily; read everything | 3 consecutive runs route exactly as you would have |
| 2 | draft | cron on; `make queue` with coffee | 2 skills cross 20 logged runs |
| 3 | ship | best skill goes unattended | 1 week, zero interventions |
| 4 | grow | approve 1 proposed skill; delete something | you removed something and nothing broke |

## Deviations from the article (deliberate)
1. **Worker seat is `claude -p`, not `llm`.** A text-only CLI cannot edit
   files; the implement seat needs an agentic tool. `llm` stays in the triage
   seat, where text-in/text-out is exactly right.
2. **Triage writes a per-tick file.** The article greps all of STATE.md for
   "status: actionable", so one actionable day would defeat the quiet gate
   forever after.
3. **Conductor runs at effort high, not xhigh.** The article's own law says
   never above high inside a loop; we obey the law, not the example.
4. **Refusal check added.** BUILD 0 mandates checking for refusal-shaped
   success responses; the article's loop.sh forgot to.
5. **Gate is full pytest** (this is Python, not npm). mypy has 140
   pre-existing errors under current tooling on this 2024-era fork, so it
   enters the gate only once fix-lint-debt clears the debt; pylint/bandit
   stay in `make static_analysis` for humans.

## Porting this to another project
Everything model-facing is generic. Only four things are repo-specific:
the CLAUDE.md NEVER paths, the verify.sh commands, the goals/ predicates,
and the skills/ roster. Copy `loop/` + `CLAUDE.md`, edit those four, run
the checks in order. For the MonitorCLT-specific port plan, see
`loop/PORT-monitorclt.md`.
