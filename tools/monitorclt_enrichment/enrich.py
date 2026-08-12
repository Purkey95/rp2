#!/usr/bin/env python3
"""MonitorCLT contact-enrichment engine — Step 1: the parcel join.

Takes your CRM contacts and your Regrid/parcel table and, for every contact,
finds the matching parcel and writes the owner name, owner mailing address,
acreage, zoning, land use, and an absentee flag onto the contact. This is the
free step: it converts "no-contact" records into MAILABLE records using data you
already own, before any paid skip tracing.

It then splits the output three ways:
  - enriched_contacts.csv  : every contact with parcel fields merged in
  - mail_merge.csv         : mailable records (name + deliverable address), deduped
  - skip_trace_queue.csv   : mailable but still missing phone AND email -> Step 2
  - metrics.json           : MonitorCLT-style counts and rates for the dashboard

Matching strategy, highest-confidence first:
  1. APN exact           (both sides carry an assessor parcel number)
  2. Situs address + zip  (normalized)
  3. Owner name + zip     (normalized; backfills where address entry is messy)

Pure stdlib. Wire the load_* / write_* functions to Postgres on the host; the
CSV versions here let you run and validate the whole flow today.

Usage:
    python3 enrich.py --contacts sample/contacts.csv \\
                      --parcels  sample/parcels.csv \\
                      --outdir   out
"""

import argparse
import csv
import json
import os

from normalize import normalize_address, normalize_name, is_absentee

# Fields the join writes onto a contact when a parcel match is found.
PARCEL_FIELDS = [
    "owner_name", "mail_street", "mail_city", "mail_state", "mail_zip",
    "acreage", "zoning", "land_use",
]


def load_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_parcel_indexes(parcels):
    """Three lookup dicts, one per match strategy."""
    by_apn, by_addr, by_name = {}, {}, {}
    for p in parcels:
        apn = (p.get("apn") or "").strip()
        if apn:
            by_apn.setdefault(apn, p)
        addr_key = normalize_address(p.get("situs_street"), p.get("situs_zip"))
        if addr_key:
            by_addr.setdefault(addr_key, p)
        name_key = normalize_name(p.get("owner_name"))
        z = (p.get("situs_zip") or p.get("mail_zip") or "").strip()[:5]
        if name_key and z:
            by_name.setdefault(f"{name_key}|{z}", p)
    return by_apn, by_addr, by_name


def match_parcel(contact, idx):
    by_apn, by_addr, by_name = idx
    apn = (contact.get("apn") or "").strip()
    if apn and apn in by_apn:
        return by_apn[apn], "apn"
    addr_key = normalize_address(contact.get("situs_street"), contact.get("situs_zip"))
    if addr_key and addr_key in by_addr:
        return by_addr[addr_key], "situs_address"
    name_key = normalize_name(
        contact.get("full_name")
        or (f"{contact.get('first_name','')} {contact.get('last_name','')}").strip()
        or contact.get("owner_name")
    )
    z = (contact.get("situs_zip") or contact.get("mail_zip") or "").strip()[:5]
    if name_key and z and f"{name_key}|{z}" in by_name:
        return by_name[f"{name_key}|{z}"], "owner_name"
    return None, None


def has_value(*vals):
    return any((v or "").strip() for v in vals)


def enrich(contacts, parcels):
    idx = build_parcel_indexes(parcels)
    enriched, mail_rows, skip_rows = [], [], []
    seen_mail_keys = set()
    stats = {
        "total": len(contacts), "matched": 0,
        "by_apn": 0, "by_situs_address": 0, "by_owner_name": 0,
        "name_backfilled": 0, "mail_backfilled": 0,
        "absentee": 0, "mailable": 0, "contactable": 0, "needs_skip_trace": 0,
    }

    for c in contacts:
        row = dict(c)
        parcel, method = match_parcel(c, idx)
        row["match_method"] = method or ""

        if parcel:
            stats["matched"] += 1
            stats[f"by_{method}"] += 1
            # Backfill only empty fields — never overwrite CRM-entered data.
            if not has_value(row.get("owner_name")) and parcel.get("owner_name"):
                row["owner_name"] = parcel["owner_name"]
                stats["name_backfilled"] += 1
            had_mail = has_value(row.get("mail_street"))
            for fld in ("mail_street", "mail_city", "mail_state", "mail_zip",
                        "acreage", "zoning", "land_use"):
                if not has_value(row.get(fld)) and parcel.get(fld):
                    row[fld] = parcel[fld]
            if not had_mail and has_value(row.get("mail_street")):
                stats["mail_backfilled"] += 1
            absentee = is_absentee(
                row.get("situs_zip"), row.get("situs_street"),
                row.get("mail_zip"), row.get("mail_street"),
            )
            row["absentee"] = "" if absentee is None else ("yes" if absentee else "no")
            if absentee:
                stats["absentee"] += 1

        contactable = has_value(row.get("phone"), row.get("email"))
        mailable = has_value(row.get("owner_name") or row.get("full_name")) and \
            has_value(row.get("mail_street")) and has_value(row.get("mail_city")) and \
            has_value(row.get("mail_state")) and has_value(row.get("mail_zip"))
        row["contactable"] = "yes" if contactable else "no"
        row["mailable"] = "yes" if mailable else "no"
        row["needs_skip_trace"] = "yes" if (mailable and not contactable) else "no"

        if contactable:
            stats["contactable"] += 1
        if mailable:
            stats["mailable"] += 1
            key = normalize_address(row.get("mail_street"), row.get("mail_zip"))
            if key not in seen_mail_keys:
                seen_mail_keys.add(key)
                mail_rows.append(row)
        if mailable and not contactable:
            stats["needs_skip_trace"] += 1
            skip_rows.append(row)

        enriched.append(row)

    return enriched, mail_rows, skip_rows, stats


def write_csv(path, rows):
    if not rows:
        # still create the file with a header-less placeholder so downstream jobs don't crash
        open(path, "w", encoding="utf-8").close()
        return
    fields = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def monitorclt_metrics(stats):
    """Shape the stats into the metric names MonitorCLT should track daily."""
    total = stats["total"] or 1
    return {
        "enrichment.match_rate": round(stats["matched"] / total * 100, 2),
        "enrichment.mailable_pct": round(stats["mailable"] / total * 100, 2),
        "enrichment.contactable_pct": round(stats["contactable"] / total * 100, 2),
        "enrichment.absentee_count": stats["absentee"],
        "enrichment.name_backfilled": stats["name_backfilled"],
        "enrichment.skip_trace_queue": stats["needs_skip_trace"],
        "_raw": stats,
    }


def main():
    ap = argparse.ArgumentParser(description="MonitorCLT parcel-join contact enrichment (Step 1)")
    ap.add_argument("--contacts", required=True)
    ap.add_argument("--parcels", required=True)
    ap.add_argument("--outdir", default="out")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    contacts = load_csv(args.contacts)
    parcels = load_csv(args.parcels)

    enriched, mail_rows, skip_rows, stats = enrich(contacts, parcels)

    write_csv(os.path.join(args.outdir, "enriched_contacts.csv"), enriched)
    write_csv(os.path.join(args.outdir, "mail_merge.csv"), mail_rows)
    write_csv(os.path.join(args.outdir, "skip_trace_queue.csv"), skip_rows)
    metrics = monitorclt_metrics(stats)
    with open(os.path.join(args.outdir, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)

    print(f"Contacts processed : {stats['total']}")
    print(f"Parcel matched     : {stats['matched']} "
          f"({stats['by_apn']} apn / {stats['by_situs_address']} addr / {stats['by_owner_name']} name)")
    print(f"Names backfilled   : {stats['name_backfilled']}")
    print(f"Absentee owners    : {stats['absentee']}")
    print(f"Mailable now       : {stats['mailable']}  ->  mail_merge.csv "
          f"({len(mail_rows)} after dedupe)")
    print(f"Contactable now    : {stats['contactable']}  (contactable_pct = "
          f"{metrics['enrichment.contactable_pct']}%)")
    print(f"Needs skip trace   : {stats['needs_skip_trace']}  ->  skip_trace_queue.csv (Step 2)")
    print(f"Metrics written    : {os.path.join(args.outdir, 'metrics.json')}")


if __name__ == "__main__":
    main()
