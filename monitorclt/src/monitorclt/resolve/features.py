"""Pairwise evidence between a subject mention and a candidate mention.

Every feature is a 0/1 fact a reviewer can check against the two records, named
so that the evidence list reads as English. New evidence is a new function here and
a weight in rules.json; nothing else changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..normalize.addresses import compare
from ..normalize.names import Name, name_agreement, suffix_conflict

CORROBORATING_DEFAULT = (
    "estate_marker_on_candidate",
    "mailing_address_match",
    "situs_address_match",
    "deed_grantor_link",
    "deed_grantee_link",
    "related_party_on_candidate",
)


@dataclass
class PairContext:
    """Everything features may look at. Built once per candidate by the resolver."""

    subject: Name
    candidate: Name
    subject_record: Dict[str, Any]
    candidate_record: Dict[str, Any]
    subject_county: str
    candidate_county: str
    subject_address: Optional[str] = None  # normalized address that travels with the subject (e.g. PR mailing)
    candidate_mailing: Optional[str] = None
    candidate_situs: Optional[str] = None
    related_keys: List[str] = field(default_factory=list)  # key_fl of parties related to the subject (personal rep)
    candidate_party_keys: List[str] = field(default_factory=list)  # key_fl of all parties on the candidate record
    deeds_as_grantor: List[Dict[str, Any]] = field(default_factory=list)  # deeds on this parcel naming subject as grantor
    deeds_as_grantee: List[Dict[str, Any]] = field(default_factory=list)
    name_frequency: int = 0
    common_name_threshold: int = 8


def compute(ctx: PairContext) -> Dict[str, float]:
    f: Dict[str, float] = {}
    agreement = name_agreement(ctx.subject, ctx.candidate)
    f["name_full_exact"] = 1.0 if agreement == "full" else 0.0
    f["name_first_last_only"] = 1.0 if agreement == "first_last" else 0.0
    f["middle_initial_conflict"] = 1.0 if agreement == "conflict" else 0.0
    f["suffix_conflict"] = 1.0 if suffix_conflict(ctx.subject, ctx.candidate) else 0.0
    f["estate_marker_on_candidate"] = 1.0 if ctx.candidate.markers else 0.0

    mailing = compare(ctx.subject_address, ctx.candidate_mailing) if ctx.subject_address else "none"
    situs = compare(ctx.subject_address, ctx.candidate_situs) if ctx.subject_address else "none"
    f["mailing_address_match"] = 1.0 if mailing == "exact" else 0.0
    f["situs_address_match"] = 1.0 if mailing != "exact" and situs == "exact" else 0.0
    f["street_address_match"] = 1.0 if mailing != "exact" and situs != "exact" and "street" in (mailing, situs) else 0.0

    f["deed_grantor_link"] = 1.0 if ctx.deeds_as_grantor else 0.0
    f["deed_grantee_link"] = 1.0 if ctx.deeds_as_grantee else 0.0
    others = [k for k in ctx.candidate_party_keys if k != ctx.subject.key_fl]
    f["related_party_on_candidate"] = 1.0 if any(k in others for k in ctx.related_keys) else 0.0

    f["organization_candidate"] = 1.0 if ctx.candidate.is_organization else 0.0
    f["county_mismatch"] = 1.0 if ctx.subject_county != ctx.candidate_county else 0.0
    f["common_name"] = 1.0 if ctx.name_frequency >= ctx.common_name_threshold else 0.0
    return f


def evidence_list(features: Dict[str, float]) -> List[str]:
    """The human-readable rationale: every feature that fired, strongest positives first."""
    order = [
        "name_full_exact",
        "name_first_last_only",
        "estate_marker_on_candidate",
        "deed_grantor_link",
        "mailing_address_match",
        "related_party_on_candidate",
        "deed_grantee_link",
        "situs_address_match",
        "street_address_match",
        "middle_initial_conflict",
        "suffix_conflict",
        "organization_candidate",
        "county_mismatch",
        "common_name",
    ]
    return [k for k in order if features.get(k)] + [k for k in sorted(features) if features.get(k) and k not in order]


def flags_for(ctx: PairContext, features: Dict[str, float]) -> List[str]:
    """Things a reviewer should know that are not evidence for or against identity."""
    out: List[str] = []
    death = str(ctx.subject_record.get("date_of_death") or "")[:10]
    if death:
        for deed in ctx.deeds_as_grantor:
            recorded = str(deed.get("recorded_date") or "")[:10]
            if recorded and recorded > death:
                out.append("post_death_conveyance")
                break
    if ctx.subject_address and ctx.subject_address.startswith("PO BOX"):
        out.append("subject_address_is_po_box")
    return sorted(set(out))
