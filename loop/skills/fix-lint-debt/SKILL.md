---
name: fix-lint-debt
description: Clear one pylint/mypy warning at a time in src/ or tests/
when: triage reports lint or type warnings on main
---
Steps:
1. Run `.venv/bin/pylint -r y src tests/*.py` and `.venv/bin/mypy src/ tests/`; pick ONE warning.
2. Fix the code, never the config. Behavior identical.
3. Run loop/guardrails/verify.sh.

Never:
- Never silence a warning with a disable comment or config change.
- Never touch tax_engine, balance, gain_loss, or accounting_method plugins.
- Never exceed 50 changed lines.

Done when: the chosen warning is gone AND verify.sh exits 0.
