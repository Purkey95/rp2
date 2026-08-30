"""Caller-ID → parcel + Owner Distress Score resolution (the screen pop).

Normalizes the caller's number, finds the matching contact, and joins to the
scored lead so whoever answers sees who's calling and how hot the property is
before they say hello. Backed by CSV/JSONL for the framework; wire load_* to the
live CRM `contacts` + `leads` tables on the host.
"""

import csv
import json
import re


def normalize_phone(raw):
    """US phone -> last-10-digit key. Handles +1, spaces, dashes, parens, leading 1."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits[-10:] if len(digits) >= 10 else ""


class CallerLookup:
    """contacts: rows with at least `phone` and `apn`. leads: {apn: lead-dict}."""

    def __init__(self, contacts, leads):
        self.by_phone = {}
        for c in contacts:
            key = normalize_phone(c.get("phone", ""))
            if key:
                self.by_phone.setdefault(key, c)
        self.leads = {str(k): v for k, v in leads.items()}

    def resolve(self, caller_number):
        """Return the screen-pop dict for an inbound caller."""
        key = normalize_phone(caller_number)
        pop = {"caller": caller_number, "phone": key, "matched": False}
        contact = self.by_phone.get(key)
        if not contact:
            return pop
        pop["matched"] = True
        pop["owner"] = contact.get("owner") or contact.get("owner_name") or ""
        pop["apn"] = contact.get("apn", "")
        lead = self.leads.get(str(contact.get("apn", "")))
        if lead:
            pop["score"] = lead.get("score")
            pop["band"] = lead.get("band")
            pop["situs_address"] = lead.get("situs_address", "")
            pop["owner"] = pop["owner"] or lead.get("owner", "")
            pop["top_signals"] = [s.get("signal") for s in lead.get("signals", [])][:5]
        else:
            pop["note"] = "contact matched but no scored lead for its parcel"
        return pop


def load_contacts_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_leads_jsonl(path):
    leads = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                r = json.loads(line)
                leads[str(r.get("apn"))] = r
    return leads
