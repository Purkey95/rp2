"""Core inbound-call logic: pure functions from Twilio params -> (TwiML, screen-pop).

Kept transport-free so it drops into Flask / FastAPI / AWS Lambda / the stdlib
server here, and is unit-tested without a network. Two handlers:

  handle_inbound(params, ctx)  -> (twiml, screen_pop)
      Greets, warm-transfers to the operator with a whisper announcing the
      caller's distress profile, records (per consent config), and voicemail
      fallback. Returns the screen_pop dict for the CRM/dashboard + push.

  handle_whisper(params, ctx)  -> twiml
      What the operator hears BEFORE the caller is bridged: a one-line read of
      who's calling and how hot the parcel is.
"""

from urllib.parse import urlencode

import twiml


class Context:
    """Runtime config + the caller lookup. Built once from env on the host."""
    def __init__(self, lookup, operator_number, public_base_url,
                 record_calls=True, two_party_consent=False,
                 business_name="CLT Buys"):
        self.lookup = lookup
        self.operator_number = operator_number
        self.public_base_url = public_base_url.rstrip("/")
        self.record_calls = record_calls
        self.two_party_consent = two_party_consent
        self.business_name = business_name


def build_screen_pop(caller_number, ctx):
    return ctx.lookup.resolve(caller_number)


def _whisper_line(pop):
    if not pop.get("matched"):
        return "Incoming call. No property match on this number."
    who = pop.get("owner") or "owner"
    situs = pop.get("situs_address") or "property on file"
    if pop.get("score") is not None:
        sigs = ", ".join(pop.get("top_signals", [])[:3]) or "no active signals"
        return (f"Incoming seller. {who}, {situs}. "
                f"Score {pop['score']}, {pop.get('band', '')}. Signals: {sigs}.")
    return f"Incoming. {who}, {situs}. Matched contact, no score yet."


def handle_inbound(params, ctx):
    caller = params.get("From", "")
    to = params.get("To", "")
    pop = build_screen_pop(caller, ctx)

    greeting = f"Thank you for calling {ctx.business_name}. Connecting you now."
    if ctx.two_party_consent:
        greeting += " This call may be recorded."

    record_mode = "record-from-answer-dual" if ctx.record_calls else None
    whisper_url = f"{ctx.public_base_url}/voice/whisper?" + urlencode({"caller": caller})

    body = twiml.say(greeting)
    body += twiml.dial_number(ctx.operator_number, caller_id=to, record=record_mode,
                              whisper_url=whisper_url)
    # Fallback if the operator doesn't pick up.
    body += twiml.say("Sorry we missed you. Please leave a message after the tone.")
    body += twiml.record(max_length=120)
    return twiml.response(body), pop


def handle_whisper(params, ctx):
    # caller number arrives as a query param on the whisper URL.
    caller = params.get("caller") or params.get("From", "")
    pop = build_screen_pop(caller, ctx)
    return twiml.response(twiml.say(_whisper_line(pop)))
