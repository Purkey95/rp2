"""Minimal Google Calendar API v3 client — the five calls the bridge needs.

list_calendars / list_events / insert / patch / delete, plus pagination, a single
retry on an expired access token, and bounded backoff on 429 and 5xx (Calendar's
per-minute quota is easy to hit when a first sync writes a few hundred events).

The transport is injectable, so test_calendar.py exercises every path -- including
pagination and rate-limit retry -- with no network and no credentials.

Pure stdlib.
"""

import json
import time
import urllib.parse

BASE = "https://www.googleapis.com/calendar/v3"
RETRY_STATUSES = (403, 429, 500, 502, 503, 504)


class CalendarError(RuntimeError):
    pass


class Calendar:
    """Bound to one calendar id. `primary` is the account's own calendar."""

    def __init__(self, credentials, calendar_id="primary", transport=None,
                 max_retries=4, sleep=time.sleep):
        self.credentials = credentials
        self.calendar_id = calendar_id
        self.transport = transport or credentials.transport
        self.max_retries = max_retries
        self.sleep = sleep

    # ---- plumbing ----

    def _request(self, method, path, params=None, body=None, _retried_auth=False):
        url = BASE + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = dict(self.credentials.auth_header())
        payload = None
        if body is not None:
            payload = json.dumps(body)
            headers["Content-Type"] = "application/json"

        delay = 1.0
        for attempt in range(self.max_retries + 1):
            status, text = self.transport(method, url, payload, headers)

            if status == 401 and not _retried_auth:
                # access token expired mid-run: drop the cache and try once more
                self.credentials._token = None  # pylint: disable=protected-access
                return self._request(method, path, params, body, _retried_auth=True)

            if status in RETRY_STATUSES and attempt < self.max_retries:
                if status == 403 and "rateLimitExceeded" not in text and "userRateLimit" not in text:
                    break  # a real permission problem, not throttling -- do not sit on it
                self.sleep(delay)
                delay *= 2
                continue

            if 200 <= status < 300:
                return json.loads(text) if text.strip() else {}
            break

        raise CalendarError(f"{method} {path} -> {status}: {text[:300]}")

    def _paged(self, path, params, item_key="items"):
        out, page_token = [], None
        while True:
            page = dict(params)
            if page_token:
                page["pageToken"] = page_token
            data = self._request("GET", path, params=page)
            out.extend(data.get(item_key, []))
            page_token = data.get("nextPageToken")
            if not page_token:
                return out

    # ---- calls ----

    def list_calendars(self):
        return self._paged("/users/me/calendarList", {"maxResults": 250})

    def list_events(self, time_min=None, time_max=None, private_property=None,
                    query=None, show_deleted=False, max_results=250):
        """time_min/time_max are RFC3339 timestamps. private_property filters on an
        extendedProperties.private entry ("key=value") -- how we find the events
        this tool owns without walking the whole calendar."""
        params = {
            "maxResults": max_results,
            "singleEvents": "true",       # expand recurrence into real dated instances
            "orderBy": "startTime",
            "showDeleted": "true" if show_deleted else "false",
        }
        if time_min:
            params["timeMin"] = time_min
        if time_max:
            params["timeMax"] = time_max
        if private_property:
            params["privateExtendedProperty"] = private_property
        if query:
            params["q"] = query
        return self._paged(f"/calendars/{urllib.parse.quote(self.calendar_id)}/events", params)

    def insert_event(self, body):
        return self._request("POST", f"/calendars/{urllib.parse.quote(self.calendar_id)}/events",
                             body=body)

    def patch_event(self, event_id, body):
        return self._request("PATCH",
                             f"/calendars/{urllib.parse.quote(self.calendar_id)}/events/"
                             f"{urllib.parse.quote(event_id)}", body=body)

    def delete_event(self, event_id):
        return self._request("DELETE",
                             f"/calendars/{urllib.parse.quote(self.calendar_id)}/events/"
                             f"{urllib.parse.quote(event_id)}")
