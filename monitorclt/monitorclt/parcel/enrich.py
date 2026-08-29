"""Enrichment lookups over the parcel index.

The person-matching entry point deliberately returns **candidates with
evidence**, never a match with a confidence percentage. Rationale in
``second-brain/wiki/entity-resolution-for-property-records.md``: a false
positive here means contacting the wrong family about a death, so precision
dominates recall and the promotion decision stays with a caller who can see
the evidence.
"""

from __future__ import annotations

import sqlite3
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

from ..normalize.address import normalize_address
from ..normalize.owner import NAME_SUFFIXES, PersonName
from ..normalize.text import clean, tokens
from .store import ParcelStore


class MatchTier(object):
    """How much corroboration a candidate has. Not a probability."""

    STRONG = "STRONG"
    MODERATE = "MODERATE"
    WEAK = "WEAK"


class Candidate(NamedTuple):
    """A possible person-to-parcel match.

    There is intentionally no ``is_match`` field and no numeric score. Every
    candidate, including a ``STRONG`` one, requires review before it is treated
    as a match.
    """

    parcel_key: str
    owner_raw: str
    situs_raw: Optional[str]
    matched_name: str
    tier: str
    evidence: Tuple[str, ...]

    @property
    def requires_review(self) -> bool:
        return True


def parse_person_query(name: str) -> PersonName:
    """Parse a human-entered name in natural order ("John A. Smith").

    This is the reverse of the county's storage order (surname first), so it
    gets its own parser rather than reusing the CAMA column parser.
    """
    parts = tokens(name)
    suffix: Optional[str] = None
    if len(parts) > 1 and parts[-1] in NAME_SUFFIXES:
        suffix = parts[-1]
        parts = parts[:-1]
    if not parts:
        return PersonName("", "", None)
    if len(parts) == 1:
        return PersonName(parts[0], "", suffix)
    return PersonName(parts[-1], " ".join(parts[:-1]), suffix)


class ParcelIndex:
    """Read-side API over a loaded :class:`ParcelStore`."""

    def __init__(self, store: ParcelStore) -> None:
        self.store = store

    # --- Property-keyed lookups (cheap, no entity resolution) ---------------

    def by_parcel_key(self, parcel_key: str) -> Optional[sqlite3.Row]:
        rows = self.store.query("SELECT * FROM parcels WHERE parcel_key = ?", (parcel_key,))
        return rows[0] if rows else None

    def by_address(self, raw_address: str) -> List[sqlite3.Row]:
        """All parcels at a street address.

        Returns a list, not a single row: condominium buildings put many
        parcels at one street address, so an address is not a unique key.
        """
        normalized = normalize_address(raw_address)
        if normalized.is_empty:
            return []
        if normalized.unit:
            rows = self.store.query(
                "SELECT * FROM parcels WHERE situs_key_with_unit = ?", (normalized.key_with_unit,)
            )
            if rows:
                return rows
        return self.store.query(
            "SELECT * FROM parcels WHERE situs_key = ? ORDER BY parcel_key", (normalized.key,)
        )

    def decedent_marked(self, limit: int = 500) -> List[sqlite3.Row]:
        """Parcels whose owner string already marks the owner as deceased.

        Estate signals that need no obituary and no name matching. A small
        population (~95 countywide), so treat as a backlog sweep, not a feed.
        """
        return self.store.query(
            "SELECT * FROM parcels WHERE indicates_decedent = 1 "
            "ORDER BY total_value DESC LIMIT ?",
            (limit,),
        )

    def absentee_owners(self, *, out_of_state_only: bool = False, limit: int = 500) -> List[sqlite3.Row]:
        column = "is_out_of_state" if out_of_state_only else "is_absentee"
        return self.store.query(
            "SELECT * FROM parcels WHERE " + column + " = 1 ORDER BY total_value DESC LIMIT ?",
            (limit,),
        )

    # --- Person-keyed lookup (expensive, needs corroboration) ---------------

    def candidates_for_person(
        self,
        name: str,
        *,
        city: Optional[str] = None,
        include_weak: bool = False,
        limit: int = 50,
    ) -> List[Candidate]:
        """Find parcels possibly owned by a named person.

        Blocking is on surname plus first initial, then each candidate is
        scored by *which corroborating signals are present*, not by string
        similarity. A candidate reaches ``STRONG`` only with an exact
        surname+given match **and** at least one independent corroborating
        signal — the two-signal rule from the entity-resolution page.
        """
        person = parse_person_query(name)
        if not person.surname:
            return []

        rows = self.store.query(
            "SELECT o.surname, o.given, o.suffix, p.* "
            "FROM owner_names o JOIN parcels p ON p.parcel_key = o.parcel_key "
            "WHERE o.loose_blocking_key = ?",
            (person.loose_blocking_key,),
        )

        wanted_city = clean(city) or None
        query_given = person.given.split(" ") if person.given else []
        candidates: List[Candidate] = []

        for row in rows:
            row_given = (row["given"] or "").split(" ")
            evidence: List[str] = ["surname " + person.surname]
            exact_given = bool(query_given) and bool(row_given) and query_given[0] == row_given[0]

            if exact_given:
                evidence.append("given name " + query_given[0])
            elif query_given and row_given and query_given[0][:1] == row_given[0][:1]:
                evidence.append("first initial " + query_given[0][:1] + " only")

            corroborating = 0
            if len(query_given) > 1 and len(row_given) > 1 and query_given[1][:1] == row_given[1][:1]:
                evidence.append("middle initial " + query_given[1][:1])
                corroborating += 1
            if person.suffix and row["suffix"] and person.suffix == row["suffix"]:
                evidence.append("suffix " + person.suffix)
                corroborating += 1
            if wanted_city and row["situs_city"] and wanted_city == row["situs_city"]:
                evidence.append("city " + wanted_city)
                corroborating += 1
            if row["indicates_decedent"]:
                evidence.append("owner record marked " + str(row["owner_type"]))
                corroborating += 1

            if exact_given and corroborating >= 1:
                tier = MatchTier.STRONG
            elif exact_given:
                tier = MatchTier.MODERATE
            else:
                tier = MatchTier.WEAK

            if tier == MatchTier.WEAK and not include_weak:
                continue

            candidates.append(
                Candidate(
                    parcel_key=row["parcel_key"],
                    owner_raw=row["owner_raw"],
                    situs_raw=row["situs_raw"],
                    matched_name=" ".join(x for x in (row["given"], row["surname"]) if x),
                    tier=tier,
                    evidence=tuple(evidence),
                )
            )

        order: Dict[str, int] = {MatchTier.STRONG: 0, MatchTier.MODERATE: 1, MatchTier.WEAK: 2}
        candidates.sort(key=lambda c: (order[c.tier], -len(c.evidence), c.parcel_key))
        return candidates[:limit]


def summarize_candidates(candidates: Sequence[Candidate]) -> str:
    """Render candidates as an evidence list rather than a ranked score."""
    if not candidates:
        return "no candidates"
    lines = []
    for candidate in candidates:
        lines.append(
            candidate.tier
            + "  "
            + candidate.parcel_key
            + "  "
            + (candidate.situs_raw or "(no situs address)")
            + "\n    owner: "
            + candidate.owner_raw
            + "\n    evidence: "
            + "; ".join(candidate.evidence)
        )
    lines.append("")
    lines.append(str(len(candidates)) + " candidate(s). All require review before use as a match.")
    return "\n".join(lines)
