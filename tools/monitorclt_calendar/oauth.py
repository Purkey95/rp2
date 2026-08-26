#!/usr/bin/env python3
"""Google OAuth for MonitorCLT — refresh-token flow, pure stdlib.

Google's own client library is a heavy dependency tree; this module is the small
part of it we actually need. There are two moments:

  ONCE, interactively   `python3 oauth.py --authorize` opens Google's consent
                        screen, catches the redirect on http://localhost:<port>,
                        and prints a REFRESH TOKEN. Paste that into .env. It uses
                        PKCE + a state check, so a local process cannot race in
                        and steal the code.

  EVERY RUN, headless   Credentials.access_token() trades the refresh token for a
                        short-lived access token and caches it until it expires.
                        No browser, no user present -- which is what makes the
                        cron job in the README possible.

We use an installed-app ("Desktop app") client rather than a service account on
purpose: a service account has no access to a personal Google account's calendars
unless a Workspace admin delegates it, and Jeff's calendars are personal. It also
keeps this file free of the RSA/JWT signing a service account would need.

The refresh token is a long-lived credential to your calendar: it belongs in .env
(gitignored), never in the repo. Every call here is HTTPS to accounts.google.com
or oauth2.googleapis.com.
"""

import argparse
import base64
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"  # nosec B105 - a URL, not a password

# Read+write on events. Not calendar.settings, not Gmail, not Drive: the bridge
# only ever needs to list and edit events on the calendars you point it at.
SCOPE = "https://www.googleapis.com/auth/calendar.events"

USER_AGENT = "MonitorCLT-calendar/1.0"


def default_transport(method, url, body=None, headers=None):
    """(status, text). Injectable so every test in this module runs offline."""
    data = body.encode("utf-8") if isinstance(body, str) else body
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", USER_AGENT)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310 - https endpoints only
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


class AuthError(RuntimeError):
    pass


class Credentials:
    """Client id/secret + refresh token -> a cached access token."""

    def __init__(self, client_id, client_secret, refresh_token, transport=None, clock=time.time):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.transport = transport or default_transport
        self.clock = clock
        self._token = None
        self._expires_at = 0.0

    @classmethod
    def from_env(cls, env=None, transport=None):
        env = env if env is not None else os.environ
        missing = [k for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN")
                   if not env.get(k)]
        if missing:
            raise AuthError(f"missing in the environment: {', '.join(missing)} "
                            "(see config.example.env, then run `python3 oauth.py --authorize`)")
        return cls(env["GOOGLE_CLIENT_ID"], env["GOOGLE_CLIENT_SECRET"],
                   env["GOOGLE_REFRESH_TOKEN"], transport=transport)

    def access_token(self):
        if self._token and self.clock() < self._expires_at - 60:  # refresh a minute early
            return self._token
        payload = urllib.parse.urlencode({
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
            "grant_type": "refresh_token",
        })
        status, text = self.transport(
            "POST", TOKEN_ENDPOINT, payload,
            {"Content-Type": "application/x-www-form-urlencoded"})
        if status != 200:
            raise AuthError(f"token refresh failed ({status}): {_safe_error(text)}")
        data = json.loads(text)
        self._token = data["access_token"]
        self._expires_at = self.clock() + float(data.get("expires_in", 3600))
        return self._token

    def auth_header(self):
        return {"Authorization": f"Bearer {self.access_token()}"}


def _safe_error(text):
    """Surface Google's error without echoing anything token-shaped into a log."""
    try:
        data = json.loads(text)
    except ValueError:
        return text[:200]
    return f"{data.get('error', '?')}: {data.get('error_description', '')}".strip()


# ---- one-time interactive authorization ----

class _CallbackHandler(BaseHTTPRequestHandler):
    result = {}

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's API
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _CallbackHandler.result = {k: v[0] for k, v in query.items()}
        ok = "code" in _CallbackHandler.result
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<h2>MonitorCLT: authorized. You can close this tab.</h2>" if ok else
            b"<h2>MonitorCLT: authorization failed. Check the terminal.</h2>")

    def log_message(self, *args):  # keep the console clean
        pass


def authorize(client_id, client_secret, port=8765, scope=SCOPE, open_browser=True):
    """Run the loopback consent flow once and return the refresh token."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    redirect_uri = f"http://localhost:{port}/"

    url = AUTH_ENDPOINT + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope,
        "access_type": "offline",       # this is what makes Google issue a refresh token
        "prompt": "consent",            # ...and re-issue one if you have authorized before
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    })

    print("\nOpen this URL and grant access (it should open automatically):\n")
    print(url + "\n")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # nosec B110 - headless host: the printed URL is the fallback
            pass

    server = HTTPServer(("127.0.0.1", port), _CallbackHandler)
    print(f"Waiting for the redirect on {redirect_uri} ...")
    server.handle_request()
    server.server_close()
    result = _CallbackHandler.result

    if result.get("error"):
        raise AuthError(f"Google returned: {result['error']}")
    if not result.get("code"):
        raise AuthError("no authorization code in the redirect")
    if not secrets.compare_digest(result.get("state", ""), state):
        raise AuthError("state mismatch — aborting rather than trusting that redirect")

    payload = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "code": result["code"],
        "code_verifier": verifier,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    })
    status, text = default_transport("POST", TOKEN_ENDPOINT, payload,
                                     {"Content-Type": "application/x-www-form-urlencoded"})
    if status != 200:
        raise AuthError(f"code exchange failed ({status}): {_safe_error(text)}")
    data = json.loads(text)
    if not data.get("refresh_token"):
        raise AuthError("Google did not return a refresh token — revoke the app at "
                        "myaccount.google.com/permissions and retry")
    return data["refresh_token"]


def _client_from_args(args):
    if args.client_secrets:
        blob = json.load(open(args.client_secrets, encoding="utf-8"))
        node = blob.get("installed") or blob.get("web") or blob
        return node["client_id"], node["client_secret"]
    client_id = args.client_id or os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = args.client_secret or os.environ.get("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise SystemExit("need --client-secrets, or --client-id/--client-secret, or "
                         "GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET in the environment")
    return client_id, client_secret


def main():
    ap = argparse.ArgumentParser(description="MonitorCLT Google OAuth helper")
    ap.add_argument("--authorize", action="store_true",
                    help="run the one-time consent flow and print a refresh token")
    ap.add_argument("--check", action="store_true",
                    help="verify the refresh token in the environment still works")
    ap.add_argument("--client-secrets", help="client_secret_*.json downloaded from Google Cloud")
    ap.add_argument("--client-id")
    ap.add_argument("--client-secret")
    ap.add_argument("--port", type=int, default=8765, help="loopback port for the redirect")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    if args.check:
        creds = Credentials.from_env()
        creds.access_token()
        print("OK — refresh token works, access token obtained.")
        return 0

    if not args.authorize:
        ap.print_help()
        return 1

    client_id, client_secret = _client_from_args(args)
    token = authorize(client_id, client_secret, port=args.port, open_browser=not args.no_browser)
    print("\n" + "=" * 72)
    print("Add these to tools/monitorclt_calendar/.env (never commit them):\n")
    print(f"GOOGLE_CLIENT_ID={client_id}")
    print("GOOGLE_CLIENT_SECRET=<the client secret you just used>")
    print(f"GOOGLE_REFRESH_TOKEN={token}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
