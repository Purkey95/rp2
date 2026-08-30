#!/usr/bin/env python3
"""Reference webhook server for MonitorCLT inbound calls (stdlib http.server).

A thin runner around the pure logic in inbound.py — signature-gate the request,
resolve the caller, return TwiML, and emit the screen pop (log + optional push to
a dashboard/Slack/CRM webhook). Drop the same handlers into Flask/FastAPI/Lambda
on the host; this exists so the framework runs with zero dependencies.

Config comes from the environment (see config.example.env). Nothing secret is
hardcoded — TWILIO_AUTH_TOKEN gates every request.

    TWILIO_AUTH_TOKEN=... OPERATOR_NUMBER=+1704... PUBLIC_BASE_URL=https://calls.cltbuys.com \
    CONTACTS_CSV=sample/contacts.csv LEADS_JSONL=sample/leads.jsonl python3 server.py
"""

import json
import os
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import inbound
from lookup import CallerLookup, load_contacts_csv, load_leads_jsonl
from twilio_sig import valid_signature


def build_context():
    lookup = CallerLookup(
        load_contacts_csv(os.environ["CONTACTS_CSV"]),
        load_leads_jsonl(os.environ["LEADS_JSONL"]),
    )
    return inbound.Context(
        lookup=lookup,
        operator_number=os.environ["OPERATOR_NUMBER"],
        public_base_url=os.environ["PUBLIC_BASE_URL"],
        record_calls=os.environ.get("RECORD_CALLS", "true").lower() == "true",
        two_party_consent=os.environ.get("TWO_PARTY_CONSENT", "false").lower() == "true",
        business_name=os.environ.get("BUSINESS_NAME", "CLT Buys"),
    )


def _emit_screen_pop(pop, call_sid):
    row = {"ts": datetime.now(timezone.utc).isoformat(), "call_sid": call_sid, **pop}
    with open(os.environ.get("CALL_LOG", "call_log.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    hook = os.environ.get("SCREENPOP_WEBHOOK")
    if hook:  # push to a dashboard / Slack / CRM; never let a push failure drop the call
        try:
            req = urllib.request.Request(hook, data=json.dumps(row).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=3).read()
        except Exception:
            pass


class Handler(BaseHTTPRequestHandler):
    ctx = None
    auth_token = ""

    def _twiml(self, xml):
        self.send_response(200)
        self.send_header("Content-Type", "text/xml; charset=utf-8")
        self.end_headers()
        self.wfile.write(xml.encode("utf-8"))

    def _deny(self, code=403):
        self.send_response(code)
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        body = {k: v[0] for k, v in parse_qs(raw).items()}
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}

        # Signature is over the exact public URL (incl. query) + POST params.
        url = self.ctx.public_base_url + self.path
        sig = self.headers.get("X-Twilio-Signature", "")
        if not valid_signature(url, body, sig, self.auth_token):
            return self._deny(403)

        params = {**query, **body}
        if parsed.path == "/voice/inbound":
            xml, pop = inbound.handle_inbound(params, self.ctx)
            _emit_screen_pop(pop, params.get("CallSid", ""))
            return self._twiml(xml)
        if parsed.path == "/voice/whisper":
            return self._twiml(inbound.handle_whisper(params, self.ctx))
        return self._deny(404)

    def log_message(self, *args):
        pass  # quiet


def main():
    Handler.ctx = build_context()
    Handler.auth_token = os.environ["TWILIO_AUTH_TOKEN"]
    port = int(os.environ.get("PORT", "8080"))
    print(f"MonitorCLT voice webhook on :{port}  (inbound=/voice/inbound whisper=/voice/whisper)")
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
