"""Pin the inbound-call framework: signature, lookup/screen-pop, TwiML. Offline.

Run: python3 test_voice.py
"""

import os
from xml.dom.minidom import parseString

import inbound
from lookup import CallerLookup, load_contacts_csv, load_leads_jsonl, normalize_phone
from twilio_sig import expected_signature, valid_signature

HERE = os.path.dirname(os.path.abspath(__file__))


def check(label, got, want):
    ok = got == want
    print(f"[{'ok ' if ok else 'FAIL'}] {label}: {got!r}")
    return ok


def make_ctx():
    lookup = CallerLookup(
        load_contacts_csv(os.path.join(HERE, "sample", "contacts.csv")),
        load_leads_jsonl(os.path.join(HERE, "sample", "leads.jsonl")),
    )
    return inbound.Context(lookup, operator_number="+17045550000",
                           public_base_url="https://calls.example", two_party_consent=True)


def main():
    ok = True

    # ---- phone normalization ----
    ok &= check("normalize parens/dashes", normalize_phone("(704) 555-0142"), "7045550142")
    ok &= check("normalize +1", normalize_phone("+1 704-555-0142"), "7045550142")
    ok &= check("normalize junk", normalize_phone("na"), "")

    # ---- Twilio signature validation ----
    url = "https://calls.example/voice/inbound"
    params = {"From": "+17045550142", "To": "+19805551000", "CallSid": "CA123"}
    token = "test_auth_token"
    sig = expected_signature(url, params, token)
    ok &= check("signature validates", valid_signature(url, params, sig, token), True)
    ok &= check("tampered params rejected",
                valid_signature(url, {**params, "From": "+10000000000"}, sig, token), False)
    ok &= check("wrong token rejected", valid_signature(url, params, sig, "nope"), False)
    ok &= check("missing signature rejected", valid_signature(url, params, "", token), False)

    ctx = make_ctx()

    # ---- screen pop: matched hot lead ----
    xml, pop = inbound.handle_inbound({"From": "(704) 555-0142", "To": "+19805551000",
                                       "CallSid": "CA1"}, ctx)
    ok &= check("matched", pop["matched"], True)
    ok &= check("score surfaced", pop["score"], 98)
    ok &= check("band surfaced", pop["band"], "immediate")
    ok &= check("top signals surfaced", pop["top_signals"][:2], ["tax_delinquency", "code_violation"])

    # ---- TwiML well-formed + dials operator + records + consent notice ----
    parseString(xml)  # raises if not well-formed XML
    ok &= check("dials operator", ctx.operator_number in xml, True)
    ok &= check("records call", "record-from-answer-dual" in xml, True)
    ok &= check("consent notice (2-party)", "may be recorded" in xml, True)
    ok &= check("whisper url wired", "/voice/whisper" in xml, True)

    # ---- unmatched caller ----
    xml2, pop2 = inbound.handle_inbound({"From": "+19999999999", "To": "+19805551000"}, ctx)
    ok &= check("unmatched flagged", pop2["matched"], False)
    ok &= check("unmatched still connects", ctx.operator_number in xml2, True)

    # ---- whisper audio read ----
    w = inbound.handle_whisper({"caller": "(704) 555-0142"}, ctx)
    ok &= check("whisper announces score", "Score 98" in w, True)
    ok &= check("whisper names signals", "tax_delinquency" in w, True)
    w_unknown = inbound.handle_whisper({"caller": "+19999999999"}, ctx)
    ok &= check("whisper no-match line", "No property match" in w_unknown, True)

    print("\n" + ("ALL PASSED" if ok else "SOME TESTS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
