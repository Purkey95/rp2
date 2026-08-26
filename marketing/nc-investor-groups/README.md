# NC Investor Groups — posting campaign

Working files for a scheduled posting campaign across North Carolina real estate
Facebook groups, promoting Longhorn Investments lending.

> Note: this directory is unrelated to the RP2 crypto tax calculator that occupies the
> rest of this repository. It lives here because it needs a persistent home that the
> daily routine can read; move it to its own repo if that's cleaner.

## Files

| File | What it is |
|---|---|
| `nc_real_estate_groups.csv` | 60 NC real estate groups from the Facebook membership export, with URLs (50 resolved), join dates, and blank columns for each group's posting rules |
| `message_library.md` | 25 message variants across 7 types (Educational, Soft-intro, Deal-enabler, Direct offer, Engagement, Case study, Comment replies) |
| `posting_schedule.csv` | 6-week rotation, one group per weekday, 30 slots |
| `post_log.csv` | Append-only record of what was actually posted where. **This is the state file** — the daily routine reads it to avoid repeats. |

## Daily routine

A scheduled routine fires each weekday at 8:00am ET, reads `posting_schedule.csv` and
`post_log.csv`, works out which group is up and which variants are still unused this
week, and returns finished copy ready to paste.

Posting itself is manual and must stay that way. Meta deprecated the Groups API, and
automated posting violates the Platform Terms — the enforcement is account-level, which
would cost every group membership at once.

## Guardrails

- One group per day. Never two on the same day.
- Never reuse a message variant within the same week, in any group.
- No rates, points, terms, LTV/ARV figures, or turnaround times that Longhorn compliance
  has not approved in writing.
- Disclose the Longhorn relationship in every promotional post.
- Type D (direct offer) only in promo-day threads or vendor-friendly groups.
- Where a group bans vendor posts, use Type A or E only — or skip it.
- Log every post in `post_log.csv`, including the variant used.
