"""Address and name normalization for parcel<->contact joining.

Address matching is the crux of enrichment: the same property is written a dozen
ways across county deed data, CRM entry, and parcel files ("123 North Main
Street" vs "123 N MAIN ST"). Everything here reduces those variants to one
canonical key so an exact-string join actually lands.

Pure stdlib, no external dependencies, so it runs anywhere on the MonitorCLT host.
"""

import re

# USPS C1 street-suffix abbreviations (common subset). Maps long/variant -> canonical.
_SUFFIX = {
    "street": "st", "str": "st", "st": "st",
    "avenue": "ave", "av": "ave", "ave": "ave",
    "boulevard": "blvd", "blvd": "blvd",
    "drive": "dr", "dr": "dr",
    "road": "rd", "rd": "rd",
    "lane": "ln", "ln": "ln",
    "court": "ct", "ct": "ct",
    "place": "pl", "pl": "pl",
    "circle": "cir", "cir": "cir",
    "trail": "trl", "trl": "trl",
    "parkway": "pkwy", "pkwy": "pkwy",
    "highway": "hwy", "hwy": "hwy",
    "terrace": "ter", "ter": "ter",
    "way": "way", "cove": "cv", "cv": "cv",
    "loop": "loop", "run": "run", "pass": "pass",
    "square": "sq", "sq": "sq", "crossing": "xing", "xing": "xing",
}

# Directionals -> canonical single/double letter.
_DIR = {
    "north": "n", "south": "s", "east": "e", "west": "w",
    "northeast": "ne", "northwest": "nw", "southeast": "se", "southwest": "sw",
    "n": "n", "s": "s", "e": "e", "w": "w",
    "ne": "ne", "nw": "nw", "se": "se", "sw": "sw",
}

# Secondary-unit designators whose value we strip (apt/unit/ste numbers vary and
# hurt matching on the primary parcel).
_UNIT_WORDS = {"apt", "unit", "ste", "suite", "#", "bldg", "building", "fl", "floor", "rm", "room", "lot"}


def normalize_address(street, zip_code=None):
    """Canonicalize a street line into a match key.

    Returns a lowercased, punctuation-free, USPS-abbreviated string. When a zip
    is supplied it is appended so two different towns that share a street name
    don't collide. Returns "" for empty/garbage input so callers can skip it.
    """
    if not street:
        return ""
    s = street.lower().strip()
    s = re.sub(r"[.,]", " ", s)          # drop periods/commas
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return ""

    tokens = s.split(" ")
    out = []
    skip_next = False
    for i, tok in enumerate(tokens):
        if skip_next:
            skip_next = False
            continue
        # Strip a secondary-unit designator and the token that follows it.
        if tok in _UNIT_WORDS:
            skip_next = True
            continue
        if tok.startswith("#"):
            continue
        # Directional: only collapse when it isn't the whole street name.
        if tok in _DIR and len(tokens) > 2:
            out.append(_DIR[tok])
            continue
        # Suffix: collapse only in trailing position (a "St" mid-name is a name).
        if tok in _SUFFIX and i >= len(tokens) - 2:
            out.append(_SUFFIX[tok])
            continue
        out.append(tok)

    key = " ".join(out).strip()
    z = (zip_code or "").strip()[:5]
    return f"{key} {z}".strip() if z else key


def normalize_name(name):
    """Canonicalize a person/owner name for comparison.

    Handles "LAST, FIRST" county format, strips common entity suffixes and
    honorifics, drops punctuation, and sorts tokens so "John Smith" and
    "Smith John" match. Returns "" for empty input.
    """
    if not name:
        return ""
    n = name.lower().strip()
    # county deed data is often "SMITH, JOHN A" -> "john a smith"
    if "," in n:
        last, _, rest = n.partition(",")
        n = f"{rest.strip()} {last.strip()}"
    n = re.sub(r"[.,&/]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()

    drop = {"jr", "sr", "ii", "iii", "iv", "mr", "mrs", "ms", "dr",
            "the", "et", "al", "etal", "and", "trustee", "trust", "estate",
            "llc", "inc", "corp", "co", "ltd", "lp", "revocable", "living", "family"}
    tokens = [t for t in n.split(" ") if t and t not in drop]
    if not tokens:
        return ""
    # Sort so token order doesn't matter; join for a stable comparison key.
    return " ".join(sorted(tokens))


def is_absentee(situs_zip, situs_street, mail_zip, mail_street):
    """True when the owner's mailing address differs from the property address.

    Absentee/out-of-state ownership is one of the strongest sell-likelihood
    signals in land, and it's computable from parcel data alone. Compares the
    normalized street+zip on each side.
    """
    if not (mail_street and mail_zip):
        return None  # can't tell
    situs = normalize_address(situs_street, situs_zip)
    mail = normalize_address(mail_street, mail_zip)
    if not situs or not mail:
        return None
    return situs != mail
