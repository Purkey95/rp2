"""Address normalization for joining county records to parcels.

The goal is a *stable join key*, not USPS-certified output. Where a choice is
ambiguous, this module prefers the option that maps both sides of a join to the
same string, and documents the tradeoff.

Mecklenburg situs addresses embed the city and state
(``'12422 DOWNY BIRCH RD CHARLOTTE NC'``) while mailing addresses do not
(``'12422 DOWNY BIRCH RD'`` + separate city/state/zip columns), so the city and
state tail is stripped before comparison.
"""

from __future__ import annotations

from typing import List, NamedTuple, Optional, Sequence, Tuple

from .text import clean, tokens

# Municipalities appearing in Mecklenburg situs strings. Multi-word entries are
# matched as token sequences, so "MINT HILL" is stripped as a unit.
MECKLENBURG_MUNICIPALITIES: Tuple[Tuple[str, ...], ...] = (
    ("CHARLOTTE",),
    ("CORNELIUS",),
    ("DAVIDSON",),
    ("HUNTERSVILLE",),
    ("MATTHEWS",),
    ("MINT", "HILL"),
    ("PINEVILLE",),
    ("STALLINGS",),
    ("WEDDINGTON",),
    ("INDIAN", "TRAIL",),
    # Not a municipality: the county writes UNINC in the jurisdiction slot for
    # unincorporated addresses (' GOODMAN RD UNINC NC'). It is stripped in the
    # same position and for the same reason, and it is not rare -- roughly 5.5%
    # of parcels carry it.
    ("UNINC",),
)

STATE_TOKENS = frozenset({"NC", "SC"})

# Applied to the final token only, so a street named PARK keeps its name while a
# trailing "PARKWAY" becomes PKWY.
#
# Both the USPS long form and Mecklenburg's own abbreviations map to the same
# canonical value, which is the point: the county writes AV, CR, BV, WY, PY and
# TR where USPS writes AVE, CIR, BLVD, WAY, PKWY and TRL. Canonicalizing both
# sides lets a county situs address join to a legal notice or a mailing address
# that spells the suffix differently. Note TR is TRAIL in this data
# ('9917 PALLISERS TR'), not TERRACE.
SUFFIX_ABBREVIATIONS = {
    "ALLEY": "ALY", "AVENUE": "AVE", "AVENU": "AVE", "AVN": "AVE", "BEND": "BND",
    "BLUFF": "BLF", "BOULEVARD": "BLVD", "BRANCH": "BR", "BRIDGE": "BRG",
    "BROOK": "BRK", "BYPASS": "BYP", "CENTER": "CTR", "CIRCLE": "CIR",
    "CLIFF": "CLF", "COMMONS": "CMNS", "CORNER": "COR", "COURT": "CT",
    "COVE": "CV", "CREEK": "CRK", "CRESCENT": "CRES", "CROSSING": "XING",
    "DRIVE": "DR", "ESTATES": "ESTS", "EXPRESSWAY": "EXPY", "EXTENSION": "EXT",
    "FALLS": "FLS", "FIELD": "FLD", "FOREST": "FRST", "FORK": "FRK",
    "FREEWAY": "FWY", "GARDEN": "GDN", "GARDENS": "GDNS", "GATEWAY": "GTWY",
    "GLEN": "GLN", "GREEN": "GRN", "GROVE": "GRV", "HARBOR": "HBR",
    "HEIGHTS": "HTS", "HIGHWAY": "HWY", "HILL": "HL", "HILLS": "HLS",
    "HOLLOW": "HOLW", "ISLAND": "IS", "JUNCTION": "JCT", "KNOLL": "KNL",
    "LAKE": "LK", "LAKES": "LKS", "LANDING": "LNDG", "LANE": "LN",
    "MANOR": "MNR", "MEADOW": "MDW", "MEADOWS": "MDWS", "MILL": "ML",
    "MOUNT": "MT", "MOUNTAIN": "MTN", "ORCHARD": "ORCH", "PARKWAY": "PKWY",
    "PASSAGE": "PSGE", "PLACE": "PL", "PLAIN": "PLN", "PLAZA": "PLZ",
    "POINT": "PT", "RIDGE": "RDG", "RIVER": "RIV", "ROAD": "RD",
    "SHORE": "SHR", "SPRING": "SPG", "SPRINGS": "SPGS", "SQUARE": "SQ",
    "STATION": "STA", "STREET": "ST", "SUMMIT": "SMT", "TERRACE": "TER",
    "TRACE": "TRCE", "TRAIL": "TRL", "TURNPIKE": "TPKE", "VALLEY": "VLY",
    "VIEW": "VW", "VILLAGE": "VLG", "VISTA": "VIS", "WELLS": "WLS",
    # Mecklenburg-specific abbreviations observed in the situs data.
    "AV": "AVE", "CR": "CIR", "BV": "BLVD", "WY": "WAY", "TL": "TRL",
    "PY": "PKWY", "TR": "TRL", "HY": "HWY", "AL": "ALY", "RN": "RUN",
    "LP": "LOOP",
}

DIRECTIONALS = {
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
    "NORTHEAST": "NE", "NORTHWEST": "NW", "SOUTHEAST": "SE", "SOUTHWEST": "SW",
}

UNIT_DESIGNATORS = frozenset(
    {"UNIT", "APT", "APARTMENT", "STE", "SUITE", "BLDG", "BUILDING", "LOT", "FL", "FLOOR", "RM", "ROOM", "TRLR", "SPC"}
)


class NormalizedAddress(NamedTuple):
    """A parsed address plus the key used for joining."""

    key: str
    """Normalized street address without unit, city or state. The join key."""

    key_with_unit: str
    """As ``key``, plus the unit. Distinguishes condo units at one street address."""

    house_number: Optional[str]
    street: str
    unit: Optional[str]
    city: Optional[str]
    state: Optional[str]

    @property
    def is_empty(self) -> bool:
        return not self.key


def _strip_state(parts: List[str]) -> Tuple[List[str], Optional[str]]:
    if parts and parts[-1] in STATE_TOKENS:
        return parts[:-1], parts[-1]
    return parts, None


def _strip_municipality(
    parts: List[str], municipalities: Sequence[Tuple[str, ...]]
) -> Tuple[List[str], Optional[str]]:
    """Remove a trailing city name, longest match first.

    Only the trailing position is considered, so a street named DAVIDSON ST in
    Charlotte keeps its name; only the final CHARLOTTE is removed.
    """
    for name in sorted(municipalities, key=len, reverse=True):
        span = len(name)
        if span <= len(parts) and tuple(parts[-span:]) == name:
            return parts[:-span], " ".join(name)
    return parts, None


def _extract_unit(parts: List[str]) -> Tuple[List[str], Optional[str]]:
    for index, token in enumerate(parts):
        if token in UNIT_DESIGNATORS and index + 1 < len(parts):
            return parts[:index] + parts[index + 2 :], parts[index + 1]
    return parts, None


def _standardize(parts: List[str]) -> List[str]:
    result = [DIRECTIONALS.get(token, token) for token in parts]
    if result:
        # Suffix mapping applies to the last token only: "PARK AVE" keeps PARK,
        # while "SHARON AMITY PARKWAY" gets PKWY.
        result[-1] = SUFFIX_ABBREVIATIONS.get(result[-1], result[-1])
    return result


def normalize_address(
    raw: Optional[str],
    *,
    city: Optional[str] = None,
    state: Optional[str] = None,
    municipalities: Sequence[Tuple[str, ...]] = MECKLENBURG_MUNICIPALITIES,
) -> NormalizedAddress:
    """Normalize a raw address string into a join key and components.

    ``city``/``state`` supply values for sources that keep them in separate
    columns (mailing addresses); values embedded in ``raw`` take precedence.
    """
    parts = tokens(raw)
    if not parts:
        return NormalizedAddress("", "", None, "", None, clean(city) or None, clean(state) or None)

    parts, found_state = _strip_state(parts)
    parts, found_city = _strip_municipality(parts, municipalities)
    parts, unit = _extract_unit(parts)
    parts = _standardize(parts)

    house_number: Optional[str] = None
    if parts and parts[0].isdigit():
        house_number = parts[0]

    key = " ".join(parts)
    return NormalizedAddress(
        key=key,
        key_with_unit=(key + " # " + unit) if unit else key,
        house_number=house_number,
        street=" ".join(parts[1:]) if house_number else key,
        unit=unit,
        city=found_city or (clean(city) or None),
        state=found_state or (clean(state) or None),
    )


def same_address(left: NormalizedAddress, right: NormalizedAddress) -> bool:
    """True when two normalized addresses share a non-empty street-level key."""
    return bool(left.key) and left.key == right.key
