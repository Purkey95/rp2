# Multi-model harness

Five vendors, one contract. Which model runs a task is a line of config; how that
model gets called is another. The default transport is the **CLI you already logged
into**, so a five-provider setup needs one API key instead of five.

```bash
python3 harness.py doctor          # what this machine can actually call
python3 harness.py routes          # what goes where, and what it sends
python3 harness.py run --task obituary_confirm --dry-run \
    --set decedent_name="John Q Public" --set county=MECKLENBURG \
    --set date_of_death=2026-01-14
```

`--dry-run` replays recorded fixtures: no keys, no CLIs, no network. That is also
how the tests run.

## Why CLI first

| Provider | CLI | Auth without an API key |
|---|---|---|
| Claude | `claude` (Claude Code) | **yes** — Pro/Max login |
| Gemini | `gemini` | **yes** — Google account / free tier |
| Grok | `grok` (Grok Build) | **yes** — SuperGrok / X Premium Plus |
| OpenAI | `codex` | **yes** — ChatGPT sign-in |
| Perplexity | `pplx` | **no** — wraps the Search API; needs `PERPLEXITY_API_KEY` |

The `http` transport is still there, in the same route entry, for what a CLI cannot
do: schema-enforced JSON, per-call usage accounting, and running somewhere nobody
can complete an interactive login. Flipping a task between the two is one edit —
compare `deed_extract` (CLI) with `deed_extract_api` (HTTP) in `routes.json`.

Know what CLI transport costs you before you commit to it: these binaries are
**coding agents**, not model endpoints — they carry their own system prompts and
tool loops, they start a process per call (~1–3s), most report no usage or cost, and
subscription plans are priced for interactive use, not for a script walking a
records table. The harness runs each one in a throwaway temp directory for that
reason. When volume shows up, move the hot route to `http` and leave the rest alone.

## Routing

`routes.json` has two halves, for the same reason `crossref.py` keeps its weights in
`match_rules.json`: vendors rename flags and reshape payloads far more often than
this code should change.

- **`providers`** — *how* to call something: `argv` or `url`, and the paths where the
  answer, the citations, the token usage and the cost sit in its output. Response
  paths are tried in order, and if none resolves, raw stdout becomes the text and the
  result is annotated `text_from_stdout` — an argv that drifts degrades to "whatever
  the CLI printed" instead of silently returning empty.
- **`tasks`** — *what* to send and to whom: a prompt template, a `sends` allowlist,
  and either one provider or a `fanout` list.

Providers ship marked `"verified": false`, meaning the argv came from vendor docs and
has not been run against an installed CLI here. `doctor` shows the flag; confirm a
route yourself and set it true. `claude` is verified.

## Nothing leaves that a task did not declare

Every task lists `sends` — the exhaustive set of input fields allowed to reach that
vendor. Fields outside it never enter the prompt context, so a template that reaches
for one fails loudly rather than leaking it, and the run log records what was
withheld.

```
$ python3 harness.py run --task obituary_confirm --prompt-only \
      --set decedent_name="John Q Public" --set county=MECKLENBURG \
      --set date_of_death=2026-01-14 --set ssn=123-45-6789
would send to: perplexity_search
withheld: ssn
------------------------------------------------------------------------
Obituary or death notice for John Q Public, died 2026-01-14,
MECKLENBURG County North Carolina. ...
```

`--prompt-only` renders exactly what would be sent and sends nothing. Use it before
pointing a new task at a vendor for the first time.

## Disagreement is the product

A `fanout` task puts identical evidence to three models and requires a quorum. Split
verdicts are **not** averaged into a number — averaging five opinions launders
disagreement into false confidence. They escalate to a human, which is the only
thing three vendors buy that one does not:

```
QUORUM 1 of 2
  votes: MATCH x1
  ESCALATE -- no quorum. A human decides this one.
```

A provider that failed does not get a vote, and a tie escalates. Verdict extraction
takes the **last** matching token in the response, because models restate the
question ("...whether this is a MATCH...") before answering it.

## The run log

`runs.jsonl` gets one line per run: timestamp, task, per-provider latency, usage,
cost, verdict, citation count, and notes. Prompts are **not** stored — a sha256 goes
in instead, so a run stays auditable without accumulating a second copy of the
records. `--log-prompts` overrides that when you are debugging. Any value read from
the environment is remembered and scrubbed from stored payloads and error strings; a
key that reaches the log is a key that has leaked.

## What this does not do

It does not touch the matcher. `crossref.py` decides matches deterministically from
evidence and [`evaluate.py`](../monitorclt_probate/README.md) measures those
decisions against hand labels; a model that could nudge a score would make both
unreproducible. Models run *beside* that pipeline — fetching a citation, reading a
scanned deed, helping a human clear the review queue — and whatever they produce
enters as evidence with a provenance label, which `evaluate.py`'s per-evidence
precision table can then hold to account like any other evidence.

Two things that stay true regardless of transport: the records here are public but
they are still about identifiable people, so `sends` is a decision to make per
vendor and not a formality; and nothing from the restricted-research carve-out in
the probate README belongs in any prompt.

## Fixtures

```bash
python3 harness.py run --task obituary_confirm --record ...   # save live output
python3 harness.py run --task obituary_confirm --dry-run ...  # replay it
python3 test_harness.py                                       # 43 tests, offline
```

The CLI transport is tested against `sys.executable` — a real subprocess with real
argv and a real exit status — so the suite needs no vendor logins. A test suite that
needs five logins is a test suite nobody runs.

Docs: [Claude Code](https://claude.com/claude-code) ·
[Perplexity](https://docs.perplexity.ai/) · [xAI](https://x.ai/)
