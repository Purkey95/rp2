# Porting the agentic OS to monitor-clt

Design doc for applying this `loop/` scaffolding to `onestepsmith/monitor-clt`
(Express/Postgres/React, single-operator, PM2 + launchd on the Mac mini).
Written against the 2026-06-10 repo review and the 2026-07-07 ERPNext plan;
re-check what has merged before implementing. The reference implementation
lives in this repo (rp2 branch `claude/fable-5-agentic-os-h60062`); this doc
is the delta.

## Why it fits better than rp2
The June review's top findings are exactly what this system detects by
construction: 4 Firecrawl syncs silently dark, backups never restore-tested,
"monitoring without alerting — zero alert rules", no CI. Standing goals are
the missing alerting layer; the gate doubles as the missing CI.

## 1. Constitution (CLAUDE.md NEVER list)
- Never send anything — SMS, email, buyer blast. Drafts only, always.
  (Matches the existing approval doctrine: AI drafts, human gates.)
- Never touch compliance/DNC/opt-out/consent tables or the pre-send paths.
- Never run contacts_bulk operations or merges unattended.
- Never create or edit migrations/ unattended (77 migrations, drift-detected).
- Never change scraper user-agents, robots posture, or add sources —
  the ToS gray zone is a human decision.
- Never touch ecosystem.config.cjs, the Cloudflare tunnel, or .env.
- Never write to ERPNext except draft (unsubmitted) documents — Phase 2
  of the ERP plan, approval-gated in both systems.

## 2. Gate (guardrails/verify.sh)
`npm test` + `npx gitleaks protect --staged` + `node --check` over changed
files. Same script becomes the first `.github/workflows/ci.yml` — the
review's finding #3 (no CI) and the gate are one artifact.

## 3. Standing goals (the missing alert rules)
| goal | predicate sketch |
|---|---|
| no-dark-syncs | newest row per source (mecktimes, ruffbond, polaris, nc-sos, ...) < 24h old — would have caught the Firecrawl outage |
| backups-restore-tested | last successful pg_restore-to-throwaway log < 31 days old |
| auth-enforced | unauthenticated `curl` to /api returns 401 (AUTH_ENFORCED defaults off today — flip it first) |
| pm2-green | `pm2 jlist` shows every app online |
| no-default-grafana-creds | compose file no longer contains admin/monitorclt |
| compliance-gate-live | test send to a DNC fixture is blocked pre-send (once enforcement lands) |

`verify-goals.sh` runs unchanged; only goals/*.md differ.

## 4. Skills roster (= the review's backlog, trust-tiered)
merge-stale-branch (11 unmerged branches, W1), fix-dark-sync,
extract-oversized-component (CommandCenter 1.6k lines),
convert-fetch-to-react-query (219 raw fetches), triage-issues.
Each starts at watch tier; auto only after 20 logged passes at 95%.

## 5. Contract
- acts alone: docs, tests, lint, draft branches, STATE.md
- queues: migrations, anything in a send path, compliance, schema, ERP writes
- wakes: any goal VIOLATED (dark sync, stale backup, auth off), verify
  fails twice, budget breach

## 6. Triage inputs
Richer than rp2: `git log` + `pm2 jlist` + sync-freshness query + Prometheus
alerts endpoint. Same triage.md contract: findings only, "status: quiet"
otherwise.

## 7. Sequencing (respects W1)
Gate + goals first (they make branch-merging safer, not a 12th branch),
then the loop by hand for a week, then cron via launchd on the Mac mini.
The ERPNext bridge slots in after W1 as already planned; the loop's goal
`erp-bridge-fresh` (cache age < 24h) gets added when Phase 1 ships.
