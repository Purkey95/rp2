"""The daily run, as one function: ingest -> resolve -> cluster -> watchlists -> deliver -> status.

Each step records its result; a failure in one step does not hide the others. The
run returns non-zero when any connector failed or any quality anomaly is present, so
a scheduler can page on it, and it can POST a summary to an alert webhook.
"""

from __future__ import annotations

import json
import traceback
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from . import ingest, persons, quality, watch
from .resolve import resolve
from .sources.base import registry
from .sources.transport import Transport
from .store import Store

Sender = Callable[[str, Dict[str, str], bytes], int]


def _http_sender(url: str, headers: Dict[str, str], body: bytes) -> int:  # pragma: no cover - network
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310 - operator-configured https endpoint
        return int(resp.status)


def run_daily(
    store: Store,
    county: str,
    transport: Transport,
    sender: Optional[Sender] = None,
    alert_webhook: Optional[str] = None,
    base_url: str = "monitorclt://",
    sources: Optional[List[str]] = None,
    profile: str = "live",
    endpoints: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    sender = sender or _http_sender
    report: Dict[str, Any] = {"county": county, "started_at": store.now(), "steps": [], "ok": True}

    def step(name: str, fn: Callable[[], Any]) -> Any:
        try:
            out = fn()
            report["steps"].append({"step": name, "ok": True, "result": out})
            return out
        except Exception:  # keep going; the summary carries the traceback
            report["steps"].append({"step": name, "ok": False, "error": traceback.format_exc()})
            report["ok"] = False
            return None

    def do_ingest() -> Dict[str, Any]:
        results = []
        for connector in registry.connectors(county, profile, endpoints):
            if sources and connector.name not in sources:
                continue
            results.append(ingest.ingest(store, connector, transport))
        from . import entities

        entities.backfill_parcel_coordinates(store)
        failed = [r for r in results if not r.ok]
        if failed:
            report["ok"] = False
        return {
            "connectors": {
                r["connector"]: {
                    "status": r["status"],
                    "new": r["rows_new"],
                    "changed": r["rows_changed"],
                    "retired": r["rows_retired"],
                    "failed": r["rows_failed"],
                }
                for r in results
            }
        }

    step("ingest", do_ingest)
    step("resolve", lambda: resolve(store)["counts"])
    step("cluster", lambda: persons.cluster_persons(store))
    step("watchlists", lambda: watch.evaluate_watchlists(store, base_url=base_url))
    step("deliver", lambda: watch.deliver(store, sender))

    def do_status() -> Dict[str, Any]:
        rows = quality.snapshot(store)
        anomalies = {"{0}/{1}".format(r["connector"], r["county"]): r["anomalies"] for r in rows if r["anomalies"]}
        if anomalies:
            report["ok"] = False
        return {"anomalies": anomalies, "text": quality.format_status(rows)}

    step("status", do_status)
    report["finished_at"] = store.now()
    if alert_webhook and not report["ok"]:
        try:
            body = json.dumps({"text": summary(report)}, default=str).encode("utf-8")
            sender(alert_webhook, {"Content-Type": "application/json"}, body)
            report["alerted"] = True
        except Exception as exc:  # pragma: no cover - network
            report["alerted"] = False
            report["alert_error"] = str(exc)
    return report


def summary(report: Dict[str, Any]) -> str:
    lines = ["MonitorCLT daily run for {0}: {1}".format(report["county"], "OK" if report["ok"] else "ATTENTION")]
    for s in report["steps"]:
        if not s["ok"]:
            lines.append("  {0}: FAILED\n    {1}".format(s["step"], (s.get("error") or "").strip().splitlines()[-1]))
            continue
        r = s.get("result") or {}
        if s["step"] == "ingest":
            bad = [k for k, v in r.get("connectors", {}).items() if v["status"] != "ok"]
            lines.append("  ingest: {0} connectors{1}".format(len(r.get("connectors", {})), ", FAILED: " + ", ".join(bad) if bad else ""))
        elif s["step"] == "resolve":
            lines.append("  resolve: {0} confirmed, {1} pending, {2} blocked out".format(r.get("confirmed"), r.get("pending"), r.get("blocked_out")))
        elif s["step"] == "cluster":
            lines.append("  cluster: {0} mentions attached".format(r.get("attached")))
        elif s["step"] == "watchlists":
            lines.append("  watchlists: {0} notifications".format(r.get("notifications_created")))
        elif s["step"] == "deliver":
            lines.append("  deliver: {0}".format(", ".join("wl{0}={1}".format(d["watchlist_id"], d["status"]) for d in r) or "nothing to send"))
        elif s["step"] == "status":
            an = r.get("anomalies", {})
            lines.append("  status: {0}".format("no anomalies" if not an else "; ".join("{0}: {1}".format(k, ", ".join(v)) for k, v in an.items())))
    return "\n".join(lines)
