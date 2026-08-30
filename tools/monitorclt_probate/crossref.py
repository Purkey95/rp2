#!/usr/bin/env python3
"""Probate -> real property cross-reference. Which estates hold real estate, and where.

Probate-first, by design. The lead is an *estate case*: a decedent, a file number,
and a personal representative on record with the Clerk of Superior Court. This tool
answers the next question -- does that estate appear to hold real property, and
which parcels -- by resolving the decedent against the county parcel and deed
indexes. It never runs the other direction (person -> estate), and it takes no
criminal-justice input at all; see README.md.

Matching is deterministic and staged, because a shared name is not evidence:

  1. blocking      candidate parcels are those whose owner string carries a party
                   with the decedent's FIRST + LAST -- nothing weaker enters
  2. name tier     full (first + middle) exact, or first/last only
  3. corroboration estate marker on the owner string ("ESTATE OF", "HEIRS"), the
                   estate's mailing address on the parcel, or a recorded deed
                   naming the decedent as grantor of that PIN
  4. contradiction middle-initial conflict, organization owner, wrong county, or a
                   surname+forename common enough in the parcel index to be noise
  5. disposition   confirmed / pending / rejected -- and a name-only match can
                   never be confirmed, however high it scores

Every link carries its evidence list, so a reviewer sees why, not just how much.
Output rows map 1:1 onto probate.entity_match in db/migrations/. Pure stdlib.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict

TOOL_VERSION = "1.0"
HERE = os.path.dirname(os.path.abspath(__file__))

# Evidence labels, strongest first. The first one present names the match tier.
TIER_PRIORITY = (
    "estate_marker_on_owner",
    "deed_grantor_link",
    "mailing_address_match",
    "situs_address_match",
)


# --------------------------------------------------------------------- io ---


def load_rules(path=None):
    with open(path or os.path.join(HERE, "match_rules.json"), encoding="utf-8") as f:
        return json.load(f)


def load_records(path):
    """Read a .jsonl (one object per line) or .json (array) file of records."""
    with open(path, encoding="utf-8") as f:
        text = f.read().strip()
    if not text:
        return []
    if text[0] == "[":
        return json.loads(text)
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("//"):
            out.append(json.loads(line))
    return out


# ------------------------------------------------------------ normalizing ---


def clean_text(value):
    """Uppercase, drop intra-word punctuation, collapse whitespace.

    Hyphens/apostrophes are deleted rather than spaced (O'BRIEN -> OBRIEN,
    SMITH-JONES -> SMITHJONES) so a compound surname stays one token. Both sides
    of a comparison go through this, so the transformation only has to be
    consistent, not pretty.
    """
    s = (value or "").upper()
    s = re.sub(r"[.'‘’`\-]", "", s)
    s = s.replace("&", " & ")
    s = re.sub(r"[^A-Z0-9,& ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def strip_markers(body, markers):
    """Remove probate markers ("ESTATE OF", "HEIRS", ...) and report which were there.

    Longest first, so "HEIRS OF" is consumed before "HEIRS" can claim half of it.
    """
    found = []
    for marker in sorted(markers, key=len, reverse=True):
        pattern = r"(?<![A-Z0-9])" + re.escape(marker) + r"(?![A-Z0-9])"
        if re.search(pattern, body):
            found.append(marker)
            body = re.sub(pattern, " ", body)
    return re.sub(r"\s+", " ", body).strip(), sorted(found)


def _make_name(raw, first, middle, last, suffix, is_org, markers, body):
    first, middle, last = first.strip(), middle.strip(), last.strip()
    return {
        "raw": raw,
        "clean": body,
        "first": first,
        "middle": middle,
        "last": last,
        "middle_initial": middle[0] if middle else "",
        "suffix": suffix,
        "is_organization": is_org,
        "markers": markers,
        "normalized": " ".join(t for t in (first, middle, last) if t) or body,
        "key_full": "|".join((first, middle, last)),
        "key_fl": "|".join((first, last)),
    }


def parse_name(raw, name_format, rules):
    """Parse one party string into first / middle / last, honoring the source's order.

    name_format is the *source's* convention: assessor and register-of-deeds rows
    are "LAST FIRST MIDDLE", court filings are "FIRST MIDDLE LAST". A comma always
    wins ("PUBLIC, JOHN Q"), and an "ESTATE OF"-style prefix flips the remainder
    to natural order, because that phrasing never precedes a surname-first name.
    """
    body = clean_text(raw)
    body, markers = strip_markers(body, rules.get("estate_markers", []))
    tokens = [t for t in body.replace(",", " , ").split() if t]
    noise = set(rules.get("noise_tokens", []))
    tokens = [t for t in tokens if t not in noise]

    org_tokens = set(rules.get("organization_tokens", []))
    if any(t in org_tokens for t in tokens):
        joined = " ".join(t for t in tokens if t != ",")
        return _make_name(raw, "", "", "", "", True, markers, joined)

    suffixes = set(rules.get("suffixes", []))
    suffix = ""
    while len(tokens) > 2 and tokens[-1] in suffixes:
        suffix = suffix or tokens[-1]
        tokens.pop()

    if any(m.endswith(" OF") for m in markers):
        name_format = "first_last"

    if "," in tokens:
        cut = tokens.index(",")
        last_tokens, rest = tokens[:cut], tokens[cut + 1:]
        last = " ".join(last_tokens)
        first = rest[0] if rest else ""
        middle = " ".join(rest[1:])
    elif len(tokens) < 2:
        last, first, middle = (tokens[0] if tokens else ""), "", ""
    elif name_format == "last_first":
        last, first, middle = tokens[0], tokens[1], " ".join(tokens[2:])
    else:
        last, first, middle = tokens[-1], tokens[0], " ".join(tokens[1:-1])

    return _make_name(raw, first, middle, last, suffix, False, markers, body)


def split_parties(raw, name_format, rules):
    """Split a multi-owner string into parties, inheriting an implied surname.

    "PUBLIC JOHN Q & MARY B" is two people, and the second one's surname is only
    printed once. A trailing fragment of one or two tokens is read as forenames
    under the first party's surname; anything longer is parsed on its own.
    """
    body = clean_text(raw)
    parts = [p.strip() for p in re.split(r"\s*&\s*|\s+AND\s+|\s*;\s*", body) if p.strip()]
    if not parts:
        return []

    suffixes = set(rules.get("suffixes", []))
    out = [parse_name(parts[0], name_format, rules)]
    for part in parts[1:]:
        name = parse_name(part, name_format, rules)
        primary = out[0]
        tokens = [t for t in part.split() if t not in suffixes and t != ","]
        if (
            not name["is_organization"]
            and not primary["is_organization"]
            and primary["last"]
            and len(tokens) <= 2
            and not name["markers"]
        ):
            name = _make_name(
                part, tokens[0], " ".join(tokens[1:]), primary["last"], "", False, [], part
            )
        out.append(name)
    return out


def normalize_address(raw, rules):
    """Canonical form for address comparison: unit noise out, street words abbreviated."""
    body = clean_text(raw).replace(",", " ")
    body = re.sub(r"\s+", " ", body).strip()
    if not body:
        return ""
    mapping = rules.get("street_suffixes", {})
    tokens = []
    for token in body.split():
        if re.fullmatch(r"\d{5}\d{4}", token):  # ZIP+4 written without the hyphen
            token = token[:5]
        tokens.append(mapping.get(token, token))
    return " ".join(tokens)


def pin_key(county, pin):
    return "{0}/{1}".format(clean_text(county), re.sub(r"[^A-Z0-9]", "", clean_text(pin)))


# --------------------------------------------------------------- indexing ---


def index_parcels(parcels, rules):
    """Block parcels by owner FIRST|LAST, and count how common each such name is."""
    fmt = rules.get("name_formats", {}).get("parcel", "last_first")
    by_name = defaultdict(list)
    frequency = defaultdict(set)
    for parcel in parcels:
        for party in split_parties(parcel.get("owner_name", ""), fmt, rules):
            if party["is_organization"] or not party["key_fl"].strip("|"):
                continue
            by_name[party["key_fl"]].append((parcel, party))
            frequency[party["key_fl"]].add(pin_key(parcel.get("county"), parcel.get("pin")))
    return by_name, {k: len(v) for k, v in frequency.items()}


def index_deeds(deeds, rules):
    """Block deeds by grantor FIRST|LAST -- the side that conveys away."""
    fmt = rules.get("name_formats", {}).get("deed", "last_first")
    by_name = defaultdict(list)
    for deed in deeds or []:
        for party in split_parties(deed.get("grantor_name", ""), fmt, rules):
            if party["is_organization"] or not party["key_fl"].strip("|"):
                continue
            by_name[party["key_fl"]].append(deed)
    return by_name


# --------------------------------------------------------------- matching ---


def name_tier(decedent, party):
    """Name-only strength, plus a contradiction if the middle names disagree."""
    if decedent["middle"] and party["middle"]:
        if decedent["middle"] == party["middle"] or decedent["middle"][0] == party["middle"][0]:
            return "name_full_exact", None
        return "name_first_last_only", "middle_initial_conflict"
    return "name_first_last_only", None


def _deed_evidence(decedent, parcel, estate, deed_index):
    """Deeds naming the decedent as grantor of this PIN, and what they imply."""
    target = pin_key(parcel.get("county"), parcel.get("pin"))
    death = (estate.get("date_of_death") or "")[:10]
    linked, flags = [], []
    for deed in deed_index.get(decedent["key_fl"], []):
        if not deed.get("parcel_pin"):
            continue
        if pin_key(deed.get("county") or parcel.get("county"), deed["parcel_pin"]) != target:
            continue
        linked.append(deed)
        recorded = (deed.get("recorded_date") or "")[:10]
        if death and recorded and recorded > death:
            flags.append("post_death_conveyance")
    return linked, sorted(set(flags))


def disposition(score, evidence, party, same_county, rules):
    """Turn a score plus its evidence into confirmed / pending / rejected.

    Confirmation is gated, not just thresholded: a name-only match is a question
    for a human however high it scores, and an organization owner or an
    out-of-county parcel never auto-confirms.
    """
    thresholds = rules["thresholds"]
    corroborated = [e for e in evidence if e in rules["corroborating_evidence"]]
    if score < thresholds["review_floor"]:
        return "rejected", []
    if (
        score >= thresholds["auto_confirm"]
        and same_county
        and not party["is_organization"]
        and (corroborated or not rules.get("require_corroboration_to_confirm", True))
    ):
        return "confirmed", []
    return "pending", [] if corroborated else ["name_only_needs_human_review"]


def score_link(estate, parcel, decedent, party, rules, frequency, deed_index):
    """Build one candidate link: evidence in, score/tier/status out."""
    weights = rules["evidence"]
    thresholds = rules["thresholds"]
    evidence, flags = [], []

    tier_name, conflict = name_tier(decedent, party)
    score = rules["name_base"][tier_name]
    evidence.append(tier_name)
    if conflict:
        evidence.append(conflict)
        score += weights[conflict]

    if party["markers"]:
        evidence.append("estate_marker_on_owner")
        score += weights["estate_marker_on_owner"]

    estate_address = normalize_address(estate.get("pr_mailing_address"), rules)
    if estate_address:
        if estate_address == normalize_address(parcel.get("owner_mailing_address"), rules):
            evidence.append("mailing_address_match")
            score += weights["mailing_address_match"]
        elif estate_address == normalize_address(parcel.get("situs_address"), rules):
            evidence.append("situs_address_match")
            score += weights["situs_address_match"]

    deeds, deed_flags = _deed_evidence(decedent, parcel, estate, deed_index)
    if deeds:
        evidence.append("deed_grantor_link")
        score += weights["deed_grantor_link"]
    flags.extend(deed_flags)

    if party["is_organization"]:
        evidence.append("organization_owner")
        score += weights["organization_owner"]

    same_county = clean_text(estate.get("county")) == clean_text(parcel.get("county"))
    if not same_county:
        evidence.append("county_mismatch")
        score += weights["county_mismatch"]

    if frequency.get(decedent["key_fl"], 0) >= thresholds["common_name_parcels"]:
        evidence.append("common_name")
        score += weights["common_name"]

    score = round(max(0.0, min(1.0, score)), 3)
    tier = next((t for t in TIER_PRIORITY if t in evidence), tier_name)
    status, disposition_flags = disposition(score, evidence, party, same_county, rules)
    flags.extend(disposition_flags)

    return {
        "left_source": "estate_case",
        "left_id": "{0}/{1}".format(estate.get("county"), estate.get("file_number")),
        "right_source": "parcel",
        "right_id": "{0}/{1}".format(parcel.get("county"), parcel.get("pin")),
        "match_tier": tier,
        "score": score,
        "evidence": evidence,
        "flags": sorted(set(flags)),
        "status": status,
        "decedent_name": estate.get("decedent_name"),
        "owner_name": parcel.get("owner_name"),
        "situs_address": parcel.get("situs_address"),
        "assessed_value": parcel.get("assessed_value"),
        "deed_instruments": sorted(
            d.get("instrument_number") or "{0}/{1}".format(d.get("book"), d.get("page"))
            for d in deeds
        ),
    }


def crossref(estates, parcels, deeds, rules):
    """Cross-reference every estate case against the parcel and deed indexes."""
    parcel_index, frequency = index_parcels(parcels, rules)
    deed_index = index_deeds(deeds, rules)
    estate_fmt = rules.get("name_formats", {}).get("estate_case", "first_last")

    links, skipped, matched_pins = [], [], set()
    for estate in estates:
        decedent = parse_name(estate.get("decedent_name", ""), estate_fmt, rules)
        if decedent["is_organization"] or not (decedent["first"] and decedent["last"]):
            skipped.append(
                {
                    "left_id": "{0}/{1}".format(estate.get("county"), estate.get("file_number")),
                    "decedent_name": estate.get("decedent_name"),
                    "reason": "decedent name not parseable into first + last",
                }
            )
            continue
        for parcel, party in parcel_index.get(decedent["key_fl"], []):
            link = score_link(estate, parcel, decedent, party, rules, frequency, deed_index)
            links.append(link)
            if link["status"] != "rejected":
                matched_pins.add(link["right_id"])

    links.sort(key=lambda link: (link["left_id"], -link["score"], link["right_id"]))
    return {
        "run": {
            "tool_version": TOOL_VERSION,
            "rules_version": rules.get("version"),
            "estate_cases": len(estates),
            "parcels": len(parcels),
            "deeds": len(deeds or []),
        },
        "matches": links,
        "estates": _rollup(estates, links),
        "review_queue": [link for link in links if link["status"] == "pending"],
        "unmatched_estate_parcels": _orphan_estate_parcels(parcels, matched_pins, rules),
        "skipped_estates": skipped,
    }


def _rollup(estates, links):
    """Per-estate answer to the actual question: does this estate hold real property?"""
    by_estate = defaultdict(list)
    for link in links:
        by_estate[link["left_id"]].append(link)

    rows = []
    for estate in estates:
        key = "{0}/{1}".format(estate.get("county"), estate.get("file_number"))
        found = by_estate.get(key, [])
        confirmed = [link for link in found if link["status"] == "confirmed"]
        pending = [link for link in found if link["status"] == "pending"]
        rows.append(
            {
                "left_id": key,
                "decedent_name": estate.get("decedent_name"),
                "personal_rep_name": estate.get("personal_rep_name"),
                "filing_date": estate.get("filing_date"),
                "confirmed_parcels": [link["right_id"] for link in confirmed],
                "pending_parcels": [link["right_id"] for link in pending],
                "has_real_property": bool(confirmed),
                "assessed_value_confirmed": round(
                    sum(float(link["assessed_value"] or 0) for link in confirmed), 2
                ),
            }
        )
    rows.sort(key=lambda row: row["left_id"])
    return rows


def _orphan_estate_parcels(parcels, matched_pins, rules):
    """Parcels whose owner string says "ESTATE OF"/"HEIRS" with no estate case matched.

    Usually means the estate file is in a county or date range we have not pulled --
    a gap in the input, worth surfacing rather than silently dropping.
    """
    fmt = rules.get("name_formats", {}).get("parcel", "last_first")
    rows = []
    for parcel in parcels:
        key = "{0}/{1}".format(parcel.get("county"), parcel.get("pin"))
        if key in matched_pins:
            continue
        markers = sorted(
            {m for party in split_parties(parcel.get("owner_name", ""), fmt, rules) for m in party["markers"]}
        )
        if markers:
            rows.append(
                {
                    "right_id": key,
                    "owner_name": parcel.get("owner_name"),
                    "situs_address": parcel.get("situs_address"),
                    "markers": markers,
                }
            )
    rows.sort(key=lambda row: row["right_id"])
    return rows


# ----------------------------------------------------------------- report ---


def format_report(result):
    run = result["run"]
    lines = [
        "MonitorCLT probate -> property cross-reference",
        "  tool {0} / rules {1} | {2} estate cases, {3} parcels, {4} deeds".format(
            run["tool_version"], run["rules_version"], run["estate_cases"], run["parcels"], run["deeds"]
        ),
        "",
    ]
    counts = defaultdict(int)
    for link in result["matches"]:
        counts[link["status"]] += 1
    lines.append(
        "  candidates: {0} confirmed, {1} pending review, {2} rejected".format(
            counts["confirmed"], counts["pending"], counts["rejected"]
        )
    )
    holding = [row for row in result["estates"] if row["has_real_property"]]
    lines.append(
        "  estates with confirmed real property: {0} of {1}".format(len(holding), len(result["estates"]))
    )
    lines.append("")

    for row in result["estates"]:
        if not (row["confirmed_parcels"] or row["pending_parcels"]):
            continue
        lines.append("{0}  {1}".format(row["left_id"], row["decedent_name"]))
        if row["personal_rep_name"]:
            lines.append("    representative: {0}".format(row["personal_rep_name"]))
        for link in result["matches"]:
            if link["left_id"] != row["left_id"] or link["status"] == "rejected":
                continue
            lines.append(
                "    [{0:<9}] {1:.3f} {2:<26} {3}".format(
                    link["status"], link["score"], link["right_id"], link["situs_address"] or ""
                )
            )
            lines.append("        owner: {0}".format(link["owner_name"]))
            lines.append("        tier: {0} | evidence: {1}".format(link["match_tier"], ", ".join(link["evidence"])))
            if link["flags"]:
                lines.append("        flags: {0}".format(", ".join(link["flags"])))
        lines.append("")

    if result["skipped_estates"]:
        lines.append("skipped estate cases:")
        for row in result["skipped_estates"]:
            lines.append("    {0}  {1} -- {2}".format(row["left_id"], row["decedent_name"], row["reason"]))
        lines.append("")

    if result["unmatched_estate_parcels"]:
        lines.append("estate-marked parcels with no matching estate case (input gap):")
        for row in result["unmatched_estate_parcels"]:
            lines.append("    {0}  {1}".format(row["right_id"], row["owner_name"]))
        lines.append("")

    lines.append(
        "Confirmed is a records match, not a conclusion: verify chain of title, liens,"
    )
    lines.append(
        "heirs, and the representative's authority before any outreach, and contact the"
    )
    lines.append("personal representative or estate attorney -- nobody else.")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--estates", required=True, help="estate case records (.jsonl or .json)")
    parser.add_argument("--parcels", required=True, help="parcel/assessor records (.jsonl or .json)")
    parser.add_argument("--deeds", help="recorded deed records (.jsonl or .json)")
    parser.add_argument("--rules", help="matching rules (default: match_rules.json beside this script)")
    parser.add_argument("--json", dest="json_out", help="write the full result as JSON to this path")
    parser.add_argument("--quiet", action="store_true", help="suppress the text report")
    args = parser.parse_args(argv)

    rules = load_rules(args.rules)
    result = crossref(
        load_records(args.estates),
        load_records(args.parcels),
        load_records(args.deeds) if args.deeds else [],
        rules,
    )
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, sort_keys=True)
    if not args.quiet:
        print(format_report(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
