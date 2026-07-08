---
name: bump-deps
description: Propose (never apply) dependency updates
when: triage reports a security advisory or a pinned dep >1 major behind
---
Steps:
1. List outdated: `.venv/bin/pip list --outdated`.
2. Write the proposal (package, current, target, changelog link, risk) to
   loop/memory/STATE.md under "queued:".
3. STOP. CLAUDE.md law: never add or change a dependency unattended.

Done when: the proposal is in STATE.md. This skill never ships.
