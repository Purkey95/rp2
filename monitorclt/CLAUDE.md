# MonitorCLT — working notes for agents

Stdlib-only Python, 3.8 compatible (no `match`, no `str.removeprefix`, no `dict | dict`,
no `list[str]` outside quoted annotations). SQLite is the reference store; every
feature must run and be tested with no services. Black/isort at line length 160.

Run everything from this directory:

    export PYTHONPATH=src MONITORCLT_DB=dev.db
    python3 -m unittest discover -s tests -p "test_mclt_*.py"
    python3 -m monitorclt run-daily --county MECKLENBURG --profile sample --fixtures fixtures/mecklenburg
    python3 -m monitorclt run-daily --county MECKLENBURG --profile live --fixtures fixtures/mecklenburg/live

Profiles are explicit everywhere: `sample` (synthetic) or `live` (real endpoints;
`--fixtures` replays captured pages). `serve` needs a token or proxy header.

Rules that are not up for negotiation in code changes:

- Source tables are append-only (`history.write`); never UPDATE a record payload.
- Anything person-level leaves only through `policy.check_export`; do not add a second
  path (API, webhook, digest, CSV all call it).
- A name-only match never auto-confirms; gates live in `resolve/gates.py`.
- No incarceration / probation / parole / arrest data: no table, field, feature, signal.
- Test names are `tests/test_mclt_*.py` (unique basenames: the parent repo's pytest run
  collects them alongside RP2's tests).
- Fixtures under `fixtures/<county>/` are the connector contract; change a parser, run
  `monitorclt contract --golden --write-golden` and review the golden diff.
- Never commit raw live captures: they go in `fixtures-private/` (git-ignored) and reach
  `fixtures/` only through `scripts/pseudonymize_fixtures.py`.

Where things live: `ARCHITECTURE.md` (why), `docs/RUNBOOK.md` (how to operate).
