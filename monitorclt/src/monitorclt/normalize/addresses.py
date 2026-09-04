"""Address parsing into components, and comparisons that know what a unit is.

A regex suffix dictionary plateaus fast; this parser is small but structural: it
pulls out the house number, directionals, street name, suffix, unit, PO box, city,
state and ZIP, so "4210 ELM ST APT 5" and "4210 Elm Street, Unit 5" compare equal,
and "4210 ELM ST" compares equal at street level but not at unit level. Swap in
libpostal/usaddress behind `parse_address` for production without touching callers.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

STREET_SUFFIXES = {
    "STREET": "ST",
    "STR": "ST",
    "AVENUE": "AVE",
    "AVENU": "AVE",
    "AV": "AVE",
    "BOULEVARD": "BLVD",
    "BLV": "BLVD",
    "DRIVE": "DR",
    "DRV": "DR",
    "ROAD": "RD",
    "LANE": "LN",
    "COURT": "CT",
    "CIRCLE": "CIR",
    "PLACE": "PL",
    "TERRACE": "TER",
    "TERR": "TER",
    "PARKWAY": "PKWY",
    "PKY": "PKWY",
    "HIGHWAY": "HWY",
    "TRAIL": "TRL",
    "SQUARE": "SQ",
    "WAY": "WAY",
    "LOOP": "LOOP",
    "RUN": "RUN",
    "PATH": "PATH",
    "POINT": "PT",
    "PIKE": "PIKE",
    "CROSSING": "XING",
    "EXTENSION": "EXT",
    "ALLEY": "ALY",
    "PLAZA": "PLZ",
    "COVE": "CV",
    "BEND": "BND",
    "RIDGE": "RDG",
    "HILL": "HL",
    "HILLS": "HLS",
    "GLEN": "GLN",
    "GROVE": "GRV",
    "MANOR": "MNR",
    "COMMONS": "CMNS",
    "ST": "ST",
    "AVE": "AVE",
    "BLVD": "BLVD",
    "DR": "DR",
    "RD": "RD",
    "LN": "LN",
    "CT": "CT",
    "CIR": "CIR",
    "PL": "PL",
    "TER": "TER",
    "PKWY": "PKWY",
    "HWY": "HWY",
    "TRL": "TRL",
    "SQ": "SQ",
    "PT": "PT",
    "XING": "XING",
    "EXT": "EXT",
    "ALY": "ALY",
    "PLZ": "PLZ",
    "CV": "CV",
    "BND": "BND",
    "RDG": "RDG",
    "HL": "HL",
    "HLS": "HLS",
    "GLN": "GLN",
    "GRV": "GRV",
    "MNR": "MNR",
    "CMNS": "CMNS",
}
DIRECTIONALS = {
    "NORTH": "N",
    "SOUTH": "S",
    "EAST": "E",
    "WEST": "W",
    "NORTHEAST": "NE",
    "NORTHWEST": "NW",
    "SOUTHEAST": "SE",
    "SOUTHWEST": "SW",
    "N": "N",
    "S": "S",
    "E": "E",
    "W": "W",
    "NE": "NE",
    "NW": "NW",
    "SE": "SE",
    "SW": "SW",
}
UNIT_WORDS = {"APT", "APARTMENT", "UNIT", "STE", "SUITE", "BLDG", "BUILDING", "FL", "FLOOR", "RM", "ROOM", "LOT", "TRLR", "SPC", "#"}
STATES = {
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
    "DC",
    "PR",
}
_ZIP = re.compile(r"^(\d{5})(?:-?(\d{4}))?$")


@dataclass
class Address:
    raw: str
    number: str = ""
    predir: str = ""
    street: str = ""
    suffix: str = ""
    postdir: str = ""
    unit: str = ""
    po_box: str = ""
    city: str = ""
    state: str = ""
    zip: str = ""

    @property
    def street_line(self) -> str:
        """Everything that identifies the building, unit excluded."""
        if self.po_box:
            return "PO BOX " + self.po_box
        return " ".join(p for p in (self.number, self.predir, self.street, self.suffix, self.postdir) if p)

    @property
    def normalized(self) -> str:
        parts = [self.street_line]
        if self.unit:
            parts.append("# " + self.unit)
        parts.extend(p for p in (self.city, self.state, self.zip) if p)
        return " ".join(parts).strip()

    @property
    def street_key(self) -> str:
        """Street-level identity within a locality: no unit, ZIP preferred over city text."""
        locality = self.zip or self.city
        return (self.street_line + " " + locality).strip()

    @property
    def is_po_box(self) -> bool:
        return bool(self.po_box)

    @property
    def is_empty(self) -> bool:
        return not (self.number or self.street or self.po_box)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["normalized"] = self.normalized
        d["street_key"] = self.street_key
        return d


def _tokens(raw: Optional[str]) -> "list[str]":
    s = (raw or "").upper()
    s = s.replace("#", " # ")
    s = re.sub(r"[.,]", " ", s)
    s = re.sub(r"[^A-Z0-9#/\- ]+", " ", s)
    return [t for t in s.split() if t]


def parse_address(raw: Optional[str]) -> Address:
    addr = Address(raw=raw or "")
    tokens = _tokens(raw)
    if not tokens:
        return addr

    # C/O lines and "ATTN" prefixes are routing, not location.
    for marker in ("C/O", "ATTN", "ATTENTION"):
        if marker in tokens:
            idx = tokens.index(marker)
            # drop the marker and the name that follows it, up to the first number-looking token
            j = idx + 1
            while j < len(tokens) and not (tokens[j].isdigit() or tokens[j] in ("PO", "P", "BOX")):
                j += 1
            tokens = tokens[:idx] + tokens[j:]

    # ZIP and state come off the end.
    if tokens and _ZIP.match(tokens[-1]):
        m = _ZIP.match(tokens[-1])
        addr.zip = m.group(1)  # type: ignore[union-attr]
        tokens.pop()
    elif len(tokens) >= 2 and re.fullmatch(r"\d{5}", tokens[-2]) and re.fullmatch(r"\d{4}", tokens[-1]):
        addr.zip = tokens[-2]
        tokens = tokens[:-2]
    if tokens and tokens[-1] in STATES and len(tokens) > 1:
        addr.state = tokens[-1]
        tokens.pop()

    # PO box.
    joined = " ".join(tokens)
    m = re.search(r"\b(?:P\s*O|PO|POST OFFICE)\s*BOX\s+([A-Z0-9\-]+)", joined)
    if m:
        addr.po_box = m.group(1)
        rest = (joined[: m.start()] + " " + joined[m.end() :]).split()
        addr.city = " ".join(rest)
        return addr

    # Unit.
    unit_idx = next((i for i, t in enumerate(tokens) if t in UNIT_WORDS), None)
    if unit_idx is not None and unit_idx + 1 < len(tokens):
        addr.unit = tokens[unit_idx + 1].lstrip("#")
        tokens = tokens[:unit_idx] + tokens[unit_idx + 2 :]
    elif unit_idx is not None:
        tokens = tokens[:unit_idx]

    # House number.
    if tokens and re.fullmatch(r"\d+[A-Z]?(?:-\d+)?", tokens[0]):
        addr.number = tokens.pop(0)

    if tokens and tokens[0] in DIRECTIONALS and len(tokens) > 1:
        addr.predir = DIRECTIONALS[tokens.pop(0)]

    # Street name runs until the suffix; whatever follows a suffix is postdir then city.
    suffix_idx = None
    for i in range(len(tokens) - 1, -1, -1):
        if tokens[i] in STREET_SUFFIXES and i > 0:
            suffix_idx = i
            break
    if suffix_idx is not None:
        addr.street = " ".join(tokens[:suffix_idx])
        addr.suffix = STREET_SUFFIXES[tokens[suffix_idx]]
        rest = tokens[suffix_idx + 1 :]
        if rest and rest[0] in DIRECTIONALS:
            addr.postdir = DIRECTIONALS[rest.pop(0)]
        addr.city = " ".join(rest)
    else:
        # No suffix: treat trailing tokens as city only if there is a state/zip to anchor them.
        if (addr.state or addr.zip) and len(tokens) > 2:
            addr.street = " ".join(tokens[:-1])
            addr.city = tokens[-1]
        else:
            addr.street = " ".join(tokens)
    return addr


def normalize_address(raw: Optional[str]) -> str:
    return parse_address(raw).normalized


def compare(a: Optional[str], b: Optional[str]) -> str:
    """'exact' (same building and unit), 'street' (same building, unit differs/missing), or 'none'."""
    pa, pb = parse_address(a), parse_address(b)
    if pa.is_empty or pb.is_empty:
        return "none"
    if pa.street_key != pb.street_key:
        return "none"
    if pa.unit == pb.unit:
        return "exact"
    return "street"


def zip_of(raw: Optional[str]) -> str:
    return parse_address(raw).zip


def state_of(raw: Optional[str]) -> str:
    return parse_address(raw).state
