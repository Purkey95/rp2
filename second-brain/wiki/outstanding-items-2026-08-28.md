---
type: synthesis
created: 2026-08-28
updated: 2026-08-28
tags: [status, backlog, cross-session]
---

# Outstanding Items (as of 2026-08-28)

Roll-up of every unfinished thread across 72 Claude sessions, reconstructed
from each session's **title and last post-turn summary**. Session transcripts
were not readable, so this reflects what each session last recorded about
itself, not a re-derivation from the underlying work.

Related: [[rp2]] · [[monitorclt]] · [[rp2-audit-verification]]

## Blocked on a decision or credential from Jeff

These eight sessions were live (idle, not archived) and each ended asking for
something only the human can supply.

| Session | Date | Needs |
|---|---|---|
| Weekly backup script | 08-27 | Blended-AVM layout: move the block above Strategy, or add a one-line summary. Chrome also cannot reach the dev server. |
| Outstanding items | 08-26 | 22 structural decisions; marks on disagreements before 3a starts |
| Google Calendar + MonitorCLT | 08-26 | OAuth `client_secret` JSON, create the MonitorCLT calendar, run `oauth.py --authorize` |
| NC real estate groups | 08-26 | See [[charlotte-reia-calendar]] — the diagnosis changed |
| Henderson County NC land | 08-25 | Confirm whether to pull parcels from the county GIS portal |
| Red panda conversation review | 08-24 | The artifact text pasted in; the link is unreachable |
| Progress to 100% | 08-12 | Point at the real MonitorCLT repo, or leave the work in [[rp2]]? Gates PR #2. |
| Document integration into MonitorCLT | 08-07 | Nowhere to push — see [[monitorclt]] |

## Credential and machine items (older, never marked resolved)

Last words of archived sessions; may already be done.

- **AWS SES** (06-24): 4 Cloudflare CNAMEs (DNS-only), confirm the From
  address, SES SMTP credentials
- **Google Voice IMAP** (06-25): 2-Step Verification, app password, 4 env vars
- **Zombie schedulers** (07-09): confirm whether to kill 4 PIDs
- **Docker wedge** (07-29): whale-menu restart or reboot
- **DB migration** (07-10): hard-refresh `localhost:5173` and report
- **E-signature board** (07-09): ERPNext keys, Canva registration, SkySlope
  keys, LCOS decisions
- **Operator gates** (06-16): API keys, email DNS, court token, MLS feed
  (`HANDOFF_2026-06-16.md`, not in this vault)

## Open pull requests on [[rp2]]

Six open, none merged, no issues. As of 2026-08-28 PR #7 had **no checks
reported at all** on its head SHA, so earlier "CI green" notes are stale.

| PR | Subject | State |
|---|---|---|
| #7 | Mecklenburg adjacent-parcels map (17,121 parcels) | Open question: 7 MB CSV + 2.1 MB HTML in git, or LFS |
| #6 | SendGrid/Twilio notifications | Awaiting review |
| #5 | BEA API client | Awaiting review |
| #3 | Production-readiness audit (`AUDIT.md`) | Claims verified in [[rp2-audit-verification]] |
| #2 | MonitorCLT opportunity engine | Gated on the repo-convergence decision |
| #1 | worldmonitor practices adoption | Awaiting review |

## Referenced but not in this vault

Several sessions point at todo files that live on Jeff's Mac and were never
captured here: `todo_2026-08-17.md`, `HANDOFF_2026-06-16.md`,
`RECAP-TODO-2026-07-13.md`. The **MonitorCLT Build Register** artifact
(updated 08-27) is the most likely home of the "22 structural decisions" and
has not been reconciled against this list.
