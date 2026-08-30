"""FRED time-series fetch (keyless) + trend helpers.

Uses FRED's public CSV endpoint (no API key): fredgraph.csv?id=<series>. Parses
(date, value), skipping FRED's "." missing markers. Trend helpers compute the
change over N months and its DIRECTION and ACCELERATION — because, as the brief
says, the second derivative matters more than the level.

Pure stdlib. The fetch is injectable so tests run offline.
"""

import csv
import io
import subprocess
from datetime import datetime, timedelta

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={id}&cosd={cosd}"


def _default_fetch(url):
    # curl, not urllib: FRED's endpoint hangs under urllib through this env's proxy
    # but responds fine to curl (which also works direct on the host).
    out = subprocess.run(
        ["curl", "-sS", "--http1.1", "--max-time", "60", "-A", "MonitorCLT-market/1.0", url],
        capture_output=True, text=True, timeout=75,
    )
    return out.stdout


def fetch_series(series_id, fetch=None):
    """Return [(date_str, float)] oldest→newest for a FRED series id.
    Limits to recent years (enough for 6/12mo change + acceleration) so large
    daily series don't drag the run."""
    cosd = f"{datetime.now().year - 6}-01-01"
    text = (fetch or _default_fetch)(FRED_CSV.format(id=series_id, cosd=cosd))
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    out = []
    for row in reader:
        if len(row) < 2:
            continue
        d, v = row[0].strip(), row[1].strip()
        if v in (".", "", "NA"):
            continue
        try:
            out.append((d, float(v)))
        except ValueError:
            continue
    return out


def _date(s):
    return datetime.strptime(s[:10], "%Y-%m-%d")


def latest(series):
    return series[-1] if series else None


def value_n_months_ago(series, months):
    """Observation nearest to (latest date − months)."""
    if not series:
        return None
    target = _date(series[-1][0]) - timedelta(days=months * 30.44)
    return min(series, key=lambda r: abs((_date(r[0]) - target).days))


def pct_change(series, months):
    last, past = latest(series), value_n_months_ago(series, months)
    if not last or not past or past[1] == 0 or last[0] == past[0]:
        return None
    return (last[1] - past[1]) / abs(past[1]) * 100.0


def abs_change(series, months):
    last, past = latest(series), value_n_months_ago(series, months)
    if not last or not past or last[0] == past[0]:
        return None
    return last[1] - past[1]


def direction(series, months=6, flat_band=1.0):
    ch = pct_change(series, months)
    if ch is None:
        return "flat"
    return "rising" if ch > flat_band else ("falling" if ch < -flat_band else "flat")


def acceleration(series, months=6):
    """Compare the most-recent N-month change to the prior N-month change.
    Returns 'accelerating' | 'decelerating' | 'steady' — the second derivative."""
    recent = pct_change(series, months)
    if recent is None or len(series) < 3:
        return "steady"
    # prior window: value N ago vs 2N ago
    a, b = value_n_months_ago(series, months), value_n_months_ago(series, 2 * months)
    if not a or not b or b[1] == 0 or a[0] == b[0]:
        return "steady"
    prior = (a[1] - b[1]) / abs(b[1]) * 100.0
    if abs(recent) > abs(prior) + 0.5:
        return "accelerating"
    if abs(recent) < abs(prior) - 0.5:
        return "decelerating"
    return "steady"
