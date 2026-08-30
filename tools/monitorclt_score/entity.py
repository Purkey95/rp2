"""Owner entity resolution + behavioral signals.

Groups parcels by a normalized owner key so we can see a portfolio, not just a
parcel — the layer that produces the behavioral signals ("recently sold another",
"large portfolio") that most investors never build. Keeps entities distinct
(SMITH LLC != SMITH) so an LLC portfolio doesn't merge with a namesake person.

Pure stdlib.
"""

import re


def normalize_owner(name):
    """Canonical owner key for grouping. Lowercase, de-punctuate, sort tokens so
    order doesn't matter; handle county 'LAST, FIRST' order. Entity words (LLC,
    INC, TRUST) are KEPT — they distinguish separate legal owners."""
    if not name:
        return ""
    n = name.lower().strip()
    if "," in n and " llc" not in n and " inc" not in n:
        # person "LAST, FIRST" -> "first last"; leave company commas alone
        last, _, rest = n.partition(",")
        n = f"{rest.strip()} {last.strip()}"
    n = re.sub(r"[.,&/']", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    drop = {"jr", "sr", "ii", "iii", "iv", "mr", "mrs", "ms", "dr", "the", "and", "et", "al", "etal"}
    tokens = [t for t in n.split(" ") if t and t not in drop]
    return " ".join(sorted(tokens))


def build_portfolios(parcels, current_year, recent_years=2):
    """Return {owner_key: {parcels: [...], size, recent_sale}} plus per-parcel
    behavioral flags. recent_sale = the owner sold ANY parcel within recent_years,
    which flags their still-held parcels as 'recently_sold_another'."""
    groups = {}
    for p in parcels:
        key = normalize_owner(p.get("owner") or p.get("owner_name") or "")
        if not key:
            continue
        groups.setdefault(key, []).append(p)

    portfolios = {}
    for key, plist in groups.items():
        recent_sale = False
        for p in plist:
            sy = _year(p.get("sale_year") or p.get("sale_date"))
            if sy and (current_year - sy) <= recent_years:
                recent_sale = True
                break
        portfolios[key] = {"parcels": plist, "size": len(plist), "recent_sale": recent_sale}
    return portfolios


def _year(val):
    if not val:
        return None
    m = re.search(r"(19|20)\d{2}", str(val))
    return int(m.group(0)) if m else None
