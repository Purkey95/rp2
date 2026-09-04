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
    trust: bool = False  # the string named a trust; first/middle/last are the settlor/trustee if parseable
    order_uncertain: bool = False  # natural vs surname-first order could not be determined

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
        data["block_keys"] = sorted(block_keys(self))
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
    trust, trust_natural_order, tokens = _strip_trust(tokens)
    if trust:
        remainder_orgs = [t for t in tokens if t in org_tokens]
        if remainder_orgs or len([t for t in tokens if t != ","]) < 2:
            joined = " ".join(t for t in tokens if t != ",") or clean_text(raw)
            return Name(raw=raw or "", clean=joined, is_organization=True, markers=markers, trust=True)
        person = parse_name(" ".join(tokens), "first_last" if trust_natural_order else name_format, rules)
        person.raw = raw or ""
        person.trust = True
        person.order_uncertain = trust_natural_order and name_format == "last_first"
        return person
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
    trust_words = TRUST_ROLE_TOKENS | TRUST_NAME_TOKENS
    any_trust = any(t in ("TRUST", "TRUSTEE", "TRUSTEES", "TTEE", "TTEES") for t in body.split())
    out = [parse_name(parts[0], name_format, rules)]
    for part in parts[1:]:
        name = parse_name(part, name_format, rules)
        primary = out[0]
        tokens = [t for t in part.split() if t not in suffixes and t != "," and t not in trust_words]
        if not name.is_organization and not primary.is_organization and primary.last and 0 < len(tokens) <= 2 and not name.markers:
            name = Name(raw=part, clean=part, first=tokens[0], middle=" ".join(tokens[1:]), last=primary.last, trust=name.trust)
        out.append(name)
    if any_trust:
        for name in out:
            name.trust = True
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


# ------------------------------------------------------------------ trusts ---

TRUST_ROLE_TOKENS = {"TRUSTEE", "TRUSTEES", "TTEE", "TTEES", "TR", "TRS", "SUCCESSOR"}
TRUST_NAME_TOKENS = {
    "TRUST",
    "REVOCABLE",
    "IRREVOCABLE",
    "LIVING",
    "FAMILY",
    "AGREEMENT",
    "UDT",
    "UA",
    "U/A",
    "DTD",
    "DATED",
    "THE",
    "OF",
    "AS",
    "UNDER",
    "DECLARATION",
    "INTER",
    "VIVOS",
    "RESIDUARY",
    "MARITAL",
    "BYPASS",
    "QTIP",
    "SURVIVORS",
}


def _strip_trust(tokens: List[str]) -> "tuple[bool, bool, List[str]]":
    """Remove trust vocabulary. Returns (was_trust, natural_order_likely, remaining_tokens).

    "PUBLIC JOHN Q TRUSTEE" keeps the source's order (a trustee suffix on an assessor row).
    "JOHN Q PUBLIC REVOCABLE LIVING TRUST" is almost always written in natural order.
    """
    if not any(t in TRUST_ROLE_TOKENS or t in TRUST_NAME_TOKENS for t in tokens):
        return False, False, tokens
    if not any(t in ("TRUST", "TRUSTEE", "TRUSTEES", "TTEE", "TTEES") for t in tokens):
        return False, False, tokens
    natural = any(t in ("TRUST", "REVOCABLE", "IRREVOCABLE", "LIVING", "FAMILY") for t in tokens) and not any(t in TRUST_ROLE_TOKENS for t in tokens)
    kept = [t for t in tokens if t not in TRUST_ROLE_TOKENS and t not in TRUST_NAME_TOKENS and not re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2,4}|\d{4}|\d{1,2}", t)]
    return True, natural, kept


# --------------------------------------------------------------- nicknames ---

_NICKNAME_GROUPS = [
    ("WILLIAM", "BILL", "BILLY", "WILL", "WILLIE", "LIAM"),
    ("ROBERT", "BOB", "BOBBY", "ROB", "ROBBIE", "BERT"),
    ("JAMES", "JIM", "JIMMY", "JAMIE"),
    ("JOHN", "JACK", "JOHNNY", "JOHNNIE"),
    ("RICHARD", "DICK", "RICK", "RICKY", "RICH"),
    ("MICHAEL", "MIKE", "MIKEY", "MICK"),
    ("CHARLES", "CHUCK", "CHARLIE", "CHAS"),
    ("THOMAS", "TOM", "TOMMY"),
    ("EDWARD", "ED", "EDDIE", "TED", "TEDDY", "NED"),
    ("JOSEPH", "JOE", "JOEY"),
    ("DANIEL", "DAN", "DANNY"),
    ("ANTHONY", "TONY"),
    ("DAVID", "DAVE", "DAVEY"),
    ("STEVEN", "STEPHEN", "STEVE"),
    ("KENNETH", "KEN", "KENNY"),
    ("RONALD", "RON", "RONNIE"),
    ("DONALD", "DON", "DONNIE"),
    ("GERALD", "JERRY", "GERRY"),
    ("LAWRENCE", "LARRY"),
    ("HAROLD", "HAL", "HARRY"),
    ("WALTER", "WALT", "WALLY"),
    ("HENRY", "HANK", "HARRY"),
    ("SAMUEL", "SAM", "SAMMY"),
    ("BENJAMIN", "BEN", "BENNY"),
    ("ALEXANDER", "ALEX", "AL"),
    ("ALBERT", "AL", "BERT"),
    ("RAYMOND", "RAY"),
    ("EUGENE", "GENE"),
    ("FRANCIS", "FRANK", "FRANKIE"),
    ("FREDERICK", "FRED", "FREDDIE"),
    ("LEONARD", "LEN", "LEONARD", "LENNY"),
    ("NICHOLAS", "NICK", "NICKY"),
    ("PATRICK", "PAT", "PADDY"),
    ("PETER", "PETE"),
    ("TIMOTHY", "TIM", "TIMMY"),
    ("MARGARET", "PEGGY", "MAGGIE", "MEG", "MARGE", "PEG", "MARGIE"),
    ("ELIZABETH", "LIZ", "BETH", "BETTY", "BETSY", "ELIZA", "LIBBY", "LISA"),
    ("KATHERINE", "CATHERINE", "KATHRYN", "KATHY", "CATHY", "KATE", "KATIE", "KAY"),
    ("DOROTHY", "DOT", "DOTTIE", "DOLLY"),
    ("SUSAN", "SUE", "SUSIE", "SUZANNE"),
    ("DEBORAH", "DEBRA", "DEBBIE", "DEB"),
    ("PATRICIA", "PAT", "PATTY", "PATTI", "TRISH", "TRICIA"),
    ("BARBARA", "BARB", "BARBIE", "BABS"),
    ("JENNIFER", "JEN", "JENNY"),
    ("REBECCA", "BECKY", "BECCA"),
    ("VIRGINIA", "GINNY", "GINGER"),
    ("FLORENCE", "FLO", "FLOSSIE"),
    ("FRANCES", "FRAN", "FRANNIE"),
    ("HELEN", "NELL", "NELLIE"),
    ("ELEANOR", "ELLIE", "NORA"),
    ("JACQUELINE", "JACKIE"),
    ("CHRISTOPHER", "CHRIS", "KIT"),
    ("CHRISTINE", "CHRISTINA", "CHRIS", "TINA"),
    ("MARY", "MOLLY", "POLLY", "MAE"),
    ("SARAH", "SARA", "SALLY"),
    ("ANN", "ANNE", "ANNIE", "NANCY", "NAN"),
    ("SANDRA", "SANDY"),
    ("LINDA", "LYNN"),
    ("TERESA", "THERESA", "TERRY", "TESS"),
    ("VICTORIA", "VICKY", "VICKI", "TORI"),
]
_CANONICAL: Dict[str, str] = {}
for _group in _NICKNAME_GROUPS:
    for _alias in _group:
        _CANONICAL.setdefault(_alias, _group[0])


def canonical_first(first: str) -> str:
    """WILLIAM for BILL, KATHERINE for CATHY; the name itself when unknown."""
    return _CANONICAL.get(first, first)


# ---------------------------------------------------------------- phonetic ---

_SOUNDEX = {**dict.fromkeys("BFPV", "1"), **dict.fromkeys("CGJKQSXZ", "2"), **dict.fromkeys("DT", "3"), "L": "4", **dict.fromkeys("MN", "5"), "R": "6"}


def soundex(word: str) -> str:
    word = "".join(ch for ch in word.upper() if ch.isalpha())
    if not word:
        return ""
    out = word[0]
    last = _SOUNDEX.get(word[0], "")
    for ch in word[1:]:
        code = _SOUNDEX.get(ch, "")
        if code and code != last:
            out += code
        if ch not in "HW":
            last = code
    return (out + "000")[:4]


# --------------------------------------------------------------- relations ---


def first_name_relation(a: str, b: str) -> str:
    """exact | nickname | initial | different, for two forenames.

    Deliberately no phonetic tier for forenames: JOHN and JANE share a soundex code,
    and a forename collision is exactly the error a reviewer cannot see past.
    """
    if not a or not b:
        return "different"
    if a == b:
        return "exact"
    if canonical_first(a) == canonical_first(b):
        return "nickname"
    if (len(a) == 1 or len(b) == 1) and a[0] == b[0]:
        return "initial"
    return "different"


def last_name_relation(a: str, b: str) -> str:
    if not a or not b:
        return "different"
    if a == b:
        return "exact"
    if soundex(a) == soundex(b):
        return "phonetic"
    return "different"


def block_keys(name: Name) -> List[str]:
    """Every key under which this name should be findable. Exact first|last is always
    among them; nickname, initial and phonetic keys widen recall and are tagged so the
    resolver can tell how the candidate was found. Phonetic widening applies to the
    surname only (SMITH/SMYTHE); forenames must agree exactly, by nickname, or by initial."""
    if name.is_organization or not (name.first and name.last):
        return []
    keys = set()
    pairs = [(name.first, name.last)]
    if name.order_uncertain and name.middle:
        # "JOHN Q PUBLIC" read as surname-first would have been (Q, JOHN) -- include the swap.
        pairs.append((name.last, name.first))
    for first, last in pairs:
        keys.add("{0}|{1}".format(first, last))
        keys.add("{0}|{1}".format(canonical_first(first), last))
        keys.add("{0}.|{1}".format(first[0], last))
        keys.add("~{0}|{1}".format(canonical_first(first), soundex(last)))  # surname phonetic only
    return sorted(keys)
