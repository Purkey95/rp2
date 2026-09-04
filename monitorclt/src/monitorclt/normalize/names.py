"""Name parsing that handles what county data actually looks like.

Assessor and register-of-deeds rows are "LAST FIRST MIDDLE"; court filings are
"FIRST MIDDLE LAST"; a comma always wins ("PUBLIC, JOHN Q"); an "ESTATE OF"-style
prefix flips the remainder to natural order; "PUBLIC JOHN Q & JANE R" is two people
and the second inherits the printed surname; organizations are detected by token.

Ported from the v1 probate tool and kept deterministic on purpose: a reviewer must
be able to see exactly why two strings were or were not treated as the same name.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

DEFAULT_RULES: Dict[str, Any] = {
    "suffixes": ["JR", "SR", "II", "III", "IV", "MD", "DDS", "ESQ", "PHD"],
    "organization_tokens": [
        "LLC",
        "L L C",
        "INC",
        "CORP",
        "CORPORATION",
        "COMPANY",
        "CO",
        "LP",
        "LLP",
        "PLLC",
        "TRUST",
        "TRUSTEE",
        "TRUSTEES",
        "BANK",
        "CHURCH",
        "MINISTRIES",
        "PARTNERS",
        "PARTNERSHIP",
        "HOLDINGS",
        "PROPERTIES",
        "ASSOCIATION",
        "ASSOCIATES",
        "FOUNDATION",
        "COUNTY",
        "CITY",
        "STATE",
        "AUTHORITY",
        "HOA",
        "CONDOMINIUM",
        "APARTMENTS",
        "DEVELOPMENT",
        "INVESTMENTS",
        "GROUP",
        "NA",
        "FSB",
        "MORTGAGE",
        "LENDING",
        "FUND",
        "REIT",
        "VENTURES",
        "ENTERPRISES",
    ],
    "estate_markers": [
        "ESTATE OF",
        "LIFE ESTATE",
        "HEIRS OF",
        "HEIRS",
        "DEVISEES",
        "DECEASED",
        "DECD",
        "EXECUTOR",
        "EXECUTRIX",
        "ADMINISTRATOR",
        "ADMINISTRATRIX",
    ],
    "noise_tokens": ["ET", "AL", "ETAL", "ETUX", "UX", "VIR", "ETVIR", "MR", "MRS", "MS", "DR"],
}

_NAME_CHARS = re.compile(r"[^A-Z0-9,& ]+")
_PUNCT = re.compile(r"[.'‘’`\-]")
_SPACES = re.compile(r"\s+")


@dataclass
class Name:
    raw: str
    clean: str
    first: str = ""
    middle: str = ""
    last: str = ""
    suffix: str = ""
    is_organization: bool = False
    markers: List[str] = field(default_factory=list)

    @property
    def middle_initial(self) -> str:
        return self.middle[:1]

    @property
    def normalized(self) -> str:
        return " ".join(t for t in (self.first, self.middle, self.last) if t) or self.clean

    @property
    def key_full(self) -> str:
        return "|".join((self.first, self.middle, self.last))

    @property
    def key_fl(self) -> str:
        return "|".join((self.first, self.last))

    @property
    def is_person(self) -> bool:
        return bool(self.first and self.last) and not self.is_organization

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data.update({"normalized": self.normalized, "key_fl": self.key_fl, "key_full": self.key_full, "middle_initial": self.middle_initial})
        return data


def clean_text(value: Optional[str]) -> str:
    """Uppercase, drop intra-word punctuation, collapse whitespace.

    Hyphens/apostrophes are deleted rather than spaced (O'BRIEN -> OBRIEN, SMITH-JONES
    -> SMITHJONES) so a compound surname stays one token.
    """
    s = (value or "").upper()
    s = _PUNCT.sub("", s)
    s = s.replace("&", " & ")
    s = _NAME_CHARS.sub(" ", s)
    return _SPACES.sub(" ", s).strip()


def strip_markers(body: str, markers: List[str]) -> "tuple[str, List[str]]":
    """Remove probate markers and report which were there; longest first so 'HEIRS OF' beats 'HEIRS'."""
    found = []
    for marker in sorted(markers, key=len, reverse=True):
        pattern = r"(?<![A-Z0-9])" + re.escape(clean_text(marker) or marker) + r"(?![A-Z0-9])"
        if re.search(pattern, body):
            found.append(marker)
            body = re.sub(pattern, " ", body)
    return _SPACES.sub(" ", body).strip(), sorted(found)


def parse_name(raw: Optional[str], name_format: str = "last_first", rules: Optional[Dict[str, Any]] = None) -> Name:
    rules = rules or DEFAULT_RULES
    body = clean_text(raw)
    body, markers = strip_markers(body, rules.get("estate_markers", DEFAULT_RULES["estate_markers"]))
    tokens = [t for t in body.replace(",", " , ").split() if t]
    noise = set(rules.get("noise_tokens", DEFAULT_RULES["noise_tokens"]))
    tokens = [t for t in tokens if t not in noise]

    org_tokens = set(rules.get("organization_tokens", DEFAULT_RULES["organization_tokens"]))
    if any(t in org_tokens for t in tokens):
        joined = " ".join(t for t in tokens if t != ",")
        return Name(raw=raw or "", clean=joined, is_organization=True, markers=markers)

    suffixes = set(rules.get("suffixes", DEFAULT_RULES["suffixes"]))
    suffix = ""
    while len(tokens) > 2 and tokens[-1] in suffixes:
        suffix = suffix or tokens[-1]
        tokens.pop()

    if any(m.endswith(" OF") for m in markers):
        name_format = "first_last"

    if "," in tokens:
        cut = tokens.index(",")
        last_tokens, rest = tokens[:cut], tokens[cut + 1 :]
        last = " ".join(last_tokens)
        first = rest[0] if rest else ""
        middle = " ".join(rest[1:])
    elif len(tokens) < 2:
        last, first, middle = (tokens[0] if tokens else ""), "", ""
    elif name_format == "last_first":
        last, first, middle = tokens[0], tokens[1], " ".join(tokens[2:])
    else:
        last, first, middle = tokens[-1], tokens[0], " ".join(tokens[1:-1])

    return Name(raw=raw or "", clean=body, first=first.strip(), middle=middle.strip(), last=last.strip(), suffix=suffix, markers=markers)


def split_parties(raw: Optional[str], name_format: str = "last_first", rules: Optional[Dict[str, Any]] = None) -> List[Name]:
    """Split a multi-owner string into parties, inheriting an implied surname."""
    rules = rules or DEFAULT_RULES
    body = clean_text(raw)
    parts = [p.strip() for p in re.split(r"\s*&\s*|\s+AND\s+|\s*;\s*", body) if p.strip()]
    if not parts:
        return []
    suffixes = set(rules.get("suffixes", DEFAULT_RULES["suffixes"]))
    out = [parse_name(parts[0], name_format, rules)]
    for part in parts[1:]:
        name = parse_name(part, name_format, rules)
        primary = out[0]
        tokens = [t for t in part.split() if t not in suffixes and t != ","]
        if not name.is_organization and not primary.is_organization and primary.last and len(tokens) <= 2 and not name.markers:
            name = Name(raw=part, clean=part, first=tokens[0], middle=" ".join(tokens[1:]), last=primary.last)
        out.append(name)
    return out


def name_agreement(a: Name, b: Name) -> str:
    """How two parsed person names relate: 'full', 'first_last', or 'conflict' on middle."""
    if a.key_fl != b.key_fl:
        return "different"
    if a.middle and b.middle:
        if a.middle == b.middle or a.middle[0] == b.middle[0]:
            return "full"
        return "conflict"
    return "first_last"


def suffix_conflict(a: Name, b: Name) -> bool:
    """JR vs SR on the same name is two people, usually a parent and child."""
    return bool(a.suffix and b.suffix and a.suffix != b.suffix)
