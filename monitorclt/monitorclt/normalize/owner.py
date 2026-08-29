"""Owner-string parsing and classification.

Two facts about the Mecklenburg CAMA owner columns drive this module.

**The county's own first/last split is unreliable.** It appears to split on
whitespace without understanding the string, so non-person owners are shredded
into name fields: ``'THE GELPI LIVING TRUST'`` becomes
last=``'THE GELPI LIVING '`` / first=``'TRUST'``, and
``'JOSEPH P BAGWELL ESTATE'`` becomes first=``'ESTATE'``. The split fields are
therefore used *only* after ``full_owner_name`` classifies as a person.

**Substring matching produces more false positives than true positives.**
Against ~57 genuine trailing-ESTATE owners there are 1,119 parcels containing
"REAL ESTATE"; "LIFE" hits ``GRACELIFE CHURCH``, ``LIFELD MICHAEL A`` and
``ROADSTER LIFE LLC``; "ETAL" hits ``VETAL DONALD III`` and
``METALS FREEDOM INC``. Every marker here is matched on word-boundary tokens.
"""

from __future__ import annotations

import re
from typing import FrozenSet, List, NamedTuple, Optional, Sequence, Tuple

from .text import blank_to_none, clean, tokens

# --- Classification vocabularies -------------------------------------------

COMPANY_TOKENS: FrozenSet[str] = frozenset(
    {
        "LLC", "LLP", "LLLP", "PLLC", "INC", "INCORPORATED", "CORP", "CORPORATION",
        "LP", "LTD", "COMPANY", "PARTNERS", "PARTNERSHIP", "HOLDINGS", "PROPERTIES",
        "REALTY", "INVESTMENT", "INVESTMENTS", "INVESTORS", "ASSOCIATES",
        "ENTERPRISES", "VENTURES", "CAPITAL", "MANAGEMENT", "DEVELOPMENT",
        "DEVELOPERS", "BUILDERS", "HOMES", "BANK", "MORTGAGE", "FUND",
    }
)

ORGANIZATION_TOKENS: FrozenSet[str] = frozenset(
    {
        "CHURCH", "MINISTRY", "MINISTRIES", "TEMPLE", "SYNAGOGUE", "MOSQUE",
        "CHAPEL", "CATHEDRAL", "PARISH", "DIOCESE", "CONGREGATION", "SCHOOL",
        "ACADEMY", "COLLEGE", "UNIVERSITY", "HOSPITAL", "FOUNDATION", "SOCIETY",
        "LODGE", "ASSOCIATION", "CEMETERY", "YMCA", "YWCA", "CONDOMINIUM",
        "HOMEOWNERS", "COUNCIL",
    }
)

GOVERNMENT_PHRASES: Tuple[Tuple[str, ...], ...] = (
    ("CITY", "OF"), ("COUNTY", "OF"), ("TOWN", "OF"), ("STATE", "OF"),
    ("UNITED", "STATES"), ("HOUSING", "AUTHORITY"), ("NORTH", "CAROLINA", "DEPARTMENT"),
    ("BOARD", "OF", "EDUCATION"), ("TRANSIT", "AUTHORITY"),
)

TRUST_TOKENS: FrozenSet[str] = frozenset({"TRUST", "TRUSTEE", "TRUSTEES"})

NAME_SUFFIXES: FrozenSet[str] = frozenset({"JR", "SR", "II", "III", "IV"})

#: Marker words that leak into the county's name columns on decedent records
#: and must be removed before the residual is read as a person's name.
DECEDENT_MARKER_TOKENS: FrozenSet[str] = frozenset({"HEIRS", "ESTATE", "LIFE", "OF", "THE"})

_CARE_OF = re.compile(r"^(?:C\s*/?\s*O|ATTN|ATTENTION)\b[\s:]*")


class OwnerType(object):
    """Classification of an owner string. Plain constants for Python 3.8."""

    PERSON = "PERSON"
    COUPLE = "COUPLE"
    COMPANY = "COMPANY"
    ORGANIZATION = "ORGANIZATION"
    GOVERNMENT = "GOVERNMENT"
    TRUST = "TRUST"
    ESTATE = "ESTATE"
    HEIRS = "HEIRS"
    LIFE_ESTATE = "LIFE_ESTATE"
    UNKNOWN = "UNKNOWN"


#: Owner types that mean the record itself says the owner has died. These are
#: estate signals requiring no obituary matching and no entity resolution.
DECEDENT_TYPES: FrozenSet[str] = frozenset({OwnerType.ESTATE, OwnerType.HEIRS, OwnerType.LIFE_ESTATE})


class PersonName(NamedTuple):
    surname: str
    given: str
    suffix: Optional[str]

    @property
    def blocking_key(self) -> str:
        """Surname + first given token. Used to generate match candidates."""
        first = self.given.split(" ")[0] if self.given else ""
        return self.surname + "|" + first

    @property
    def loose_blocking_key(self) -> str:
        """Surname + first initial. A wider net, for recall during blocking."""
        first = self.given.split(" ")[0] if self.given else ""
        return self.surname + "|" + (first[:1] if first else "")

    def __str__(self) -> str:
        parts = [self.given, self.surname]
        if self.suffix:
            parts.append(self.suffix)
        return " ".join(p for p in parts if p)


class ParsedOwner(NamedTuple):
    raw: str
    owner_type: str
    persons: Tuple[PersonName, ...]
    care_of: Optional[str]
    markers: Tuple[str, ...]
    """Word-boundary markers that drove the classification, for audit."""

    @property
    def indicates_decedent(self) -> bool:
        return self.owner_type in DECEDENT_TYPES

    @property
    def is_person_like(self) -> bool:
        return self.owner_type in (OwnerType.PERSON, OwnerType.COUPLE)


def split_care_of(value: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Split a ``C/O ...`` or ``ATTN ...`` prefix off a secondary-owner field.

    The secondary owner columns hold three different things: a genuine
    co-owner (``FOSTER RANDY``), a care-of agent (``C/O DALE CONRAD``), and a
    corporate agent line (``C/O FIRSTKEY HOMES LLC``). Returns
    ``(co_owner, care_of)`` with at most one populated.
    """
    cleaned = blank_to_none(value)
    if cleaned is None:
        return None, None
    match = _CARE_OF.match(cleaned)
    if match:
        remainder = cleaned[match.end() :].strip()
        return None, remainder or None
    return cleaned, None


def _strip_suffix(parts: List[str]) -> Tuple[List[str], Optional[str]]:
    if len(parts) > 1 and parts[-1] in NAME_SUFFIXES:
        return parts[:-1], parts[-1]
    return parts, None


def _has_phrase(parts: Sequence[str], phrase: Tuple[str, ...]) -> bool:
    span = len(phrase)
    return any(tuple(parts[i : i + span]) == phrase for i in range(len(parts) - span + 1))


def _classify(parts: Sequence[str]) -> Tuple[str, Tuple[str, ...]]:
    """Return ``(owner_type, markers)`` from word-boundary tokens.

    Precedence is deliberate. Organization beats company so ``GRACELIFE CHURCH
    INC`` classifies as an organization rather than a company; company beats the
    decedent markers so ``HALL JOHNSTON HEIRS LLC`` is a company rather than an
    heirs record.
    """
    token_set = frozenset(parts)

    for phrase in GOVERNMENT_PHRASES:
        if _has_phrase(parts, phrase):
            return OwnerType.GOVERNMENT, (" ".join(phrase),)

    org = token_set & ORGANIZATION_TOKENS
    if org:
        return OwnerType.ORGANIZATION, tuple(sorted(org))

    company = token_set & COMPANY_TOKENS
    if company:
        return OwnerType.COMPANY, tuple(sorted(company))

    # "LIFE ESTATE" must be adjacent; "REAL ESTATE" is never an estate marker.
    if _has_phrase(parts, ("LIFE", "ESTATE")):
        return OwnerType.LIFE_ESTATE, ("LIFE ESTATE",)

    estate_positions = [i for i, t in enumerate(parts) if t == "ESTATE"]
    genuine_estate = [i for i in estate_positions if i == 0 or parts[i - 1] != "REAL"]
    if genuine_estate:
        return OwnerType.ESTATE, ("ESTATE",)

    if "HEIRS" in token_set:
        return OwnerType.HEIRS, ("HEIRS",)

    trust = token_set & TRUST_TOKENS
    if trust:
        return OwnerType.TRUST, tuple(sorted(trust))

    return OwnerType.PERSON, ()


def parse_person(surname: Optional[str], given: Optional[str]) -> Optional[PersonName]:
    """Build a ``PersonName`` from the county's split name columns."""
    surname_tokens = tokens(surname)
    given_tokens = tokens(given)
    if not surname_tokens and not given_tokens:
        return None
    given_tokens, suffix = _strip_suffix(given_tokens)
    if suffix is None:
        surname_tokens, suffix = _strip_suffix(surname_tokens)
    return PersonName(" ".join(surname_tokens), " ".join(given_tokens), suffix)


def parse_decedent_person(surname: Optional[str], given: Optional[str]) -> Optional[PersonName]:
    """Recover the decedent's name from an ESTATE / HEIRS / LIFE ESTATE record.

    These are the records an obituary most needs to match, so leaving them out
    of the name index would omit exactly the case the index exists for. The
    marker word leaks into whichever column the county's splitter dropped it in,
    and the two observed layouts differ in name order:

    * ``CONRAD WALTER H HEIRS`` -> last=``'CONRAD'``, first=``'WALTER H HEIRS'``
      (county order: surname first)
    * ``JOSEPH P BAGWELL ESTATE`` -> last=``'JOSEPH P BAGWELL'``, first=``'ESTATE'``
      (natural order, with the marker occupying the whole first column)

    The marker consuming the entire first column is what distinguishes them, so
    that is the test used rather than guessing at name order.
    """
    surname_tokens = [t for t in tokens(surname) if t not in DECEDENT_MARKER_TOKENS]
    given_tokens = [t for t in tokens(given) if t not in DECEDENT_MARKER_TOKENS]

    if not given_tokens:
        # Natural order: the whole name sits in the surname column.
        if len(surname_tokens) < 2:
            return PersonName(" ".join(surname_tokens), "", None) if surname_tokens else None
        surname_tokens, suffix = _strip_suffix(surname_tokens)
        if not surname_tokens:
            return None
        return PersonName(surname_tokens[-1], " ".join(surname_tokens[:-1]), suffix)

    if not surname_tokens:
        return None
    given_tokens, suffix = _strip_suffix(given_tokens)
    return PersonName(" ".join(surname_tokens), " ".join(given_tokens), suffix)


def parse_owner(
    full_owner_name: Optional[str],
    *,
    surname: Optional[str] = None,
    given: Optional[str] = None,
    secondary_surname: Optional[str] = None,
    secondary_given: Optional[str] = None,
) -> ParsedOwner:
    """Classify an owner record and extract person names where applicable.

    The split name columns are consulted only when ``full_owner_name``
    classifies as a person, because the county's splitter mangles non-person
    strings.
    """
    raw = clean(full_owner_name)
    parts = tokens(full_owner_name)
    if not parts:
        return ParsedOwner("", OwnerType.UNKNOWN, (), None, ())

    owner_type, markers = _classify(parts)

    secondary_owner, care_of = split_care_of(
        " ".join(x for x in (clean(secondary_surname), clean(secondary_given)) if x) or None
    )

    persons: List[PersonName] = []
    if owner_type in DECEDENT_TYPES:
        decedent = parse_decedent_person(surname, given)
        if decedent is not None and decedent.surname:
            persons.append(decedent)
    elif owner_type == OwnerType.PERSON:
        primary = parse_person(surname, given)
        if primary is not None:
            persons.append(primary)
        if secondary_owner is not None:
            second = parse_person(secondary_surname, secondary_given)
            if second is not None and _classify(tokens(secondary_owner))[0] == OwnerType.PERSON:
                persons.append(second)
        if len(persons) > 1:
            owner_type = OwnerType.COUPLE

    return ParsedOwner(raw, owner_type, tuple(persons), care_of, markers)
