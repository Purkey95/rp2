"""Pin the Google Calendar bridge. Run: python3 test_calendar.py

Everything here is offline: a fake transport stands in for Google, so the mapping,
the idempotent reconcile, pagination, token refresh, and rate-limit backoff are all
exercised without credentials or network.
"""

import json
import os
from datetime import datetime

import events as events_mod
from gcal import Calendar, CalendarError
from oauth import Credentials
from sync import context_by_apn, load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
RULES = json.load(open(os.path.join(HERE, "calendar_rules.json"), encoding="utf-8"))
TODAY = datetime(2026, 8, 26)


def check(label, got, want):
    ok = got == want
    print(f"[{'ok ' if ok else 'FAIL'}] {label}: {got!r}")
    return ok


class FakeTransport:
    """Scripted (status, text) responses; records every call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, body=None, headers=None):
        self.calls.append((method, url, body, headers))
        return self.responses.pop(0) if self.responses else (200, "{}")


def creds_with(transport):
    return Credentials("cid", "csecret", "rtoken", transport=transport, clock=lambda: 1000.0)


# catalyst.score_parcels()-shaped input
CATALYSTS = [
    {"apn": "12345678", "catalyst_horizon": 44.0, "next_catalyst": "rezoning_hearing",
     "next_in_days": 20, "catalysts": [
         {"apn": "12345678", "catalyst_type": "rezoning_hearing", "date": "2026-09-15",
          "days_until": 20, "source_url": "https://example.com/docket", "derived": False, "note": ""},
         {"apn": "12345678", "catalyst_type": "loan_maturity", "date": "2026-11-01",
          "days_until": 67, "source_url": "rod-bk-123", "derived": False, "note": ""},
     ]},
    {"apn": "99999999", "catalyst_horizon": 9.0, "next_catalyst": "tax_foreclosure_eligibility",
     "next_in_days": 300, "catalysts": [  # beyond push_horizon_days -> not pushed
         {"apn": "99999999", "catalyst_type": "tax_foreclosure_eligibility", "date": "2027-06-22",
          "days_until": 300, "source_url": "tax", "derived": True, "note": "approx"},
     ]},
]


def test_push_mapping():
    ok = True
    desired = events_mod.desired_events(CATALYSTS, RULES, context_by_apn([
        {"apn": "12345678", "owner": "SMITH LLC", "situs_address": "123 Main St",
         "seller_opportunity_score": 61, "band": "hot",
         "valuation": {"offer_band": {"as_is_high": 210000}}},
    ]))
    ok &= check("only in-horizon catalysts are pushed", len(desired), 2)

    key = events_mod.sync_key("12345678", "rezoning_hearing", "2026-09-15")
    ok &= check("event is keyed by apn+type+date", key in desired, True)
    body = desired[key]
    ok &= check("title uses the situs address", body["summary"], "rezoning hearing — 123 Main St")
    ok &= check("all-day event, end is exclusive",
                (body["start"]["date"], body["end"]["date"]), ("2026-09-15", "2026-09-16"))
    ok &= check("apn round-trips in extended properties",
                body["extendedProperties"]["private"]["apn"], "12345678")
    ok &= check("owner is in the description", "SMITH LLC" in body["description"], True)
    ok &= check("score is in the description",
                "Seller Opportunity Score: 61" in body["description"], True)
    ok &= check("offer band high is in the description",
                "Offer band high: 210000" in body["description"], True)
    ok &= check("catalyst does not block your day", body["transparency"], "transparent")
    ok &= check("20 days out is the 30-day colour", body["colorId"], "6")
    ok &= check("real URL becomes a source link", body["source"]["url"],
                "https://example.com/docket")

    other = desired[events_mod.sync_key("12345678", "loan_maturity", "2026-11-01")]
    ok &= check("non-URL provenance is not sent as a source link", "source" in other, False)
    ok &= check("...but is kept in the description", "rod-bk-123" in other["description"], True)

    # a parcel with no pipeline context still gets a usable title
    bare = events_mod.desired_events(CATALYSTS[:1], RULES, {})
    ok &= check("title falls back to the APN", bare[key]["summary"],
                "rezoning hearing — APN 12345678")
    return ok


def test_reconcile_is_idempotent():
    ok = True
    desired = events_mod.desired_events(CATALYSTS, RULES, {})
    keys = sorted(desired)

    # nothing on the calendar yet -> everything is an insert
    ins, pat, dele = events_mod.reconcile(desired, {}, TODAY)
    ok &= check("first run inserts everything", (len(ins), len(pat), len(dele)), (2, 0, 0))

    # same run again against what we just wrote -> no writes at all
    existing = {k: dict(desired[k], id=f"ev-{k}") for k in keys}
    ins, pat, dele = events_mod.reconcile(desired, existing, TODAY)
    ok &= check("re-running writes nothing", (len(ins), len(pat), len(dele)), (0, 0, 0))

    # someone renamed our event -> we patch it back
    drifted = {k: dict(existing[k]) for k in keys}
    drifted[keys[0]]["summary"] = "renamed by hand"
    ins, pat, dele = events_mod.reconcile(desired, drifted, TODAY)
    ok &= check("drifted title is patched", (len(ins), len(pat), len(dele)), (0, 1, 0))

    # catalyst resolved -> its future event is retired
    fewer = {keys[0]: desired[keys[0]]}
    ins, pat, dele = events_mod.reconcile(fewer, existing, TODAY)
    ok &= check("stale future event is retired", (len(ins), len(pat), len(dele)), (0, 0, 1))

    # a past event we own is history: never deleted
    past = {"gone": {"id": "old", "summary": "last month's hearing",
                     "start": {"date": "2026-07-01"}}}
    ins, pat, dele = events_mod.reconcile({}, past, TODAY)
    ok &= check("the past is never touched", len(dele), 0)
    return ok


def test_pull_mapping():
    ok = True
    calendar_events = [
        # typed by hand at a REIA meeting
        {"summary": "Rezoning hearing APN 12345678", "start": {"date": "2026-10-01"},
         "htmlLink": "https://calendar.google.com/e/1"},
        # timed event, apn in the description, alias needs the longest match
        {"summary": "Ground lease expiration", "description": "parcel: 555-22-1",
         "start": {"dateTime": "2026-10-05T14:00:00-04:00"}},
        # ours -- must not be read back in, or a derived guess becomes "explicit"
        {"summary": "loan maturity — 1 Main", "start": {"date": "2026-09-09"},
         "extendedProperties": {"private": {"monitorclt": "1", "apn": "12345678",
                                            "catalyst_type": "loan_maturity"}}},
        {"summary": "Dentist", "start": {"date": "2026-09-02"}},          # no parcel
        {"summary": "Hearing for something", "start": {"date": "2026-09-03"}},  # no APN
        {"summary": "Auction APN 777", "start": {"date": "2026-09-04"},
         "status": "cancelled"},                                          # cancelled
    ]
    rows = events_mod.to_catalyst_rows(calendar_events, RULES)
    ok &= check("only real parcel dates come back", len(rows), 2)
    ok &= check("apn parsed from the title", rows[0]["apn"], "12345678")
    ok &= check("catalyst type parsed from the title", rows[0]["catalyst_type"], "rezoning_hearing")
    ok &= check("timed event yields a date", rows[1]["date"], "2026-10-05")
    ok &= check("longest alias wins over 'lease expiration'",
                rows[1]["catalyst_type"], "ground_lease_expiration")
    ok &= check("our own events are not read back",
                all(r["apn"] != "12345678" or r["catalyst_type"] != "loan_maturity" for r in rows),
                True)

    # the payoff: catalyst.py treats a pulled row as EXPLICIT, so it is not a guess
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "monitorclt_catalyst"))
    import catalyst  # noqa: E402
    cat_rules = json.load(open(os.path.join(os.path.dirname(HERE), "monitorclt_catalyst",
                                            "catalyst_rules.json"), encoding="utf-8"))
    projected = catalyst.project_catalysts(rows, cat_rules, TODAY)
    ok &= check("pulled dates project as explicit catalysts",
                [p["derived"] for p in projected], [False, False])
    return ok


def test_client_paths():
    ok = True

    # pagination: two pages of events are concatenated
    transport = FakeTransport([
        (200, json.dumps({"access_token": "at", "expires_in": 3600})),
        (200, json.dumps({"items": [{"id": "a"}], "nextPageToken": "p2"})),
        (200, json.dumps({"items": [{"id": "b"}]})),
    ])
    got = Calendar(creds_with(transport)).list_events(time_min="2026-08-26T00:00:00Z")
    ok &= check("pages are concatenated", [e["id"] for e in got], ["a", "b"])
    ok &= check("second page sends the page token", "pageToken=p2" in transport.calls[-1][1], True)

    # an expired access token is refreshed once and the call is retried
    transport = FakeTransport([
        (200, json.dumps({"access_token": "at1", "expires_in": 3600})),
        (401, '{"error": "invalid"}'),
        (200, json.dumps({"access_token": "at2", "expires_in": 3600})),
        (200, json.dumps({"items": [{"id": "ok"}]})),
    ])
    got = Calendar(creds_with(transport)).list_events()
    ok &= check("401 triggers one refresh and retry", [e["id"] for e in got], ["ok"])

    # 429 backs off and retries; sleep is injected so the test is instant
    slept = []
    transport = FakeTransport([
        (200, json.dumps({"access_token": "at", "expires_in": 3600})),
        (429, '{"error": "rateLimitExceeded"}'),
        (200, json.dumps({"items": []})),
    ])
    Calendar(creds_with(transport), sleep=slept.append).list_events()
    ok &= check("rate limit backs off before retrying", slept, [1.0])

    # a real permission error is raised immediately, not retried into oblivion
    slept = []
    transport = FakeTransport([
        (200, json.dumps({"access_token": "at", "expires_in": 3600})),
        (403, '{"error": {"message": "insufficient permissions"}}'),
    ])
    try:
        Calendar(creds_with(transport), sleep=slept.append).list_events()
        ok &= check("403 raises", False, True)
    except CalendarError as e:
        ok &= check("403 raises immediately", "403" in str(e) and not slept, True)

    # the access token is cached, not re-fetched per call
    transport = FakeTransport([
        (200, json.dumps({"access_token": "at", "expires_in": 3600})),
        (200, "{}"), (200, "{}"),
    ])
    cal = Calendar(creds_with(transport))
    cal.insert_event({"summary": "x"})
    cal.insert_event({"summary": "y"})
    token_calls = [c for c in transport.calls if "oauth2" in c[1]]
    ok &= check("token is fetched once for two writes", len(token_calls), 1)
    ok &= check("write is authorized",
                transport.calls[-1][3]["Authorization"], "Bearer at")
    return ok


def test_dotenv():
    ok = True
    path = os.path.join(HERE, ".env.test-tmp")
    with open(path, "w", encoding="utf-8") as f:
        f.write("# comment\nCALENDAR_ID=work@example.com   # trailing\n"
                "GOOGLE_CLIENT_ID='quoted'\n\nBAD LINE\n")
    env = load_dotenv(path, env={"CALENDAR_ID": "already-set"})
    ok &= check("comments are stripped", env["GOOGLE_CLIENT_ID"], "quoted")
    ok &= check("the environment wins over .env", env["CALENDAR_ID"], "already-set")
    os.remove(path)

    env = load_dotenv(os.path.join(HERE, "no-such-file"), env={})
    ok &= check("a missing .env is fine", env, {})
    return ok


def main():
    ok = True
    for name, fn in [("push mapping", test_push_mapping),
                     ("reconcile", test_reconcile_is_idempotent),
                     ("pull mapping", test_pull_mapping),
                     ("api client", test_client_paths),
                     ("dotenv", test_dotenv)]:
        print(f"\n--- {name} ---")
        ok &= fn()
    print("\n" + ("ALL PASSED" if ok else "SOME TESTS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
