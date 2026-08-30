"""Bucket the owner names returned by the broad LIKE search into county / affiliate groups."""

COUNTY = "County government"
SCHOOLS = "CMS (Board of Education)"
BOARDS = "Library / ABC / Landmarks"
HOSPITAL = "Hospital Authority (Atrium)"

_COUNTY_NAMES = {
    "MECKLENBURG COUNTY",
    "COUNTY OF MECKLENBURG",
    "COUNTY OF MECKLENBURG DERITA ESTATES",
    "MECKLENBURG COUNTY PARKS AND RECREATION DEPARTMENT",
    "MECKLENBURG COUNTY PARKS & REC COMMISSION",
    "MECKLENBURG COUNTY CHRONIC DISEASE",
    "MECKLENBURG COUNTY TAX COLLECTOR",
}


def bucket(name):
    """Return the owner group for a full_owner_name, or None if it is not the county/an affiliate."""
    n = " ".join((name or "").upper().split())
    if n in _COUNTY_NAMES:
        return COUNTY
    if "BOARD OF EDUCATION" in n and "MECKLENBURG" in n:
        return SCHOOLS
    if "HOSPITAL AUTHORITY" in n and "MECKLENBURG" in n:
        return HOSPITAL
    if "ALCOHOLIC BEV" in n or "ABC BOARD" in n:
        return BOARDS
    if "PUBLIC LIBRARY" in n or "LIBRARY OF CHAR" in n:
        return BOARDS
    if "LANDMARK" in n and "COMMIS" in n:
        return BOARDS
    if "INDUST & POLLUTION CONT FINANCING" in n:
        return BOARDS
    return None  # private owner that merely has "Mecklenburg"/"Meck Co" in its name
