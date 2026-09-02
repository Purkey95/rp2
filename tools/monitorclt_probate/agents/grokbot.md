# Running this layer

Everything in this directory is markdown plus shell-invocable code, so it runs
on any agent platform that can read files and execute commands. Two mappings
follow: Grokbot, which is what prompted this design, and a plain
`claude` + `cron` fallback that needs no platform at all.

The specs are the source of truth. If a platform's model of the world disagrees
with [`AGENTS.md`](AGENTS.md), the spec wins and the platform is configured
around it.

## Grokbot

| This layer | Grokbot |
|---|---|
| A bot in [`bots.md`](bots.md) | A bot. Its **description** is the one-sentence job — that description is what other bots read to decide whether to delegate, so it earns the care |
| [`AGENTS.md`](AGENTS.md) | **Shared** memory, verbatim. Not per-bot memory |
| Per-bot context in `bots.md` | That bot's own memory |
| A file in [`skills/`](skills/) | A skill |
| A row in [`routines.md`](routines.md) | A routine — time-based, except the run, which is chained to intake |
| Postgres, county endpoints, mail | Plugins, connected per bot |

Three places the platform's defaults are wrong for this pipeline:

**Credentials are shared; they must not be.** Bots share one computer, so
authenticating a site once authenticates it for everyone. That is convenient and
unacceptable here — see the least-privilege rule in `AGENTS.md`. Where the
platform cannot scope a connection to one bot, do not connect it: have the
privileged step run as a command with credentials from the environment, and give
the bot the command rather than the credential. Counsel-draft in particular must
never be able to write to `probate.*`.

**Bot templates carry memories.** Exporting a bot to share it exports what it
knows. Nothing in this system may be exported until it has been checked for
decedent names, addresses and representative details.

**Delegation is not free.** Any bot can message any other. Clerk delegating to
Queue is the design; Queue asking Calibrator whether a pending row "looks like" a
match is the design failing. Bot descriptions should state what the bot will not
do, not only what it will.

### Standing it up

Follow the phases in [`README.md`](README.md). One bot per phase, each gated.
Phase 0 is Clerk plus shared memory and nothing else — no connections, no
routines. A bot with no plugins is a bot that cannot yet be wrong.

## Portable fallback: `claude` + `cron`

No platform required.

- **Bots** → `claude` sessions in this directory, or subagents. `AGENTS.md` and
  the relevant `bots.md` section go in the prompt.
- **Skills** → the files in `skills/`, referenced by path.
- **Routines** → `cron`, one entry per row of `routines.md`. The event-chained
  run is a shell `&&` after intake exits zero, which is what "on successful
  intake" means anyway.
- **Least privilege** → separate service accounts and separate environments per
  job, which is easier here than on a shared-computer platform.

Coverage of the two most important guards is identical: intake exiting non-zero
stops the chain, and no cron entry exists for outreach.

## What each is better at

Grokbot's advantage is reach — routines fire while nobody is at a desk, and the
review packet arrives on a phone. That matters for Queue and Scribe.

The fallback's advantage is control, which matters for Intake and Runner: the
work is deterministic, the credentials are sensitive, and a chained pipeline
that either exits zero or stops is more trustworthy than a conversational agent
deciding whether the pull looked complete.

Running both is reasonable, and is what the split above suggests: deterministic
adapters and the run under `cron`, human-facing packets and escalation on the
agent platform. The pipeline is the same either way; only the delivery differs.
