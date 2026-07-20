<!--- Copyright 2026 purkey95 --->

<!--- Licensed under the Apache License, Version 2.0 (the "License"); --->
<!--- you may not use this file except in compliance with the License. --->
<!--- You may obtain a copy of the License at --->

<!---     http://www.apache.org/licenses/LICENSE-2.0 --->

<!--- Unless required by applicable law or agreed to in writing, software --->
<!--- distributed under the License is distributed on an "AS IS" BASIS, --->
<!--- WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. --->
<!--- See the License for the specific language governing permissions and --->
<!--- limitations under the License. --->

# Evaluation: What RP2 Can Borrow from koala73/worldmonitor

This document maps the results of evaluating [worldmonitor](https://github.com/koala73/worldmonitor)
(a real-time global-intelligence dashboard) for anything reusable in RP2. It records what was
adopted, what was rejected, and why — so the decision doesn't have to be re-litigated later.

## Table of Contents
* **[Ground Rules](#ground-rules)**
* **[What Was Adopted](#what-was-adopted)**
* **[What Was Evaluated and Rejected](#what-was-evaluated-and-rejected)**
* **[Summary Map](#summary-map)**

## Ground Rules

Two constraints shaped this evaluation:

1. **License incompatibility (hard blocker for code).** worldmonitor is licensed
   **AGPL-3.0-only**; RP2 is **Apache-2.0**. Copying any worldmonitor source into RP2 would
   require relicensing RP2 under the AGPL. Therefore **no source code was copied** — only
   engineering *practices and ideas* were adopted, and everything in this branch was written
   from scratch against RP2's own codebase.
2. **Domain mismatch.** worldmonitor is a browser SPA + edge-function stack for live data
   feeds; RP2 is a privacy-focused, offline, CLI tax calculator. Almost none of worldmonitor's
   subsystems solve a problem RP2 has.

## What Was Adopted

Three practices from worldmonitor were reimplemented for RP2:

### 1. CI-verified documentation

worldmonitor keeps machine-checkable facts in its docs honest with a script that CI runs on
every push, so documentation can't silently drift from the code.

RP2 version: [`scripts/check_documentation.py`](../scripts/check_documentation.py) verifies that:
* every country plugin in `src/rp2/plugin/country/` has a matching `rp2_<country>` console
  entry point in `setup.cfg` and is documented in
  [`docs/supported_countries.md`](supported_countries.md);
* every accounting-method plugin in `src/rp2/plugin/accounting_method/` is documented in
  [`docs/supported_countries.md`](supported_countries.md);
* every report generator in `src/rp2/plugin/report/` (including country subpackages) is
  documented in [`docs/output_files.md`](output_files.md).

It runs in CI via the `documentation_check` workflow and locally via `make doc_check`.
It uses only the Python standard library, so it needs no virtual environment.

### 2. A root-level architecture document

worldmonitor's `ARCHITECTURE.md` gives every directory an owner and shows the data flow, which
makes onboarding (human or AI) much faster.

RP2 version: [`ARCHITECTURE.md`](../ARCHITECTURE.md) documents the input → parse → compute →
report pipeline, the module map, and the three plugin types (countries, accounting methods,
report generators).

### 3. An agent/contributor entry point

worldmonitor ships `AGENTS.md` describing how automated coding agents should build, test, and
review changes in the repo.

RP2 version: [`AGENTS.md`](../AGENTS.md) (with `CLAUDE.md` importing it) documents the Makefile
targets, the golden-file ODS test discipline, plugin conventions, and the review checklist.

## What Was Evaluated and Rejected

| worldmonitor capability | Why it was rejected for RP2 |
|---|---|
| Crypto price fetching (CoinGecko/CoinPaprika relay) | Fetches *current spot quotes* for a ticker widget only. RP2 needs *historical per-transaction* prices for cost basis — a different problem, already covered by the sibling project [DaLI](https://github.com/eprbell/dali-rp2). Also AGPL. |
| Web UI / panel system (vanilla TS, config-driven panels) | RP2 is deliberately CLI-only and offline; a GUI is a separate ecosystem project per the README. The code is AGPL and built for live-streaming dashboards. |
| Excel handling (exceljs) | Only *reads* xlsx in seed scripts, is JavaScript, and RP2's ODS generation is already served by `pyexcel-ezodf` templates. |
| Edge functions, Redis caching, rate limiting, CORS proxying | Server-side infrastructure for live feeds. RP2 has no server component and no network I/O by design (privacy focus). |
| Tauri desktop shell, PWA/service worker, in-browser ML | No corresponding surface in RP2. |
| Protobuf/sebuf RPC layer, multi-language SDKs | RP2 has no RPC API. The zero-dependency single-file SDK *style* is noted as a packaging reference if RP2 ever exposes a programmatic API package. |

## Summary Map

```
worldmonitor (AGPL-3.0, TypeScript dashboard)          RP2 (Apache-2.0, Python CLI)
------------------------------------------------       -------------------------------------
CI-verified doc stats  ── idea adopted ──────────────▶ scripts/check_documentation.py
ARCHITECTURE.md w/ ownership rule ── idea adopted ───▶ ARCHITECTURE.md
AGENTS.md agent entry point ── idea adopted ─────────▶ AGENTS.md + CLAUDE.md
spot-price ticker, web UI, edge/caching stack,
desktop shell, protobuf RPC, SDKs ── rejected ───────▶ (out of scope / AGPL / covered by DaLI)
source code (any) ── blocked by AGPL ────────────────▶ nothing copied
```
