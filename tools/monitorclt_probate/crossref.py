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
  6. capped paths  a parcel reached through a business entity the decedent ran
                   (NC SOS officials) or through a named trustee is held by the
                   entity or the trust, not the estate; such a link is capped at
                   pending by CAP_AT_PENDING and only a signed human review can
                   confirm it

Every link carries its evidence list, so a reviewer sees why, not just how much.
Output rows map 1:1 onto probate.entity_match in schema.sql. Pure stdlib.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict

TOOL_VERSION = "1.1"
HERE = os.path.dirname(os.path.abspath(__file__))

# Evidence labels, strongest first. The first one present names the match tier.
TIER_PRIORITY = (
    "estate_marker_on_owner",
    "deed_grantor_link",
    "mailing_address_match",
    "entity_office_address_match",
    "situs_address_match",
    "entity_official_link",
    "registered_agent_link",
)

# Evidence that forbids auto-confirmation, whatever the score. A parcel reached
# through a business entity or a named trustee is held by the entity or the
# trust; the estate holds at most an interest in it, and whether that interest
# exists is a legal question no records match settles. This is a code constant,
# not a rules key, on purpose: the calibration loop rewards recall, and a cap
# that a rules proposal could remove is not a cap. It is keyed on evidence, not
# on flags, so evaluate.status_at() reproduces it from the link alone and a
# loader that "cleans up" flags cannot lift it.
CAP_AT_PENDING = {
    "entity_official_link": "held_via_entity",
    "registered_agent_link": "held_via_entity",
    "trustee_owner": "held_in_trust",
}


def cap_flags(evidence):
    """Flags that forbid auto-confirmation, derived from the evidence list only."""
    return sorted({CAP_AT_PENDING[e] for e in evidence if e in CAP_AT_PENDING})


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


def _make_name(raw, first, middle, last, suffix, is_org, markers, body, roles=()):
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
        "roles": sorted(roles),
        "entity_key": "",
        "entity_family": "",
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
    # A trustee is a person acting in a role, not an organization: strip the role
    # before the organization check so the person's name enters blocking. The
    # link is capped at pending downstream because the trust, not the estate,
    # holds title. "PUBLIC FAMILY TRUST" still carries TRUST and stays an org.
    body, roles = strip_markers(body, rules.get("role_tokens", []))
    tokens = [t for t in body.replace(",", " , ").split() if t]
    noise = set(rules.get("noise_tokens", []))
    tokens = [t for t in tokens if t not in noise]

    org_tokens = set(rules.get("organization_tokens", []))
    if any(t in org_tokens for t in tokens):
        joined = " ".join(t for t in tokens if t != ",")
        name = _make_name(raw, "", "", "", "", True, markers, joined, roles)
        name["entity_key"], name["entity_family"] = normalize_entity_name(joined, rules)
        return name

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

    return _make_name(raw, first, middle, last, suffix, False, markers, body, roles)


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

    skip = set(rules.get("suffixes", [])) | set(rules.get("role_tokens", []))
    out = [parse_name(parts[0], name_format, rules)]
    for part in parts[1:]:
        name = parse_name(part, name_format, rules)
        primary = out[0]
        tokens = [t for t in part.split() if t not in skip and t != ","]
        if (
            not name["is_organization"]
            and not primary["is_organization"]
            and primary["last"]
            and len(tokens) <= 2
            and not name["markers"]
        ):
            name = _make_name(
                part, tokens[0], " ".join(tokens[1:]), primary["last"], "", False, [], part,
                name["roles"],
            )
        out.append(name)
    # A role is a property of the owner string, not of the token that carried it:
    # "PUBLIC JOHN Q & JANE R TRUSTEES" makes both of them trustees.
    roles = sorted({r for party in out for r in party["roles"]})
    for party in out:
        party["roles"] = roles
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


def normalize_entity_name(raw, rules):
    """Exact-match key for an organization name, plus its legal-form family.

    "Nonesuch Holdings, L.L.C." and "THE NONESUCH HOLDINGS LLC" must meet;
    "SMITH PROPERTIES" and "SMITH PROPERTY" must not. So this strips punctuation
    and legal-form noise (LLC, INC, THE, ...) and nothing else, and remembers
    which family the suffix belonged to so INC-on-the-parcel vs LLC-at-the-SOS
    can be called out. No fuzzy matching, on purpose: a path that can never
    auto-confirm has no business guessing at names.
    """
    body = clean_text(raw).replace(",", " ")
    body = re.sub(r"(?<![A-Z])L L C(?![A-Z])", "LLC", body)
    body = re.sub(r"\s+", " ", body).strip()
    noise = set(rules.get("entity_name_noise_tokens", []))
    families = rules.get("entity_suffix_families", {})
    key_tokens, family = [], ""
    for token in body.split():
        if token == "AND":
            token = "&"
        if token in families:
            family = families[token]
        if token in noise:
            continue
        key_tokens.append(token)
    return " ".join(key_tokens), family


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


def index_organization_parcels(parcels, rules):
    """Block organization-owned parcels by normalized entity name.

    The sibling of index_parcels for the parties it skips. A trust-named owner
    lands here too and is simply never looked up: trusts are not registered with
    the Secretary of State, so no entity key will ever meet it.
    """
    fmt = rules.get("name_formats", {}).get("parcel", "last_first")
    by_key = defaultdict(list)
    for parcel in parcels:
        for party in split_parties(parcel.get("owner_name", ""), fmt, rules):
            if party["is_organization"] and party["entity_key"]:
                by_key[party["entity_key"]].append((parcel, party))
    return by_key


def index_entities(entities, rules):
    """Index Secretary of State business entities by the people who run them.

    by_official[FIRST|LAST] -> [(entity, official)], where official is a parsed
    person name plus person_name (verbatim), title and role_kind ("official" or
    "registered_agent"). Organization-named registered agents (service
    companies) are skipped. Entity names are keyed exactly; a key shared by more
    than one sos_id is ambiguous and is reported as evidence, never resolved by
    guess. frequency counts how many distinct entities name a FIRST+LAST, for
    the common-name penalty on this path.
    """
    fmt = rules.get("name_formats", {}).get("business_entity", "first_last")
    by_official = defaultdict(list)
    entity_keys = {}
    key_owners = defaultdict(set)
    per_name = defaultdict(set)
    for entity in entities or []:
        sos_id = str(entity.get("sos_id") or "")
        key, family = normalize_entity_name(entity.get("entity_name", ""), rules)
        entity_keys[sos_id] = (key, family)
        if key:
            key_owners[key].add(sos_id)
        people = [
            (o.get("person_name", ""), o.get("title", ""), "official")
            for o in entity.get("officials") or []
        ]
        if entity.get("registered_agent_name"):
            people.append((entity["registered_agent_name"], "REGISTERED AGENT", "registered_agent"))
        seen = set()
        for person_name, title, role_kind in people:
            parsed = parse_name(person_name, fmt, rules)
            if parsed["is_organization"] or not (parsed["first"] and parsed["last"]):
                continue
            if (parsed["key_full"], role_kind) in seen:
                continue  # one person, several titles at one entity: one candidate
            seen.add((parsed["key_full"], role_kind))
            official = dict(parsed, person_name=person_name, title=title, role_kind=role_kind)
            by_official[parsed["key_fl"]].append((entity, official))
            per_name[parsed["key_fl"]].add(sos_id)
    frequency = {name: len(ids) for name, ids in per_name.items()}
    return by_official, entity_keys, key_owners, frequency


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
    for a human however high it scores, an organization owner or an
    out-of-county parcel never auto-confirms, and a link reached through an
    entity or a trustee (CAP_AT_PENDING) never auto-confirms either. The review
    floor still applies to capped links: a cap means "cannot be confirmed by a
    rule run", not "must be reviewed".
    """
    thresholds = rules["thresholds"]
    corroborated = [e for e in evidence if e in rules["corroborating_evidence"]]
    caps = cap_flags(evidence)
    if score < thresholds["review_floor"]:
        return "rejected", []
    if (
        not caps
        and score >= thresholds["auto_confirm"]
        and same_county
        and not party["is_organization"]
        and (corroborated or not rules.get("require_corroboration_to_confirm", True))
    ):
        return "confirmed", []
    flags = list(caps)
    if not corroborated:
        flags.append("name_only_needs_human_review")
    return "pending", sorted(flags)


def _score(evidence, rules):
    """Additive score: the name tier's base plus every other label's weight.

    A label without a weight in match_rules.json raises KeyError rather than
    scoring as zero -- an unweighted label is a bug, not a neutral fact.
    """
    weights = rules["evidence"]
    score = rules["name_base"][evidence[0]]
    for label in evidence[1:]:
        score += weights[label]
    return round(max(0.0, min(1.0, score)), 3)


def _shared_evidence(estate, parcel, decedent, rules, frequency, deed_index):
    """Parcel-side evidence both paths share: addresses, deeds, county, common name."""
    evidence, flags = [], []
    estate_address = normalize_address(estate.get("pr_mailing_address"), rules)
    if estate_address:
        if estate_address == normalize_address(parcel.get("owner_mailing_address"), rules):
            evidence.append("mailing_address_match")
        elif estate_address == normalize_address(parcel.get("situs_address"), rules):
            evidence.append("situs_address_match")

    deeds, deed_flags = _deed_evidence(decedent, parcel, estate, deed_index)
    if deeds:
        evidence.append("deed_grantor_link")
    flags.extend(deed_flags)

    same_county = clean_text(estate.get("county")) == clean_text(parcel.get("county"))
    if not same_county:
        evidence.append("county_mismatch")

    if frequency.get(decedent["key_fl"], 0) >= rules["thresholds"]["common_name_parcels"]:
        evidence.append("common_name")
    return evidence, flags, deeds, same_county


def _finish_link(estate, parcel, party, evidence, flags, deeds, same_county, rules, via=None):
    """Score, tier and dispose; build the row that maps onto probate.entity_match."""
    score = _score(evidence, rules)
    tier = next((t for t in TIER_PRIORITY if t in evidence), evidence[0])
    status, disposition_flags = disposition(score, evidence, party, same_county, rules)
    via = via or {}
    return {
        "left_source": "estate_case",
        "left_id": "{0}/{1}".format(estate.get("county"), estate.get("file_number")),
        "right_source": "parcel",
        "right_id": "{0}/{1}".format(parcel.get("county"), parcel.get("pin")),
        "match_tier": tier,
        "score": score,
        "evidence": evidence,
        "flags": sorted(set(flags) | set(disposition_flags)),
        "status": status,
        "via_source": via.get("via_source"),
        "via_id": via.get("via_id"),
        "decedent_name": estate.get("decedent_name"),
        "owner_name": parcel.get("owner_name"),
        "situs_address": parcel.get("situs_address"),
        "assessed_value": parcel.get("assessed_value"),
        "deed_instruments": sorted(
            d.get("instrument_number") or "{0}/{1}".format(d.get("book"), d.get("page"))
            for d in deeds
        ),
        "via_entity": via.get("via_entity"),
    }


def score_link(estate, parcel, decedent, party, rules, frequency, deed_index):
    """Build one candidate link on the direct path: evidence in, score/tier/status out."""
    tier_name, conflict = name_tier(decedent, party)
    evidence = [tier_name]
    if conflict:
        evidence.append(conflict)
    if party["markers"]:
        evidence.append("estate_marker_on_owner")
    if party.get("roles"):
        evidence.append("trustee_owner")  # weight 0; the cap does the work
    if party["is_organization"]:
        evidence.append("organization_owner")
    shared, flags, deeds, same_county = _shared_evidence(
        estate, parcel, decedent, rules, frequency, deed_index
    )
    evidence.extend(shared)
    return _finish_link(estate, parcel, party, evidence, flags, deeds, same_county, rules)


def score_entity_link(
    estate, parcel, party, decedent, entity, official, rules,
    frequency, entity_keys, key_owners, official_frequency, deed_index,
):
    """One candidate link reached through a business entity the decedent ran.

    The official -- a person -- is the party the disposition sees, so the
    organization gate never fires and organization_owner is not evidence here:
    the entity is the mechanism, not a contradiction. CAP_AT_PENDING is what
    holds the link at pending. The registered agent's address is never
    evidence; it is usually a law office.
    """
    sos_id = str(entity.get("sos_id") or "")
    tier_name, conflict = name_tier(decedent, official)
    evidence = [tier_name]
    if conflict:
        evidence.append(conflict)
    evidence.append(
        "entity_official_link" if official["role_kind"] == "official" else "registered_agent_link"
    )

    estate_address = normalize_address(estate.get("pr_mailing_address"), rules)
    entity_addresses = {
        normalize_address(entity.get(k), rules)
        for k in ("principal_office_address", "mailing_address")
    } - {""}
    if estate_address and estate_address in entity_addresses:
        evidence.append("entity_office_address_match")
    parcel_addresses = {
        normalize_address(parcel.get(k), rules) for k in ("owner_mailing_address", "situs_address")
    } - {""}
    if entity_addresses & parcel_addresses:
        evidence.append("entity_address_on_parcel")

    key, family = entity_keys[sos_id]
    if family and party["entity_family"] and family != party["entity_family"]:
        evidence.append("entity_suffix_conflict")
    if len(key_owners.get(key, ())) > 1:
        evidence.append("entity_name_ambiguous")

    shared, flags, deeds, same_county = _shared_evidence(
        estate, parcel, decedent, rules, frequency, deed_index
    )
    evidence.extend(shared)
    if (
        "common_name" not in evidence
        and official_frequency.get(decedent["key_fl"], 0) >= rules["thresholds"]["common_name_entities"]
    ):
        evidence.append("common_name")

    status = clean_text(entity.get("status"))
    if status and not (status.startswith("CURRENT") or status.startswith("ACTIVE")):
        flags.append("entity_not_active")

    via = {
        "via_source": "business_entity",
        "via_id": sos_id,
        "via_entity": {
            "sos_id": sos_id,
            "entity_name": entity.get("entity_name"),
            "entity_status": entity.get("status"),
            "person_name": official["person_name"],
            "title": official["title"],
            "role_kind": official["role_kind"],
        },
    }
    return _finish_link(estate, parcel, official, evidence, flags, deeds, same_county, rules, via)


def _dedupe_links(links):
    """One link per (estate, parcel): probate.entity_match is UNIQUE on the pair.

    The entity path makes duplicates routine -- a decedent who is both Manager
    and registered agent, or an owner string naming him and his LLC. Keep the
    direct link over an entity link, then the higher score, then an official
    over an agent; report what was dropped.
    """
    best = {}
    for link in links:
        key = (link["left_id"], link["right_id"])
        via = link.get("via_entity") or {}
        rank = (
            link["via_source"] is not None,
            -link["score"],
            via.get("role_kind") == "registered_agent",
            link["via_id"] or "",
        )
        if key not in best or rank < best[key][0]:
            best[key] = (rank, link)
    kept = [pair[1] for pair in best.values()]
    return kept, len(links) - len(kept)

def crossref(estates, parcels, deeds, rules, entities=None):
    """Cross-reference every estate case against the parcel, deed and entity indexes."""
    parcel_index, frequency = index_parcels(parcels, rules)
    org_index = index_organization_parcels(parcels, rules)
    deed_index = index_deeds(deeds, rules)
    by_official, entity_keys, key_owners, official_frequency = index_entities(entities, rules)
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
            links.append(score_link(estate, parcel, decedent, party, rules, frequency, deed_index))
        # Entity path: blocking is FIRST+LAST on both sides; the entity key is the
        # join. A same-name official at an entity that owns nothing yields nothing.
        for entity, official in by_official.get(decedent["key_fl"], []):
            key, _ = entity_keys[str(entity.get("sos_id") or "")]
            for parcel, party in org_index.get(key, []):
                links.append(
                    score_entity_link(
                        estate, parcel, party, decedent, entity, official, rules,
                        frequency, entity_keys, key_owners, official_frequency, deed_index,
                    )
                )

    links, collapsed = _dedupe_links(links)
    for link in links:
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
            "business_entities": len(entities or []),
            "collapsed_duplicates": collapsed,
        },
        "matches": links,
        "estates": _rollup(estates, links),
        "review_queue": [link for link in links if link["status"] == "pending"],
        "unmatched_estate_parcels": _orphan_estate_parcels(parcels, matched_pins, rules),
        "skipped_estates": skipped,
    }


def _rollup(estates, links):
    """Per-estate answer to the actual question: does this estate hold real property?

    Capped links are listed apart, as what they are: an interest in an entity or
    a trust that holds the parcel. There is deliberately no boolean for them --
    downstream would read it as has_real_property.
    """
    by_estate = defaultdict(list)
    for link in links:
        by_estate[link["left_id"]].append(link)

    rows = []
    for estate in estates:
        key = "{0}/{1}".format(estate.get("county"), estate.get("file_number"))
        found = by_estate.get(key, [])
        confirmed = [link for link in found if link["status"] == "confirmed"]
        pending = [link for link in found if link["status"] == "pending"]
        capped = [link for link in pending if cap_flags(link["evidence"])]
        direct = [link for link in pending if not cap_flags(link["evidence"])]
        rows.append(
            {
                "left_id": key,
                "decedent_name": estate.get("decedent_name"),
                "personal_rep_name": estate.get("personal_rep_name"),
                "filing_date": estate.get("filing_date"),
                "confirmed_parcels": [link["right_id"] for link in confirmed],
                "pending_parcels": [link["right_id"] for link in direct],
                "pending_via_entity": [
                    {
                        "right_id": link["right_id"],
                        "via_id": link["via_id"],
                        "entity_name": (link.get("via_entity") or {}).get("entity_name"),
                        "role": (link.get("via_entity") or {}).get("title"),
                    }
                    for link in capped
                    if link["via_source"]
                ],
                "pending_in_trust": [link["right_id"] for link in capped if not link["via_source"]],
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
        "  tool {0} / rules {1} | {2} estate cases, {3} parcels, {4} deeds, {5} entities".format(
            run["tool_version"], run["rules_version"], run["estate_cases"], run["parcels"],
            run["deeds"], run.get("business_entities", 0),
        ),
        "",
    ]
    counts = defaultdict(int)
    for link in result["matches"]:
        if link["status"] == "pending" and cap_flags(link["evidence"]):
            counts["capped"] += 1
        else:
            counts[link["status"]] += 1
    lines.append(
        "  candidates: {0} confirmed, {1} pending review, {2} pending via entity/trust, "
        "{3} rejected".format(counts["confirmed"], counts["pending"], counts["capped"], counts["rejected"])
    )
    holding = [row for row in result["estates"] if row["has_real_property"]]
    lines.append(
        "  estates with confirmed real property: {0} of {1}".format(len(holding), len(result["estates"]))
    )
    lines.append("")

    for row in result["estates"]:
        if not (
            row["confirmed_parcels"] or row["pending_parcels"]
            or row["pending_via_entity"] or row["pending_in_trust"]
        ):
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
            via = link.get("via_entity")
            if via:
                lines.append(
                    "        via: {0} (SOS {1}) as {2}".format(
                        via.get("entity_name"), via.get("sos_id"), via.get("title") or via.get("role_kind")
                    )
                )
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
    lines.append(
        "A row flagged held_via_entity or held_in_trust is an interest in the entity or"
    )
    lines.append(
        "trust that holds the parcel, not the parcel -- a question for the estate attorney;"
    )
    lines.append("the matcher never auto-confirms it.")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--estates", required=True, help="estate case records (.jsonl or .json)")
    parser.add_argument("--parcels", required=True, help="parcel/assessor records (.jsonl or .json)")
    parser.add_argument("--deeds", help="recorded deed records (.jsonl or .json)")
    parser.add_argument(
        "--entities", help="NC Secretary of State business entity records (.jsonl or .json)"
    )
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
        entities=load_records(args.entities) if args.entities else [],
    )
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, sort_keys=True)
    if not args.quiet:
        print(format_report(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
