"""Transports: where bytes come from. HTTP with retry/backoff, or recorded fixtures."""

from __future__ import annotations

import http.cookiejar
import os
import time
import urllib.error
import urllib.request
from typing import Callable, Dict, Iterable, Optional, Tuple
from urllib.parse import urlencode

Response = Tuple[bytes, Optional[str]]  # body, content-type


class Transport:
    def get(self, url: str, params: Optional[Dict[str, str]] = None) -> Response:  # pragma: no cover - abstract
        raise NotImplementedError

    def post(self, url: str, fields: Dict[str, str], referer: Optional[str] = None) -> Response:  # pragma: no cover - abstract
        raise NotImplementedError("this transport does not support POST")


class HttpTransport(Transport):
    """urllib with retry/backoff, an honest user agent, optional cookies (WebForms portals
    keep the search in the session) and a politeness interval between requests."""

    def __init__(
        self,
        user_agent: str = "MonitorCLT/2.0 (+public records monitor)",
        retries: int = 3,
        backoff_s: float = 1.0,
        timeout_s: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
        min_interval_s: float = 0.0,
        cookies: bool = False,
    ) -> None:
        self.user_agent = user_agent
        self.retries = retries
        self.backoff_s = backoff_s
        self.timeout_s = timeout_s
        self.sleep = sleep
        self.min_interval_s = min_interval_s
        self._last = 0.0
        self.jar = http.cookiejar.CookieJar() if cookies else None
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar)) if cookies else urllib.request.build_opener()

    def _pace(self) -> None:
        if self.min_interval_s:
            wait = self._last + self.min_interval_s - time.time()
            if wait > 0:
                self.sleep(wait)
            self._last = time.time()

    def _open(self, req: "urllib.request.Request") -> Response:
        last: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                self._pace()
                with self._opener.open(req, timeout=self.timeout_s) as resp:  # nosec B310 - http(s) only, public portals
                    return resp.read(), resp.headers.get("Content-Type")
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:  # pragma: no cover - network
                last = exc
                status = getattr(exc, "code", None)
                if status is not None and 400 <= status < 500 and status != 429:
                    raise
                self.sleep(self.backoff_s * (2**attempt))
        raise RuntimeError("fetch failed after {0} attempts: {1}".format(self.retries + 1, last))

    def get(self, url: str, params: Optional[Dict[str, str]] = None) -> Response:
        if params:
            url = url + ("&" if "?" in url else "?") + urlencode(params)
        return self._open(urllib.request.Request(url, headers={"User-Agent": self.user_agent}))

    def post(self, url: str, fields: Dict[str, str], referer: Optional[str] = None) -> Response:
        headers = {"User-Agent": self.user_agent, "Content-Type": "application/x-www-form-urlencoded"}
        if referer:
            headers["Referer"] = referer
        return self._open(urllib.request.Request(url, data=urlencode(fields).encode("utf-8"), headers=headers, method="POST"))


class FixtureTransport(Transport):
    """Serves recorded bodies from a directory (or an in-memory map), keyed by a name.

    Connectors call transport.get("fixture://estate_cases.jsonl") and get the bytes
    they would have parsed from the live site. Contract tests run on exactly this.
    """

    def __init__(self, root: Optional[str] = None, bodies: Optional[Dict[str, bytes]] = None) -> None:
        self.root = root
        self.bodies = dict(bodies or {})
        self.requests: list = []

    def get(self, url: str, params: Optional[Dict[str, str]] = None) -> Response:
        self.requests.append((url, params))
        name = url.split("://", 1)[1] if "://" in url else url
        if name in self.bodies:
            return self.bodies[name], _guess_type(name)
        if self.root:
            path = os.path.join(self.root, name)
            if os.path.exists(path):
                with open(path, "rb") as f:
                    return f.read(), _guess_type(name)
        raise FileNotFoundError("no fixture for {0}".format(name))

    def post(self, url: str, fields: Dict[str, str], referer: Optional[str] = None) -> Response:
        """Fixtures replay a recorded conversation: a POST is answered like a GET of the same name."""
        return self.get(url)

    def available(self) -> Iterable[str]:
        names = set(self.bodies)
        if self.root and os.path.isdir(self.root):
            names.update(os.listdir(self.root))
        return sorted(names)


def _guess_type(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower()
    return {"json": "application/json", "jsonl": "application/x-ndjson", "csv": "text/csv", "html": "text/html", "htm": "text/html"}.get(
        ext, "application/octet-stream"
    )
