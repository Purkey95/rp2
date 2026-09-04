"""HTTP API and the reviewer UI, on the stdlib server.

Endpoints are the product surface: permalinks per parcel / person / match with the
full evidence trail, the review queue and decisions, watchlists, the event stream,
the signal ranking, data status, and a policy-gated export. Anything person-level
goes through policy.check_export; the API never has a second path.
"""

from __future__ import annotations

import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from . import entities, history, policy, quality, review, signals, watch
from .normalize.pins import parcel_id
from .resolve.model import LinkModel, load_rules
from .store import Store, loads

HERE = os.path.dirname(os.path.abspath(__file__))
UI_PATH = os.path.join(HERE, "ui", "review.html")

Route = Tuple[str, "re.Pattern[str]", Callable[..., Any]]


class Api:
    """Routing and handlers, independent of the HTTP server so tests can call them directly."""

    def __init__(self, store: Store, token: Optional[str] = None, base_url: str = "http://localhost:8765/") -> None:
        self.store = store
        self.token = token
        self.base_url = base_url
        self.lock = threading.Lock()
        self.routes: List[Route] = [
            ("GET", re.compile(r"^/api/status$"), self.status),
            ("GET", re.compile(r"^/api/model$"), self.model),
            ("GET", re.compile(r"^/api/review/queue$"), self.queue),
            ("GET", re.compile(r"^/api/matches/(\d+)$"), self.match_detail),
            ("POST", re.compile(r"^/api/matches/(\d+)/decision$"), self.decide),
            ("GET", re.compile(r"^/api/parcels/([^/]+)/([^/]+)$"), self.parcel),
            ("GET", re.compile(r"^/api/persons/(\d+)$"), self.person),
            ("GET", re.compile(r"^/api/events$"), self.events),
            ("GET", re.compile(r"^/api/rank$"), self.rank),
            ("GET", re.compile(r"^/api/watchlists$"), self.list_watchlists),
            ("POST", re.compile(r"^/api/watchlists$"), self.create_watchlist),
            ("GET", re.compile(r"^/api/watchlists/(\d+)/digest$"), self.digest),
            ("GET", re.compile(r"^/api/export$"), self.export),
            ("GET", re.compile(r"^/api/leads/([0-9a-f]+)/provenance$"), self.provenance),
        ]

    def dispatch(self, method: str, path: str, query: Dict[str, List[str]], body: Optional[Dict[str, Any]], headers: Dict[str, str]) -> Tuple[int, Any]:
        if self.token and headers.get("x-monitorclt-token") != self.token:
            return 401, {"error": "missing or invalid token"}
        for m, pattern, handler in self.routes:
            match = pattern.match(path)
            if m == method and match:
                with self.lock:
                    try:
                        return handler(*match.groups(), query=query, body=body or {}, headers=headers)
                    except KeyError as exc:
                        return 404, {"error": str(exc)}
                    except ValueError as exc:
                        return 400, {"error": str(exc)}
        return 404, {"error": "no route for {0} {1}".format(method, path)}

    # ---------------------------------------------------------- handlers ---

    def status(self, query: Dict[str, List[str]], **_: Any) -> Tuple[int, Any]:
        rows = quality.latest(self.store)
        if not rows or query.get("refresh"):
            rows = quality.snapshot(self.store)
        return 200, {"connectors": rows, "text": quality.format_status(rows)}

    def model(self, **_: Any) -> Tuple[int, Any]:
        rules = load_rules()
        return 200, {"model": LinkModel.active(self.store, rules).to_dict(), "thresholds": rules["thresholds"], "gates": rules["gates"]}

    def queue(self, query: Dict[str, List[str]], **_: Any) -> Tuple[int, Any]:
        limit = int(query.get("limit", ["25"])[0])
        return 200, {"queue": review.queue(self.store, limit)}

    def match_detail(self, match_id: str, **_: Any) -> Tuple[int, Any]:
        d = review.detail(self.store, int(match_id))
        if d is None:
            return 404, {"error": "no match {0}".format(match_id)}
        return 200, d

    def decide(self, match_id: str, body: Dict[str, Any], headers: Dict[str, str], **_: Any) -> Tuple[int, Any]:
        reviewer = body.get("reviewer") or headers.get("x-reviewer")
        if not reviewer:
            return 400, {"error": "reviewer is required"}
        m = review.decide(self.store, int(match_id), body.get("decision", ""), reviewer, body.get("note"), body.get("seconds_spent"))
        return 200, {"match": m}

    def parcel(self, county: str, pin: str, **_: Any) -> Tuple[int, Any]:
        pid = parcel_id(county, pin)
        parcel = entities.get_parcel(self.store, pid)
        if parcel is None:
            return 404, {"error": "no parcel {0}".format(pid)}
        sigs = signals.parcel_signals(self.store, parcel)
        versions = history.versions(self.store, "parcel", parcel["county"], parcel["pin"].upper())
        events = [dict(e, payload=loads(e["payload"], {})) for e in self.store.query("SELECT * FROM event WHERE parcel_id = ? ORDER BY id", (pid,))]
        matches = self.store.query(
            "SELECT id, kind, left_id, status, probability, match_tier FROM entity_match WHERE right_id = ? ORDER BY probability DESC", (pid,)
        )
        return 200, {
            "parcel": parcel,
            "signals": sigs,
            "score": signals.score(sigs),
            "history": versions,
            "events": events,
            "matches": matches,
            "permalink": "{0}api/parcels/{1}/{2}".format(self.base_url, parcel["county"], parcel["pin"]),
        }

    def person(self, person_id: str, **_: Any) -> Tuple[int, Any]:
        p = self.store.one("SELECT * FROM person WHERE id = ?", (int(person_id),))
        if p is None:
            return 404, {"error": "no person {0}".format(person_id)}
        p["aliases"] = self.store.query("SELECT alias_normalized, source FROM person_alias WHERE person_id = ?", (p["id"],))
        p["addresses"] = self.store.query("SELECT address_norm, address_kind, observed_at FROM person_address WHERE person_id = ?", (p["id"],))
        p["mentions"] = self.store.query("SELECT id, source, county, natural_key, role, raw_name FROM mention WHERE person_id = ? ORDER BY id", (p["id"],))
        p["matches"] = self.store.query("SELECT id, kind, left_id, right_id, status, probability FROM entity_match WHERE person_id = ?", (p["id"],))
        return 200, p

    def events(self, query: Dict[str, List[str]], **_: Any) -> Tuple[int, Any]:
        after = int(query.get("after", ["0"])[0])
        kinds = query.get("kind")
        limit = int(query.get("limit", ["200"])[0])
        rows = history.events_since(self.store, query.get("since", [None])[0], kinds, after, limit)
        return 200, {"events": rows, "next_after": rows[-1]["id"] if rows else after}

    def rank(self, query: Dict[str, List[str]], **_: Any) -> Tuple[int, Any]:
        rows = signals.rank(
            self.store, query.get("county", [None])[0], int(query.get("limit", ["50"])[0]), float(query.get("min_score", ["1"])[0]), query.get("zip")
        )
        return 200, {"rank": rows}

    def list_watchlists(self, **_: Any) -> Tuple[int, Any]:
        return 200, {"watchlists": watch.watchlists(self.store, active_only=False)}

    def create_watchlist(self, body: Dict[str, Any], headers: Dict[str, str], **_: Any) -> Tuple[int, Any]:
        owner = body.get("owner") or headers.get("x-reviewer") or "api"
        if not body.get("name"):
            return 400, {"error": "name is required"}
        wid = watch.create_watchlist(
            self.store, body["name"], owner, body.get("filters", {}), body.get("channel", "digest"), body.get("endpoint"), body.get("secret")
        )
        return 201, {"id": wid}

    def digest(self, watchlist_id: str, query: Dict[str, List[str]], **_: Any) -> Tuple[int, Any]:
        watch.evaluate_watchlists(self.store, base_url=self.base_url)
        text = watch.digest(self.store, int(watchlist_id), mark_delivered=bool(query.get("deliver")))
        return 200, {"digest": text}

    def export(self, query: Dict[str, List[str]], headers: Dict[str, str], **_: Any) -> Tuple[int, Any]:
        actor = query.get("actor", [headers.get("x-reviewer") or "api"])[0]
        return 200, policy.export(self.store, actor, "api", base_url=self.base_url)

    def provenance(self, lead_id: str, **_: Any) -> Tuple[int, Any]:
        trail = policy.why_do_you_have_this(self.store, lead_id)
        if trail is None:
            return 404, {"error": "no exported lead {0}".format(lead_id)}
        return 200, trail


def ui_html() -> str:
    with open(UI_PATH, encoding="utf-8") as f:
        return f.read()


class Handler(BaseHTTPRequestHandler):
    api: Api

    def log_message(self, fmt: str, *args: Any) -> None:  # quiet by default
        return None

    def _send(self, status: int, payload: Any, content_type: str = "application/json") -> None:
        body = payload.encode("utf-8") if isinstance(payload, str) else json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self, method: str) -> None:
        url = urlparse(self.path)
        if method == "GET" and url.path in ("/", "/review"):
            self._send(200, ui_html(), "text/html")
            return
        body = None
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except ValueError:
                self._send(400, {"error": "invalid JSON body"})
                return
        headers = {k.lower(): v for k, v in self.headers.items()}
        status, payload = self.api.dispatch(method, url.path, parse_qs(url.query), body, headers)
        self._send(status, payload)

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")


def make_server(store: Store, host: str = "127.0.0.1", port: int = 8765, token: Optional[str] = None) -> ThreadingHTTPServer:
    api = Api(store, token, "http://{0}:{1}/".format(host, port))
    handler = type("BoundHandler", (Handler,), {"api": api})
    server = ThreadingHTTPServer((host, port), handler)
    server.api = api  # type: ignore[attr-defined]
    return server


def serve(store: Store, host: str = "127.0.0.1", port: int = 8765, token: Optional[str] = None) -> None:  # pragma: no cover - blocking
    server = make_server(store, host, port, token)
    print("MonitorCLT serving on http://{0}:{1}/  (reviewer UI at /review, API under /api)".format(host, server.server_address[1]))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
