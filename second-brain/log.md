# Operations Log

Append-only timeline of ingests, queries, and lint passes.
Entry prefix format: `## [YYYY-MM-DD] operation | description`
(`grep "^## \[" log.md | tail -5` shows the last 5 operations.)

## [2026-07-08] init | Vault created

Scaffolded from Karpathy's LLM Wiki pattern: schema (CLAUDE.md), empty wiki
with index, immutable raw/ sources directory, and this log.

## [2026-08-31] query | Kids' e-bike options for Adam (85 lb, 4'5"-4'6")

Web-verified specs for the Tuttio ARC-I, GOTRAX Fitz16, Macfox M16 and
Spacewalk M6. Filed [[wiki/kids-ebike-shortlist-for-adam]] and
[[wiki/kids-ebike-selection-criteria]]. Three corrections to earlier claims:
the ARC-I is throttle-only (pedals are a separate accessory), "Motor Goat KD"
could not be verified to exist, and the Spacewalk M6 weight is in conflict
across sources. Flagged that seat height, not weight, is the binding
constraint, and that throttle-only machines are not NC greenway-legal.

## [2026-08-31] query | Where to try kids' e-bikes around Davidson/Huntersville

Added a local-retail section to [[wiki/kids-ebike-shortlist-for-adam]]: four
Lake Norman-area bike shops for the 24" pedal option, plus Best Buy,
Evinmotors and Pedego as secondary stops. Key finding — three of the four
shortlisted bikes have no local dealer, so they cannot be test-ridden at all.
Second finding: REI publishes a 24-28" inseam spec for the REV 24, putting
Adam at the bottom of the 24"-wheel window.
