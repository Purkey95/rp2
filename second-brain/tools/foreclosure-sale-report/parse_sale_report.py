#!/usr/bin/env python3
"""Parse the Mecklenburg DAILY REPORT OF SALE FILE into structured records.

Source: Carolina Data Integration's daily email. It reports what happened AT the
foreclosure auction — high bidder, bid amount, and the upset-bid deadline — which
is the window MonitorCLT is currently blind to. Notices are captured before the
sale; trustee deeds are captured weeks after it. This is the middle.

    python3 parse_sale_report.py REPORT.pdf            # table
    python3 parse_sale_report.py REPORT.pdf --json     # records
    python3 parse_sale_report.py REPORT.pdf --stats    # daily clearing stats
    python3 parse_sale_report.py REPORT.csv            # preferred: the CSV attachment

Prefer the CSV/TAB attachment the vendor sends alongside the PDF. Parsing a PDF
whose columns interleave is a fallback, not a plan.
"""

import argparse
import csv
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

FIELDS = ["File #", "SALE DATE", "FILE TYPE", "FIRST NAME", "HIGH BIDDER",
          "LAST NAME", "BID AMT", "ADDRESS", "UPSET AMT", "CITY STATE",
          "UPSET DEADLINE", "ZIP", "AMT FOR DEPOSIT", "TAX VALUE", "PICTURE LINK"]

# A high bidder that is a lender means the property went back to the bank and
# becomes REO in 60-90 days. A third-party win means it left the pipeline.
LENDER_TOKENS = ("loan services", "loandepot", "mortgage", "bank", "n.a.",
                 "federal national", "fannie", "freddie", "hud", "secretary of",
                 "credit union", "servicing", "trustee of", "capital one")

MONEY = re.compile(r"\$\s*([\d,]*\.?\d*)")
SPATIALEST = re.compile(r"property/(\d+)")


def money(text):
    if not text:
        return None
    match = MONEY.search(text)
    if not match or not match.group(1).strip(" ,"):
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def parse_pdf(path):
    """Extract records from the PDF. Needs pdfminer.six."""
    from pdfminer.high_level import extract_text
    from pdfminer.layout import LAParams
    # boxes_flow=None keeps positional reading order, which is what un-interleaves
    # the two-column layout. With the default flow the labels and values scramble.
    text = extract_text(str(path), laparams=LAParams(boxes_flow=None, line_margin=0.3))

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    records, current = [], {}
    i = 0
    while i < len(lines):
        line = lines[i]
        label = line.rstrip(":").strip()
        if line.endswith(":") and label in [f.rstrip(":") for f in FIELDS]:
            value = lines[i + 1] if i + 1 < len(lines) else ""
            # A label immediately followed by another label means an empty value.
            if value.endswith(":") and value.rstrip(":").strip() in [f.rstrip(":") for f in FIELDS]:
                value = ""
                i += 1
            else:
                i += 2
            if label == "File #" and current:
                records.append(current)
                current = {}
            current[label] = value
            continue
        i += 1
    if current:
        records.append(current)
    return [normalize(r) for r in records if r.get("File #")]


def parse_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        delim = "\t" if "\t" in sample.splitlines()[0] else ","
        return [normalize(row) for row in csv.DictReader(handle, delimiter=delim)]


def normalize(raw):
    """Map vendor field names onto MonitorCLT-shaped keys."""
    bid = money(raw.get("BID AMT"))
    tax = money(raw.get("TAX VALUE"))
    bidder = (raw.get("HIGH BIDDER") or "").strip()
    low = bidder.lower()

    phone = None
    phone_match = re.search(r"(\d{3}[-.\s]?\d{3}[-.\s]?\d{4})", bidder)
    if phone_match:
        phone = phone_match.group(1)
        bidder = bidder[:phone_match.start()].strip()

    link = raw.get("PICTURE LINK") or ""
    parcel = SPATIALEST.search(link)

    return {
        "sp_case": (raw.get("File #") or "").strip(),
        "file_type": (raw.get("FILE TYPE") or "").strip(),
        "sale_date": (raw.get("SALE DATE") or "").strip(),
        "owner_first": (raw.get("FIRST NAME") or "").strip(),
        "owner_last": (raw.get("LAST NAME") or "").strip(),
        "address": (raw.get("ADDRESS") or "").strip(),
        "city_state": (raw.get("CITY STATE") or "").strip(),
        "zip": (raw.get("ZIP") or "").strip(),
        "high_bidder": bidder,
        "high_bidder_phone": phone,
        "bidder_is_lender": any(tok in low for tok in LENDER_TOKENS),
        "bid_amount": bid,
        "upset_amount": money(raw.get("UPSET AMT")),
        "upset_deadline": (raw.get("UPSET DEADLINE") or "").strip(),
        "deposit_required": money(raw.get("AMT FOR DEPOSIT")),
        "tax_value": tax,
        "bid_to_tax_ratio": round(bid / tax, 4) if bid and tax else None,
        "mecklenburg_parcel_id": parcel.group(1) if parcel else None,
        "picture_link": link,
    }


def days_left(deadline, today=None):
    try:
        end = datetime.strptime(deadline, "%m/%d/%Y").date()
    except (ValueError, TypeError):
        return None
    return (end - (today or date.today())).days


def stats(records):
    priced = [r for r in records if r["bid_to_tax_ratio"]]
    ratios = sorted(r["bid_to_tax_ratio"] for r in priced)
    lenders = [r for r in records if r["bidder_is_lender"]]
    third = [r for r in records if not r["bidder_is_lender"]]

    bidders = {}
    for record in third:
        if record["high_bidder"]:
            bidders[record["high_bidder"]] = bidders.get(record["high_bidder"], 0) + 1

    return {
        "records": len(records),
        "third_party_wins": len(third),
        "lender_takebacks": len(lenders),
        "third_party_pct": round(100 * len(third) / len(records), 1) if records else None,
        "median_bid_to_tax": ratios[len(ratios) // 2] if ratios else None,
        "total_bid_volume": round(sum(r["bid_amount"] or 0 for r in records), 2),
        "repeat_buyers": {k: v for k, v in sorted(bidders.items(), key=lambda x: -x[1]) if v > 1},
        "reo_pipeline_next_90d": len(lenders),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--today", help="YYYY-MM-DD, for deterministic day counts")
    args = parser.parse_args()

    path = Path(args.path)
    records = parse_csv(path) if path.suffix.lower() in (".csv", ".tab", ".tsv") else parse_pdf(path)
    today = datetime.strptime(args.today, "%Y-%m-%d").date() if args.today else None

    if args.json:
        print(json.dumps(records, indent=2))
        return 0
    if args.stats:
        print(json.dumps(stats(records), indent=2))
        return 0

    print(f"{len(records)} sale records\n")
    header = f"{'CASE':<17}{'TYPE':<7}{'BID':>12}{'TAX VAL':>12}{'RATIO':>7}  {'DEADLINE':<11}{'DAYS':>5}  HIGH BIDDER"
    print(header)
    print("-" * len(header))
    for r in records:
        left = days_left(r["upset_deadline"], today)
        ratio = f"{r['bid_to_tax_ratio']*100:.0f}%" if r["bid_to_tax_ratio"] else "—"
        tag = " [lender]" if r["bidder_is_lender"] else ""
        print(f"{r['sp_case']:<17}{r['file_type']:<7}"
              f"{(r['bid_amount'] or 0):>12,.0f}{(r['tax_value'] or 0):>12,.0f}{ratio:>7}  "
              f"{r['upset_deadline']:<11}{(left if left is not None else '—'):>5}  "
              f"{r['high_bidder']}{tag}")
        print(f"{'':17}{r['address']}, {r['zip']}  ·  owner {r['owner_first']} {r['owner_last']}".rstrip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
