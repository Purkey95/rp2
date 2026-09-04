#!/usr/bin/env python3
"""Read a recorded deed against an estate: did this estate convey that parcel?

One question, asked from two directions. Live monitoring (store.py) asks it of
every deed that lands on a parcel we are tracking, so a lead that has already
left the estate stops being a lead. The backtest (backtest.py) asks it of deeds
recorded *after* a cut-off date, where the answer is an out-of-sample label the
matcher never saw.

The reading is deliberately narrow. A deed conveys *from the estate* only when
the grantor is the personal representative, or the decedent's name carrying an
estate marker ("ESTATE OF", "HEIRS", "EXECUTRIX"), or the decedent's name on a
fiduciary instrument (executor / administrator deed). A plain warranty deed
signed in the decedent's bare name after the date of death is the opposite
finding: a dead person does not sign a warranty deed, so that grantor is a
living namesake and the parcel was never the estate's. Everything else is
"unrelated" and says nothing about this estate. Pure stdlib.
"""

from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import crossref  # noqa: E402  (path must be set first)

# Instrument types under which an estate conveys. Matched against the cleaned,
# upper-cased instrument_type string; "WD" / "QC" / "DEED" deliberately absent.
FIDUCIARY_INSTRUMENT = re.compile(r"EXECUT|ADMINISTRAT|ESTATE|FIDUCIARY|PERSONAL REP|\bPR\b|COMMISSIONER")


def death_or_filing(estate):
    """The date after which a conveyance by the decedent cannot be the decedent's own."""
    return ((estate.get("date_of_death") or estate.get("filing_date")) or "")[:10]


def recorded_after_death(deed, estate):
    cutoff = death_or_filing(estate)
    recorded = (deed.get("recorded_date") or "")[:10]
    return bool(cutoff and recorded and recorded > cutoff)


def estate_people(estate, rules):
    """Parsed decedent and (if on record) personal representative."""
    fmt = rules.get("name_formats", {}).get("estate_case", "first_last")
    decedent = crossref.parse_name(estate.get("decedent_name", ""), fmt, rules)
    rep = None
    if estate.get("personal_rep_name"):
        rep = crossref.parse_name(estate["personal_rep_name"], fmt, rules)
        if rep["is_organization"] or not rep["key_fl"].strip("|"):
            rep = None
    return decedent, rep


def _same_person(party, person):
    """FIRST+LAST agree and the middle names do not contradict each other."""
    if party["is_organization"] or party["key_fl"] != person["key_fl"]:
        return False
    if party["middle"] and person["middle"]:
        return party["middle"] == person["middle"] or party["middle"][0] == person["middle"][0]
    return True


def _middle_conflict(party, person):
    return (
        not party["is_organization"]
        and party["key_fl"] == person["key_fl"]
        and party["middle"]
        and person["middle"]
        and party["middle"][0] != person["middle"][0]
    )


def classify_grantee(grantee_name, decedent, rep, rules):
    """Where the parcel went: to the representative, within the family name, or out."""
    fmt = rules.get("name_formats", {}).get("deed", "last_first")
    parties = crossref.split_parties(grantee_name or "", fmt, rules)
    if not parties:
        return "unknown"
    if rep and any(_same_person(p, rep) for p in parties):
        return "personal_representative"
    if any(not p["is_organization"] and p["last"] and p["last"] == decedent["last"] for p in parties):
        return "shares_decedent_surname"
    if all(p["is_organization"] for p in parties):
        return "organization"
    return "third_party"


def classify_conveyance(deed, estate, rules):
    """How a post-death deed relates to an estate, or None if it does not involve it.

    Returns {"relation": "estate" | "namesake" | "ambiguous", "basis": ...,
    "grantee_relation": ...}. "estate" means the estate conveyed the parcel;
    "namesake" means someone else with the decedent's name did, which is evidence
    the parcel was never the estate's; "ambiguous" is a decedent-name grantor
    whose middle initial contradicts the estate record -- surfaced, never acted on.
    """
    if not recorded_after_death(deed, estate):
        return None
    decedent, rep = estate_people(estate, rules)
    if not decedent["key_fl"].strip("|"):
        return None

    fmt = rules.get("name_formats", {}).get("deed", "last_first")
    parties = crossref.split_parties(deed.get("grantor_name", ""), fmt, rules)
    if not parties:
        return None

    rep_party = rep is not None and any(_same_person(p, rep) for p in parties)
    decedent_parties = [p for p in parties if _same_person(p, decedent)]
    conflicting = [p for p in parties if _middle_conflict(p, decedent)]
    marked = any(p["markers"] for p in decedent_parties)
    fiduciary = bool(FIDUCIARY_INSTRUMENT.search(crossref.clean_text(deed.get("instrument_type"))))

    if rep_party:
        relation, basis = "estate", "grantor_is_personal_representative"
    elif decedent_parties and marked:
        relation, basis = "estate", "estate_marker_on_decedent_grantor"
    elif decedent_parties and fiduciary:
        relation, basis = "estate", "fiduciary_instrument_from_decedent"
    elif decedent_parties:
        relation, basis = "namesake", "post_death_deed_in_bare_decedent_name"
    elif conflicting:
        relation, basis = "ambiguous", "middle_initial_conflict"
    else:
        return None

    return {
        "relation": relation,
        "basis": basis,
        "grantee_relation": classify_grantee(deed.get("grantee_name"), decedent, rep, rules),
        "recorded_date": (deed.get("recorded_date") or "")[:10],
        "instrument_type": deed.get("instrument_type"),
        "grantor_name": deed.get("grantor_name"),
        "grantee_name": deed.get("grantee_name"),
        "instrument": deed_key(deed),
    }


def deed_key(deed):
    """Stable identity for a deed row: county + instrument number, else book/page."""
    county = crossref.clean_text(deed.get("county"))
    if deed.get("instrument_number"):
        return "{0}/{1}".format(county, crossref.clean_text(deed["instrument_number"]).replace(" ", ""))
    return "{0}/{1}/{2}".format(county, crossref.clean_text(deed.get("book")), crossref.clean_text(deed.get("page")))


def deed_parcel_key(deed, default_county=None):
    """The 'COUNTY/PIN' key a deed points at, or None when no PIN is printed."""
    if not deed.get("parcel_pin"):
        return None
    return crossref.pin_key(deed.get("county") or default_county, deed["parcel_pin"])
