---
name: fix-flaky-test
description: Stabilize a test that fails intermittently
when: triage reports a CI failure that passed on re-run
---
Steps:
1. Reproduce: run the failing test 10 times (`pytest <test> -q` in a loop).
2. Find the nondeterminism (ordering, tmpdir, locale, time). Fix the CAUSE.
3. Re-run 10 times green.

Never:
- Never add retries, sleeps, or skip markers.
- Never delete or weaken the assertion.

Done when: 10 consecutive green runs AND verify.sh exits 0.
