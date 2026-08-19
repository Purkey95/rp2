"""Twilio request-signature validation — so only Twilio can drive the webhook.

Twilio signs each request: HMAC-SHA1 (key = your Auth Token) over the full URL
with every POST param appended as key+value, sorted by key, then base64. We
recompute and constant-time compare against the X-Twilio-Signature header. This
is the gate that stops anyone from spoofing an inbound-call webhook to pull a
caller's distress profile.

Pure stdlib. Auth token comes from the environment, never hardcoded.
"""

import base64
import hashlib
import hmac


def expected_signature(url, params, auth_token):
    """url = the exact public URL Twilio requested (incl. query string).
    params = the POST form params (dict). Returns the base64 signature string."""
    data = url
    for key in sorted(params):
        data += key + (params[key] if params[key] is not None else "")
    digest = hmac.new(auth_token.encode("utf-8"), data.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("utf-8")


def valid_signature(url, params, provided, auth_token):
    if not provided or not auth_token:
        return False
    return hmac.compare_digest(expected_signature(url, params, auth_token), provided)
