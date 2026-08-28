---
type: entity
created: 2026-08-28
updated: 2026-08-28
tags: [repository, tax, python]
---

# rp2

`Purkey95/rp2` — a public fork of [eprbell/rp2](https://github.com/eprbell/rp2),
a privacy-focused open-source crypto tax calculator (Python CLI, Apache-2.0).
Computes capital gains and cost bases across multiple coins and exchanges and
emits Form 8949-format output via a plugin architecture.

Related: [[outstanding-items-2026-08-28]] · [[rp2-audit-verification]] · [[monitorclt]]

## Shape

- Countries as plugins (`us`, `jp`, `ie`, `es`, `generic`), each declaring a
  holding period and permitted accounting methods
- Report generators as plugins, writing ODS via `ezodf`
- Golden-file test discipline: 156 tests, ~2 min, most comparing generated
  spreadsheets byte-for-byte against checked-in expected output
- Strict typing (mypy), pylint, bandit, black, isort in CI

## Fork status

The fork is at v1.7.1 against upstream v1.7.2. Historically it carried **no
divergent code** — all commits were upstream contributions — which matters
when deciding where fixes belong: correctness fixes to shared tax logic
should go upstream rather than accumulating here.

Divergence now includes an AI second-brain vault (this directory, PR #4,
merged) and the correctness fixes in [[rp2-audit-verification]].

## Confusingly, it is also being used as a scratch repo

Several sessions pushed **MonitorCLT** work into branches of this repo because
MonitorCLT has no GitHub repo of its own — PR #2 (opportunity-detection
engine) and PR #5 (BEA API client) are real-estate tooling living in a crypto
tax calculator. The "Progress to 100%" session (08-12) is blocked precisely on
resolving this. See [[monitorclt]].

## Pre-existing CI state (2026-08-28)

Independent of any recent change, on `main`: pylint exits 4 (warnings in
`rp2_decimal.py`), black would reformat `accounting_engine.py` and
`tax_engine.py`, and isort flags 3 files. Useful baseline — a red check on a
PR is not necessarily that PR's fault.
