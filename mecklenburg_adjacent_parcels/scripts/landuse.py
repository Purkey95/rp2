"""Fold the 115 CAMA property-use codes into ten land-use classes."""

CLASSES = [
    "Single-family homes",
    "Condo & townhouse",
    "Multi-family",
    "Commercial & office",
    "Industrial & warehouse",
    "Institutional",
    "HOA common area",
    "Public land, ROW & utility",
    "Vacant, floodplain & farm",
    "Unclassified",
]
SFR, CONDO, MFR, COMM, IND, INST, HOA, PUB, VAC, UNK = range(10)


def land_class(desc):
    d = " ".join((desc or "").upper().split())
    if not d:
        return UNK
    if "COMMON" in d or d.startswith("COS "):
        return HOA
    if any(w in d for w in ("WAREHOUS", "INDUSTRIAL", "MANUFACTUR", "QUARRY", "MINING", "MINE",
                            "LUMBER YARD", "TRUCK TERMINAL")):
        return IND
    if any(w in d for w in ("SCHOOL", "COLLEGE", "CHURCH", "HOSPITAL", "FIRE DEPARTMENT", "STADIUM",
                            "ARENA", "MUNICIPAL EDUCATION", "LIBRARY", "MUSEUM", "ORPHANAGE", "NURSING",
                            "HOME FOR THE AGED")):
        return INST
    if any(w in d for w in ("REC AREA", "GREENWAY", "PARK -", "PARKLAND", "RIGHT OF WAY", "ROADWAY",
                            "OTHER COUNTY PROPERTY", "OTHER MUNICIPAL", "STATE PROP", "FEDERAL",
                            "UTILITY", "RAIL", "CELL TOWER", "AIRPORT", "WATER RETENTION",
                            "SUBMERGED LAND", "TREATMENT PLANT", "WELL LOT", "WATER PLANT",
                            "SEWER", "PUMP STATION")):
        return PUB
    if any(w in d for w in ("FLOOD", "FLUM", "SWIM", "WASTELAND", "VACANT", "NO LAND INTEREST",
                            "AGRICULTURAL", "FOREST", "HORTICULTURE", "TIMBER", "BUFFER STRIP",
                            "WETLAND", "UNSUITABLE FOR SEPTIC")):
        return VAC
    if "CONDOMINIUM" in d or "TOWN HOUSE" in d or "TOWNHOUSE" in d:
        return MFR if "MULTI FAMILY" in d else CONDO
    if ("MULTI FAMILY" in d or "MULTI FAMILTY" in d or "APARTMENT" in d or "DUPLEX" in d
            or "AFFORDABLE HOUSING" in d):
        return MFR
    if "SINGLE FAMILY" in d or "HOMESITE" in d or "MOBILE HOME" in d or "MANUFACTURED HOME" in d:
        return SFR
    if any(w in d for w in ("COMMERCIAL", "OFFICE", "MEDICAL", "RESTAURANT", "FAST FOOD", "HOTEL",
                            "MOTEL", "AUTO", "RETAIL", "STORE", "SHOPPING", "BANK", "GOLF", "CLUB",
                            "DAY CARE", "FUNERAL", "LABORATORY", "SERVICE", "THEATER", "MARINA",
                            "PARKING", "KENNEL", "GREENHOUSE", "BOWLING", "GARAGE", "SHOP",
                            "BILL BOARD", "BILLBOARD")):
        return COMM
    return UNK
