---
type: entity
created: 2026-08-28
updated: 2026-08-28
tags: [real-estate, platform, charlotte]
---

# MonitorCLT

Jeff's real-estate intelligence platform for the Charlotte, NC market: parcel
mapping, distress/opportunity signals, lead scoring, outreach, and a deal
pipeline. Node.js ESM + React 19/Vite + PostgreSQL/PostGIS + Redis, deployed
from `~/monitor-clt-deploy` on a Mac with `launchd` jobs.

Related: [[outstanding-items-2026-08-28]] · [[rp2]] · [[charlotte-reia-calendar]]

## It has no GitHub repository

**This is the root cause of several stuck sessions.** As of 2026-08-28 the
account's repos are `rp2`, `FinanceDatabase`, `neural_prophet`, `vix`, and one
clone — no MonitorCLT. Consequences:

- The "Document integration" session (08-07) built a social-gateway package
  (2.5K LOC, 41 tests) with nowhere to commit it. It was read as a
  *permissions* problem; it is an *existence* problem. Granting repo access
  cannot fix it.
- MonitorCLT work has been landing in [[rp2]] branches instead (PRs #2, #5).
- The `AUDIT.md` in rp2 PR #3 was requested for "monitorclt", found no such
  repo, and audited rp2 instead.

The source is mirrored in Google Drive (`monitor-clt-main/`, `MonitorCLT.jsx`,
deploy plists), so it can be read — but not committed to — from a session with
Drive access.

**Open decision:** create a real MonitorCLT repo, or keep using rp2 as the
host. Tracked as the "Progress to 100%" blocker.
