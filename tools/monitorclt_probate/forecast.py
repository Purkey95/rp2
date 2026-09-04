#!/usr/bin/env python3
"""Pipeline forecast: how many targets to expect, how long they last, what that costs.

Not a prediction of which estates will file -- that is the Clerk's business -- but
the arithmetic a marketing budget needs, measured from the store rather than
guessed. Three rates come out of the history:

  arrival    estate filings per period, by county
  yield      the share of filings the matcher turns into a confirmed lead, and the
             share that lands in review instead
  retirement the share of active leads that leave each period (the estate conveyed
             the parcel), which sets how long a lead stays worth contacting

and the projection is those rates run forward: expected new leads per period,
the active-lead inventory they accumulate into, and, given a cost per lead, per
review, or per active lead-period, the spend. The filings assumption is the
trailing mean of complete periods with the observed min/max as the band; pass
--filings to use a number from the Clerk instead. Everything is reported with
its denominator, because on a small store the rates are wide.

    python3 forecast.py --db monitorclt.sqlite --periods 6 --cost-per-lead 45
    python3 forecast.py --db monitorclt.sqlite --csv pipeline.csv --html pipeline.html

Pure stdlib.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import store  # noqa: E402  (path must be set first)

TOOL_VERSION = "1.0"


# ---------------------------------------------------------------- periods ---


def period_of(day, period):
    """'2026-03-14' -> '2026-03' (month) or '2026-W11' (ISO week)."""
    if not day:
        return None
    date = dt.date.fromisoformat(day[:10])
    if period == "week":
        year, week, _ = date.isocalendar()
        return "{0}-W{1:02d}".format(year, week)
    return date.strftime("%Y-%m")


def next_period(label, period):
    if period == "week":
        year, week = label.split("-W")
        date = dt.date.fromisocalendar(int(year), int(week), 1) + dt.timedelta(days=7)
        return period_of(date.isoformat(), period)
    year, month = (int(x) for x in label.split("-"))
    return "{0:04d}-{1:02d}".format(year + (month == 12), month % 12 + 1)


def periods_between(first, last, period):
    out, label = [], first
    while label <= last:
        out.append(label)
        label = next_period(label, period)
    return out


def days_in(period):
    return 7.0 if period == "week" else 365.25 / 12


# ---------------------------------------------------------------- history ---


def _estates(conn, county=None):
    where, args = "", []
    if county:
        where, args = " WHERE county = ?", [county]
    return [dict(r) for r in conn.execute("SELECT * FROM estate_case{0} ORDER BY filing_date, left_id".format(where), args)]


def _outcomes(conn):
    """Per estate: when it first had a confirmed pair, and when its last confirmed pair left."""
    first_confirmed = {}
    for r in conn.execute("""SELECT o.left_id, min(r.as_of) AS day FROM match_observation o JOIN match_run r ON r.id = o.run_id
           WHERE o.status = 'confirmed' GROUP BY o.left_id"""):
        first_confirmed[r["left_id"]] = r["day"]
    ever_pending = {r["left_id"] for r in conn.execute("SELECT DISTINCT left_id FROM match_observation WHERE status = 'pending'")}

    # Retired = every pair that was ever confirmed is now transferred. The deed's
    # own recording date is the honest retirement date when we have it.
    retired = {}
    for left_id in first_confirmed:
        pairs = [
            dict(r)
            for r in conn.execute(
                """SELECT m.right_id, m.status, m.transferred_as_of FROM entity_match m
                   WHERE m.left_id = ?
                     AND EXISTS (SELECT 1 FROM match_observation o WHERE o.left_id = m.left_id AND o.right_id = m.right_id AND o.status = 'confirmed')""",
                (left_id,),
            )
        ]
        if pairs and all(p["status"] == "transferred" for p in pairs):
            days = []
            for p in pairs:
                deed_day = conn.execute(
                    "SELECT json_extract(detail, '$.recorded_date') AS d FROM parcel_transfer"
                    " WHERE left_id = ? AND right_id = ? AND kind = 'deed_from_estate' ORDER BY d LIMIT 1",
                    (left_id, p["right_id"]),
                ).fetchone()
                days.append((deed_day["d"] if deed_day and deed_day["d"] else None) or p["transferred_as_of"])
            retired[left_id] = max(days)
    return first_confirmed, ever_pending, retired


def history(conn, period="month", county=None):
    """One row per period from the first filing to the last pull, with the funnel counts."""
    estates = _estates(conn, county)
    first_confirmed, ever_pending, retired = _outcomes(conn)
    last_run = conn.execute("SELECT max(as_of) AS d, min(as_of) AS f FROM match_run").fetchone()
    if not estates or not last_run["d"]:
        return [], {"observed_through": last_run["d"], "first_pull": last_run["f"]}

    rows = defaultdict(lambda: {"filings": 0, "lead_estates": 0, "review_only_estates": 0, "no_candidate_estates": 0, "retired": 0})
    for e in estates:
        label = period_of(e["filing_date"], period)
        if label is None:
            continue
        rows[label]["filings"] += 1
        if e["left_id"] in first_confirmed:
            rows[label]["lead_estates"] += 1
        elif e["left_id"] in ever_pending:
            rows[label]["review_only_estates"] += 1
        else:
            rows[label]["no_candidate_estates"] += 1
    for left_id, day in retired.items():
        rows[period_of(day, period)]["retired"] += 1

    new_parcels = defaultdict(int)
    for r in conn.execute("""SELECT m.first_seen_as_of, count(*) AS n FROM entity_match m
           WHERE EXISTS (SELECT 1 FROM match_observation o WHERE o.left_id = m.left_id AND o.right_id = m.right_id AND o.status = 'confirmed')
           GROUP BY 1"""):
        new_parcels[period_of(r["first_seen_as_of"], period)] += r["n"]

    first = min(rows)
    last = period_of(last_run["d"], period)
    out, active = [], 0
    for label in periods_between(first, max(last, max(rows)), period):
        row = dict(rows[label])
        active += row["lead_estates"] - row["retired"]
        row.update(
            {
                "period": label,
                "new_confirmed_parcels": new_parcels.get(label, 0),
                "active_lead_estates": active,
                "lead_yield": _ratio(row["lead_estates"], row["filings"]),
                "complete": label < last,
            }
        )
        out.append(row)
    return out, {"observed_through": last_run["d"], "first_pull": last_run["f"]}


def _ratio(n, d):
    return round(n / d, 4) if d else None


# ------------------------------------------------------------------ rates ---


def rates(conn, hist, period="month", county=None):
    """The three rates, each with the count it rests on."""
    estates = _estates(conn, county)
    first_confirmed, ever_pending, retired = _outcomes(conn)
    filed = [e for e in estates if e.get("filing_date")]
    leads = [e for e in filed if e["left_id"] in first_confirmed]
    review = [e for e in filed if e["left_id"] not in first_confirmed and e["left_id"] in ever_pending]

    complete = [r for r in hist if r["complete"]]
    filings = [r["filings"] for r in complete]

    # Retirement hazard: retirements per active lead-period of exposure.
    observed_through = dt.date.fromisoformat(max(r["as_of"] for r in conn.execute("SELECT as_of FROM match_run")))
    exposure_days, lifetimes = 0.0, []
    for e in leads:
        start = dt.date.fromisoformat(first_confirmed[e["left_id"]][:10])
        end_day = retired.get(e["left_id"])
        end = dt.date.fromisoformat(end_day[:10]) if end_day else observed_through
        exposure_days += max((end - start).days, 1)
        if end_day:
            lifetimes.append((end - dt.date.fromisoformat(e["filing_date"][:10])).days)
    exposure = exposure_days / days_in(period)
    lags = [(dt.date.fromisoformat(first_confirmed[e["left_id"]][:10]) - dt.date.fromisoformat(e["filing_date"][:10])).days for e in leads]

    return {
        "filings_per_period": {
            "mean": round(sum(filings) / len(filings), 2) if filings else None,
            "min": min(filings) if filings else None,
            "max": max(filings) if filings else None,
            "complete_periods": len(filings),
        },
        "lead_yield": {"rate": _ratio(len(leads), len(filed)), "numerator": len(leads), "denominator": len(filed)},
        "review_yield": {"rate": _ratio(len(review), len(filed)), "numerator": len(review), "denominator": len(filed)},
        "parcels_per_lead_estate": _ratio(
            conn.execute("SELECT count(DISTINCT left_id || '|' || right_id) FROM match_observation WHERE status = 'confirmed'").fetchone()[0], len(leads)
        ),
        "retirement_per_period": {
            "rate": _ratio(len(retired), exposure) if exposure else None,
            "retired": len(retired),
            "lead_periods_observed": round(exposure, 2),
        },
        "median_days_filing_to_lead": _median(lags),
        "median_days_filing_to_retired": _median(lifetimes),
        "lag_note": "filing-to-lead lag is bounded below by pull cadence; run pulls more often to tighten it",
    }


def _median(values):
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


# ------------------------------------------------------------- projection ---


def project(hist, rate_table, periods=6, period="month", filings=None, cost_per_lead=0.0, cost_per_review=0.0, cost_per_active=0.0):
    """Run the rates forward. Low/base/high follow the filings band; the rates are held fixed."""
    fp = rate_table["filings_per_period"]
    base = filings if filings is not None else fp["mean"]
    if base is None:
        return []
    low = filings if filings is not None else fp["min"]
    high = filings if filings is not None else fp["max"]
    lead_yield = rate_table["lead_yield"]["rate"] or 0.0
    review_yield = rate_table["review_yield"]["rate"] or 0.0
    hazard = rate_table["retirement_per_period"]["rate"] or 0.0
    hazard = min(hazard, 1.0)

    inventory = {"low": None, "base": None, "high": None}
    start_inventory = hist[-1]["active_lead_estates"] if hist else 0
    label = hist[-1]["period"] if hist else period_of(dt.date.today().isoformat(), period)
    out = []
    for _ in range(periods):
        label = next_period(label, period)
        row = {"period": label}
        for name, arrivals in (("low", low), ("base", base), ("high", high)):
            prev = start_inventory if inventory[name] is None else inventory[name]
            new_leads = arrivals * lead_yield
            reviews = arrivals * review_yield
            active = prev * (1 - hazard) + new_leads
            inventory[name] = active
            row[name] = {
                "filings": round(arrivals, 1),
                "new_leads": round(new_leads, 1),
                "reviews": round(reviews, 1),
                "active_leads": round(active, 1),
                "retired": round(prev * hazard, 1),
                "spend": round(new_leads * cost_per_lead + reviews * cost_per_review + active * cost_per_active, 2),
            }
        out.append(row)
    return out


# ----------------------------------------------------------------- report ---


def _pct(value):
    return "  n/a " if value is None else "{0:5.1f}%".format(value * 100)


def format_report(fc):
    r, hist, proj = fc["rates"], fc["history"], fc["projection"]
    lines = [
        "MonitorCLT pipeline forecast -- by {0}, observed through {1}".format(fc["period"], fc["observed_through"]),
        "",
        "HISTORY (estate filings and what became of them)",
        "  period    filings  leads  review  none  new parcels  retired  active  yield",
    ]
    for row in hist:
        lines.append(
            "  {0:<8}  {1:>7}  {2:>5}  {3:>6}  {4:>4}  {5:>11}  {6:>7}  {7:>6}  {8}{9}".format(
                row["period"],
                row["filings"],
                row["lead_estates"],
                row["review_only_estates"],
                row["no_candidate_estates"],
                row["new_confirmed_parcels"],
                row["retired"],
                row["active_lead_estates"],
                _pct(row["lead_yield"]),
                "" if row["complete"] else "  (partial)",
            )
        )
    fp = r["filings_per_period"]
    lines.extend(
        [
            "",
            "RATES (with the counts they rest on)",
            "  filings per {0:<6} {1}  (min {2}, max {3}, over {4} complete periods)".format(
                fc["period"], fp["mean"], fp["min"], fp["max"], fp["complete_periods"]
            ),
            "  lead yield         {0}  ({1} of {2} filings produced a confirmed parcel)".format(
                _pct(r["lead_yield"]["rate"]), r["lead_yield"]["numerator"], r["lead_yield"]["denominator"]
            ),
            "  review yield       {0}  ({1} of {2} landed in review only)".format(
                _pct(r["review_yield"]["rate"]), r["review_yield"]["numerator"], r["review_yield"]["denominator"]
            ),
            "  parcels per lead   {0}".format(r["parcels_per_lead_estate"]),
            "  retirement/period  {0}  ({1} retired over {2} lead-periods observed)".format(
                _pct(r["retirement_per_period"]["rate"]), r["retirement_per_period"]["retired"], r["retirement_per_period"]["lead_periods_observed"]
            ),
            "  filing -> lead     median {0} days  ({1})".format(r["median_days_filing_to_lead"], r["lag_note"]),
            "  filing -> retired  median {0} days".format(r["median_days_filing_to_retired"]),
            "",
        ]
    )
    if not proj:
        lines.append("PROJECTION: no complete period of filings yet -- load more pulls or pass --filings.")
        return "\n".join(lines)
    costed = any(row["base"]["spend"] for row in proj)
    lines.append(
        "PROJECTION (rates held fixed; low/base/high follow the filings band{0})".format(", " + fc["filings_assumption"] if fc["filings_assumption"] else "")
    )
    lines.append("  period    filings   new leads   reviews   retired   active leads{0}".format("   spend (low / base / high)" if costed else ""))
    for row in proj:
        b = row["base"]
        lines.append(
            "  {0:<8}  {1:>7}  {2:>10}  {3:>8}  {4:>8}  {5:>13}{6}".format(
                row["period"],
                b["filings"],
                b["new_leads"],
                b["reviews"],
                b["retired"],
                "{0} - {1} - {2}".format(row["low"]["active_leads"], b["active_leads"], row["high"]["active_leads"]),
                "   {0:,.0f} / {1:,.0f} / {2:,.0f}".format(row["low"]["spend"], b["spend"], row["high"]["spend"]) if costed else "",
            )
        )
    if costed:
        total = {k: sum(row[k]["spend"] for row in proj) for k in ("low", "base", "high")}
        lines.append("  total spend over {0} periods: {1:,.0f} / {2:,.0f} / {3:,.0f}".format(len(proj), total["low"], total["base"], total["high"]))
    lines.append("")
    lines.append("Rates measured on {0} filings. Treat the band as the honest width, not the".format(r["lead_yield"]["denominator"]))
    lines.append("decimals as precision; every loaded pull narrows it.")
    return "\n".join(lines)


def write_csv(path, fc):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            ["kind", "period", "scenario", "filings", "lead_estates", "review", "no_candidate", "new_confirmed_parcels", "retired", "active_leads", "spend"]
        )
        for row in fc["history"]:
            w.writerow(
                [
                    "actual",
                    row["period"],
                    "",
                    row["filings"],
                    row["lead_estates"],
                    row["review_only_estates"],
                    row["no_candidate_estates"],
                    row["new_confirmed_parcels"],
                    row["retired"],
                    row["active_lead_estates"],
                    "",
                ]
            )
        for row in fc["projection"]:
            for scenario in ("low", "base", "high"):
                s = row[scenario]
                w.writerow(
                    ["forecast", row["period"], scenario, s["filings"], s["new_leads"], s["reviews"], "", "", s["retired"], s["active_leads"], s["spend"]]
                )


def write_html(path, fc):
    """A self-contained page: the same numbers, laid out to be read rather than parsed."""
    r, hist, proj = fc["rates"], fc["history"], fc["projection"]
    peak = max([row["active_lead_estates"] for row in hist] + [row["high"]["active_leads"] for row in proj] + [1])

    def bar(value, cls):
        width = max(2, int(round(100 * value / peak))) if value else 0
        return '<div class="bar {0}" style="width:{1}%"></div>'.format(cls, width)

    def cell(v):
        return html.escape("" if v is None else str(v))

    rows = []
    for row in hist:
        rows.append(
            "<tr class='actual'><td>{0}{1}</td><td>{2}</td><td>{3}</td><td>{4}</td><td>{5}</td><td>{6}</td>"
            "<td>{7}</td><td class='bar-cell'>{8}<span>{9}</span></td><td></td></tr>".format(
                cell(row["period"]),
                "" if row["complete"] else " <small>partial</small>",
                row["filings"],
                row["lead_estates"],
                row["review_only_estates"],
                row["new_confirmed_parcels"],
                row["retired"],
                _pct(row["lead_yield"]).strip(),
                bar(row["active_lead_estates"], "actual"),
                row["active_lead_estates"],
            )
        )
    for row in proj:
        b = row["base"]
        rows.append(
            "<tr class='forecast'><td>{0}</td><td>{1}</td><td>{2}</td><td>{3}</td><td></td><td>{4}</td>"
            "<td></td><td class='bar-cell'>{5}<span>{6} <small>({7}–{8})</small></span></td><td>{9}</td></tr>".format(
                cell(row["period"]),
                b["filings"],
                b["new_leads"],
                b["reviews"],
                b["retired"],
                bar(b["active_leads"], "forecast"),
                b["active_leads"],
                row["low"]["active_leads"],
                row["high"]["active_leads"],
                "{0:,.0f}".format(b["spend"]) if b["spend"] else "",
            )
        )
    fp = r["filings_per_period"]
    kpis = [
        ("Filings / {0}".format(fc["period"]), fp["mean"], "min {0} · max {1} · {2} complete periods".format(fp["min"], fp["max"], fp["complete_periods"])),
        ("Lead yield", _pct(r["lead_yield"]["rate"]).strip(), "{0} of {1} filings".format(r["lead_yield"]["numerator"], r["lead_yield"]["denominator"])),
        (
            "Review yield",
            _pct(r["review_yield"]["rate"]).strip(),
            "{0} of {1} filings".format(r["review_yield"]["numerator"], r["review_yield"]["denominator"]),
        ),
        (
            "Retirement / {0}".format(fc["period"]),
            _pct(r["retirement_per_period"]["rate"]).strip(),
            "{0} retired over {1} lead-periods".format(r["retirement_per_period"]["retired"], r["retirement_per_period"]["lead_periods_observed"]),
        ),
        ("Filing → lead", "{0} d".format(r["median_days_filing_to_lead"]), "median; bounded by pull cadence"),
        ("Active leads now", hist[-1]["active_lead_estates"] if hist else 0, "estates with a confirmed parcel still in the estate"),
    ]
    total = {k: sum(row[k]["spend"] for row in proj) for k in ("low", "base", "high")} if proj else None
    page = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>MonitorCLT pipeline forecast</title>
<style>
body{{font:14px/1.45 system-ui,sans-serif;margin:2rem auto;max-width:72rem;padding:0 1rem;color:#1a1a1a;background:#fafaf8}}
h1{{font-size:1.4rem;margin:0 0 .25rem}} .sub{{color:#666;margin:0 0 1.5rem}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(11rem,1fr));gap:.75rem;margin-bottom:1.5rem}}
.kpi{{background:#fff;border:1px solid #e4e2dc;border-radius:.5rem;padding:.75rem 1rem}}
.kpi b{{display:block;font-size:1.5rem;font-weight:600}}
.kpi span{{color:#666;font-size:.8rem}}
table{{border-collapse:collapse;width:100%;background:#fff;border:1px solid #e4e2dc}}
th,td{{padding:.4rem .6rem;text-align:right;border-bottom:1px solid #eee;white-space:nowrap}}
th:first-child,td:first-child{{text-align:left}}
th{{background:#f3f1ec;font-weight:600}} tr.forecast td{{color:#555;font-style:italic}} tr.forecast td:first-child::before{{content:"→ ";color:#999}}
.bar-cell{{min-width:14rem;text-align:left}}
.bar{{display:inline-block;height:.75rem;vertical-align:middle;margin-right:.4rem;border-radius:2px}}
.bar.actual{{background:#3b6ea5}} .bar.forecast{{background:#b7c9dd}}
small{{color:#888}} .note{{color:#666;font-size:.85rem;margin-top:1rem}} .total{{margin-top:.75rem;font-weight:600}}
</style></head><body>
<h1>MonitorCLT pipeline forecast</h1>
<p class="sub">By {period}, observed through {through}. Rates measured from the store; projection holds them fixed and follows the filings band.{assumption}</p>
<div class="kpis">{kpis}</div>
<table><thead><tr><th>period</th><th>filings</th><th>lead estates</th><th>review</th><th>new parcels</th>
<th>retired</th><th>yield</th><th>active leads</th><th>spend (base)</th></tr></thead>
<tbody>{rows}</tbody></table>
{total}
<p class="note">Lead = an estate with at least one confirmed parcel still in the estate.
Retired = every confirmed parcel has since been conveyed.
Projected rows are italic; the band in parentheses follows the observed min/max filings.
Rates rest on {n} filings — every loaded pull narrows the band.</p>
</body></html>""".format(
        period=fc["period"],
        through=cell(fc["observed_through"]),
        assumption=" " + html.escape(fc["filings_assumption"]) + "." if fc["filings_assumption"] else "",
        kpis="".join("<div class='kpi'>{0}<b>{1}</b><span>{2}</span></div>".format(cell(k), cell(v), cell(s)) for k, v, s in kpis),
        rows="".join(rows),
        total=(
            "<p class='total'>Projected spend over {0} periods: {1:,.0f} base (band {2:,.0f} – {3:,.0f})</p>".format(
                len(proj), total["base"], total["low"], total["high"]
            )
            if total and total["base"]
            else ""
        ),
        n=r["lead_yield"]["denominator"],
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)


def forecast(conn, period="month", county=None, periods=6, filings=None, cost_per_lead=0.0, cost_per_review=0.0, cost_per_active=0.0):
    hist, meta = history(conn, period, county)
    rate_table = rates(conn, hist, period, county) if hist else None
    proj = project(hist, rate_table, periods, period, filings, cost_per_lead, cost_per_review, cost_per_active) if rate_table else []
    return {
        "tool_version": TOOL_VERSION,
        "period": period,
        "county": county,
        "observed_through": meta["observed_through"],
        "filings_assumption": "filings fixed at {0} per {1} (given)".format(filings, period) if filings is not None else "",
        "history": hist,
        "rates": rate_table,
        "projection": proj,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True)
    parser.add_argument("--period", choices=("month", "week"), default="month")
    parser.add_argument("--county")
    parser.add_argument("--periods", type=int, default=6, help="how many periods to project (default 6)")
    parser.add_argument("--filings", type=float, help="override the filings-per-period assumption (e.g. from the Clerk's own counts)")
    parser.add_argument("--cost-per-lead", type=float, default=0.0, help="spend per new confirmed lead estate (first outreach)")
    parser.add_argument("--cost-per-review", type=float, default=0.0, help="spend per estate landing in review (reviewer time)")
    parser.add_argument("--cost-per-active", type=float, default=0.0, help="spend per active lead per period (ongoing touches)")
    parser.add_argument("--json", dest="json_out")
    parser.add_argument("--csv", dest="csv_out")
    parser.add_argument("--html", dest="html_out")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    conn = store.connect(args.db)
    fc = forecast(conn, args.period, args.county, args.periods, args.filings, args.cost_per_lead, args.cost_per_review, args.cost_per_active)
    conn.close()
    if not fc["history"]:
        print("nothing loaded yet: run load_run.py first")
        return 1
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(fc, f, indent=2, sort_keys=True)
    if args.csv_out:
        write_csv(args.csv_out, fc)
    if args.html_out:
        write_html(args.html_out, fc)
    if not args.quiet:
        print(format_report(fc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
