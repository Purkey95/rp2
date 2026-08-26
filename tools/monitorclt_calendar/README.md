# MonitorCLT ⇄ Google Calendar

The catalyst engine already answers *"which parcels have a meaningful catalyst in
the next 60 days?"* — but the answer lives in `out/catalysts.jsonl`, and nobody
runs their Monday morning off a JSONL file. This module puts those dates on the
calendar you already look at, and reads the calendar back so dates you learn away
from the desk become real signals.

It is a **bridge, both directions**:

| | |
|---|---|
| **Push** | Upcoming catalysts → all-day calendar events, one per parcel-catalyst, colour-coded by imminence and carrying the owner, Seller Opportunity Score and offer band. |
| **Pull** | Events you typed yourself ("Rezoning hearing APN 12345678") → **explicit** catalyst rows for `catalyst.py`, which always prefers a real recorded date over a derived offset. |

That second half is the point. The catalyst engine's `derived_catalysts` rules are
approximations — *tax delinquency today → foreclosure eligibility ~730 days out*.
A date you hear at a REIA meeting or from a broker is a **hard** date, and typing
it into your phone's calendar is the lowest-friction way to capture it. It comes
back as `derived: false` and outranks the guess.

## Setup (about 10 minutes, once)

**1. Create the OAuth client.** In the Google Cloud Console
(`console.cloud.google.com`):

- Create (or pick) a project → **APIs & Services** → **Library** → enable
  **Google Calendar API**.
- **OAuth consent screen** → **External** → fill in the app name and your own
  email. Leave it in **Testing** and add your Google account under **Test users** —
  a testing app is not reviewed by Google and works indefinitely for its own test
  users. (Refresh tokens for an app still in Testing expire after 7 days; when you
  are done experimenting, hit **Publish app** — for a single-user app with only
  the `calendar.events` scope there is nothing to submit for review, and tokens
  then stop expiring.)
- **Credentials** → **Create credentials** → **OAuth client ID** → application
  type **Desktop app**. Download the JSON.

Scope requested: `calendar.events` only — list and edit events on the calendars
you name. Not your settings, not Gmail, not Drive.

> A **service account** would be the usual headless choice, and is deliberately
> not used: it has no access to a personal Google account's calendars without
> Workspace domain delegation, and Jeff's calendars are personal. An installed-app
> refresh token is the flow that actually works here — and it needs no RSA/JWT
> signing, which keeps this module pure stdlib like the rest of the engine.

**2. Make a calendar for it.** In Google Calendar, **Other calendars** → **+** →
**Create new calendar**, name it *MonitorCLT*. Keeping it separate means you can
toggle 200 catalysts off with one checkbox, share it with a partner, or clear it
entirely without touching your personal calendar.

**3. Authorize once.**

```bash
cp config.example.env .env          # .env is gitignored
python3 oauth.py --authorize --client-secrets ~/Downloads/client_secret_*.json
```

A browser opens, you grant access, and it prints a refresh token. Paste the three
`GOOGLE_*` values into `.env`. Then:

```bash
python3 sync.py calendars           # prints calendar ids
```

Copy the *MonitorCLT* id into `CALENDAR_ID`, and into `PULL_CALENDAR_IDS` list
every calendar where you might type a parcel date — the MonitorCLT one, your
personal one, Charlotte REIA, a partner's shared calendar:

```
CALENDAR_ID=abc123@group.calendar.google.com
PULL_CALENDAR_IDS=abc123@group.calendar.google.com,you@gmail.com,reia-id@import.calendar.google.com
```

Confirm it is all wired up, without writing anything:

```bash
python3 oauth.py --check
python3 sync.py push --catalysts ../monitorclt_catalyst/out/catalysts.jsonl \
                     --today "$(date +%F)" --dry-run
```

## Run

Sample data ships with the module, so the mapping can be inspected before any of
the setup above — this needs no credentials and touches no calendar:

```bash
python3 sync.py push --catalysts sample/catalysts.jsonl \
                     --offer-sheet sample/offer_sheet.jsonl --today 2026-08-26 --dry-run
```

```bash
# what would change (writes nothing)
python3 sync.py push --catalysts out/catalysts.jsonl --today 2026-08-26 --dry-run

# push, with owner/score/offer band from the pipeline on each event
python3 sync.py push --catalysts out/catalysts.jsonl \
                     --offer-sheet out/offer_sheet.jsonl --today 2026-08-26

# read hand-entered dates back out as explicit catalysts
python3 sync.py pull --today 2026-08-26 --out out/calendar_catalysts.jsonl
python3 ../monitorclt_pipeline/pipeline.py --catalyst-events out/calendar_catalysts.jsonl ...

python3 test_calendar.py
```

## Scheduled runs

`run_nightly.sh` does the whole round trip — pull, merge, project catalysts, run
the pipeline if its inputs are present, push — and is safe to re-run:

```cron
15 5 * * *  /path/to/tools/monitorclt_calendar/run_nightly.sh >> /var/log/monitorclt-calendar.log 2>&1
```

Pull runs **before** push so a date typed in yesterday is already an explicit
catalyst when tonight's scoring runs.

## Why it does not create duplicates

Every event we write carries `extendedProperties.private.key` — a stable hash of
`(apn, catalyst_type, date)` — and each sync lists only events bearing our marker,
then reconciles:

- **new catalyst** → insert
- **same catalyst, event drifted** (someone renamed it) → patch back
- **catalyst gone** (resolved, or the lead decayed out) → retire the *future* event
- **anything in the past** → never touched; it is history
- **events we did not create** → never touched, and never read back in as our own

A moved date is a new key on purpose: the old event retires and the new date
appears, which is what you want to see on a calendar.

## Refinements (next)

- Push a **daily agenda event** ("6 parcels ripen this week") as a single summary
  entry, for the days you do not want 30 individual events.
- Two-way status: mark an event *Accepted* to flip the lead's lifecycle state
  (pursued / dead) back into the score.
- Pull **showing and appointment** blocks so the pipeline knows when you are
  actually available before it ranks a door-knock list.
