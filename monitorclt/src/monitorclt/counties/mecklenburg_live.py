"""Mecklenburg County live sources on the City of Charlotte ArcGIS server.

  parcel            Accela/Accela/MapServer/16 "Parcel XAPO": the assessor ownership roll
                    (owner, co-owner, situs, mailing, values, sale, deed book/page, use)
  code_enforcement  HNS/CodeEnforcementCasesAll/MapServer/0 (430k cases, by ParcelId)
  lien              ODP/FMSLienData/MapServer/0 (city liens: nuisance, demolition, housing)
  vacant_land       PLN/VacantLand/MapServer/0 (parcels the city flags as vacant)
  address_point     CountyData/MasterAddress/MapServer/0 (676k address points with
                    coordinates and parcel id: the county-wide geocoder)

Not used, on purpose: the CMPD crime layers on the same server. They are not facts
about a property and they are the kind of signal this system does not take.

Estates (Clerk of Superior Court) and deeds (Register of Deeds) are not on this
server; those connectors stay on their own sources.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .. import events as ev
from ..sources.arcgis import ArcGisLayerConnector, epoch_ms_to_date, strip
from ..sources.base import SourceSpec, registry

GIS = "https://gis.charlottenc.gov/arcgis/rest/services"

# ------------------------------------------------------------ owner strings ---

_ESTATE_WORDS = ("ESTATE OF", "ESTATE", "HEIRS OF", "HEIRS", "THE ESTATE OF", "EST OF")
_ROUTING = re.compile(r"^(C/O|C O|ATTN|ATTENTION|%)\s*", re.I)


def compose_owner(last: Optional[str], first: Optional[str]) -> Optional[str]:
    """The assessor splits one string into two columns without a rule the parser can
    trust. Rebuild a single string in the order our name parser expects.

      ("PONDS", "CLARLISSA MAE")                 -> "PONDS CLARLISSA MAE"        (surname-first)
      ("ESTATE OF ", "GRACIELA G WALL")           -> "ESTATE OF GRACIELA G WALL"  (natural)
      ("KEITH ROBIN DUTOIT", "ESTATE")            -> "ESTATE OF KEITH ROBIN DUTOIT"
      ("JOHNNIE M DOUGLAS ", "ESTATE OF")         -> "ESTATE OF JOHNNIE M DOUGLAS"
      ("VENTURI", "THE ESTATE OF KATHRYN")        -> "ESTATE OF KATHRYN VENTURI"
      ("BLACK", "CLINT M (HEIRS)")                -> "BLACK CLINT M HEIRS"
      ("KAREN L JOHNSON LIVING", "TRUST")         -> "KAREN L JOHNSON LIVING TRUST" (natural; trust parser)
      ("SHON REALESTATE LLC", None)               -> "SHON REALESTATE LLC"
    """
    last = (last or "").strip()
    first = (first or "").strip()
    if not last and not first:
        return None
    lu, fu = last.upper(), first.upper()
    if fu in ("ESTATE", "ESTATE OF", "EST", "EST OF", "THE ESTATE OF"):
        return "ESTATE OF " + last
    if lu in ("ESTATE OF", "THE ESTATE OF", "ESTATE"):
        return "ESTATE OF " + first
    if lu in ("TRUST", "THE TRUST", "REVOCABLE TRUST", "LIVING TRUST", "FAMILY TRUST") and first:
        return "{0} {1}".format(first, last)  # ("TRUST", "AMENDED AND RESTATED WILLIAM CARSON") -> "... WILLIAM CARSON TRUST"
    m = re.match(r"^(THE\s+)?ESTATE OF\s+(.+)$", fu)
    if m:
        return "ESTATE OF {0} {1}".format(m.group(2), last)
    if fu in ("TRUST", "TRUSTEE", "TRUSTEES", "TTEE", "TTEES", "REVOCABLE TRUST", "LIVING TRUST", "FAMILY TRUST", "IRREVOCABLE TRUST"):
        return "{0} {1}".format(last, first)  # natural order; the trust parser flips it
    if not first:
        return last
    return "{0} {1}".format(last, first)


def split_routing(last: Optional[str], first: Optional[str]) -> "tuple[Optional[str], Optional[str]]":
    """A co-owner column that starts with C/O or ATTN is a contact, not an owner.
    Returns (owner_string, care_of_name)."""
    last = (last or "").strip()
    first = (first or "").strip()
    if not last and not first:
        return None, None
    if _ROUTING.match(first) and last:
        return None, "{0} {1}".format(_ROUTING.sub("", first).strip(), last).strip()  # ("PAUL K THAMES", "C/O") -> the person, natural order as printed
    if _ROUTING.match(last):
        return None, _ROUTING.sub("", last).strip() + (" " + first if first else "")
    return compose_owner(last, first), None


def situs(attrs: Dict[str, Any]) -> Optional[str]:
    parts = [strip(attrs.get("houseno")), strip(attrs.get("stdir")), strip(attrs.get("stname")), strip(attrs.get("sttype")), strip(attrs.get("stsuffix"))]
    line = " ".join(p for p in parts if p)
    unit = strip(attrs.get("houseunit"))
    if unit:
        line += " # " + unit
    muni = strip(attrs.get("municipality"))
    if not line:
        return None
    return "{0}, {1} NC".format(line, muni) if muni else line


def mailing(attrs: Dict[str, Any]) -> Optional[str]:
    parts = [strip(attrs.get("mailaddr1")), strip(attrs.get("mailaddr2"))]
    city = " ".join(p for p in (strip(attrs.get("city")), strip(attrs.get("state")), strip(attrs.get("zipcode"))) if p)
    line = " ".join(p for p in parts if p)
    if not line and not city:
        return None
    return "{0}, {1}".format(line, city) if line and city else (line or city)


# ------------------------------------------------------------------ specs ---

PARCEL_LIVE = SourceSpec(
    name="parcel",
    mode="snapshot",
    key_fields=("pin",),
    effective_field=None,
    name_format="last_first",
    mention_roles={"owner_name": "owner", "care_of_name": "care_of"},
    address_fields={"situs_address": "situs", "owner_mailing_address": "tax_mailing"},
    parcel_fields=("pin",),
    required_fields=("pin", "owner_name"),
    event_rules=[
        ev.on_field_change("owner_name", ev.OWNER_CHANGED),
        ev.on_field_change("owner_mailing_address", ev.MAILING_ADDRESS_CHANGED),
        ev.on_value_change("assessed_value", ev.ASSESSED_VALUE_CHANGED, min_pct=0.02),
        ev.on_field_change("last_sale_date", ev.DEED_RECORDED, "last_sale_date"),
    ],
    description="Assessor ownership roll (Accela Parcel XAPO on the city GIS server).",
)

CODE_ENFORCEMENT_LIVE = SourceSpec(
    name="code_enforcement",
    mode="incremental",
    key_fields=("case_number",),
    effective_field="opened_date",
    name_format="last_first",
    mention_roles={},
    address_fields={"property_address": "situs"},
    parcel_fields=("parcel_pin",),
    required_fields=("case_number", "opened_date"),
    event_rules=[
        ev.on_create(ev.CODE_CASE_OPENED, "opened_date", ("violation_type", "property_address", "parcel_pin", "origin")),
        ev.on_status("status", ("CLOSED",), ev.CODE_CASE_OPENED + "_status", ev.CODE_CASE_CLOSED, "closed_date"),
        ev.on_field_change("finding_of_fact_ordered", "code_finding_of_fact"),
    ],
    description="City code enforcement cases: housing, nuisance, zoning, commercial (HNS).",
)

LIEN = SourceSpec(
    name="lien",
    mode="snapshot",
    key_fields=("lien_number",),
    effective_field="invoice_date",
    name_format="first_last",
    mention_roles={"customer_name": "debtor"},
    address_fields={"property_address": "situs"},
    parcel_fields=("parcel_pin",),
    required_fields=("lien_number",),
    event_rules=[
        ev.on_create("lien_recorded", "invoice_date", ("status", "invoice_number")),
        ev.on_field_change("status", "lien_status_changed"),
    ],
    description="City liens from the financial system: nuisance abatement, demolition, housing invoices.",
)

VACANT_LAND = SourceSpec(
    name="vacant_land",
    mode="snapshot",
    key_fields=("pin",),
    name_format="last_first",
    mention_roles={},
    address_fields={},
    parcel_fields=("pin",),
    required_fields=("pin",),
    event_rules=[ev.on_create("vacant_land_flagged", None, ("land_use", "total_acres"))],
    description="Parcels the planning department flags as vacant land.",
)

ADDRESS_POINT = SourceSpec(
    name="address_point",
    mode="snapshot",
    key_fields=("address_id",),
    name_format="last_first",
    mention_roles={},
    address_fields={"full_address": "situs"},
    parcel_fields=("parcel_pin",),
    required_fields=("address_id", "full_address"),
    event_rules=[],
    retention_days=3650,
    description="Master address points with coordinates and parcel id; feeds the geocode cache.",
)


# ------------------------------------------------------------- connectors ---


class ParcelXapoConnector(ArcGisLayerConnector):
    spec = PARCEL_LIVE
    layer_url = GIS + "/Accela/Accela/MapServer/16"
    page_size = 4000
    return_geometry = False

    def parse(self, body: bytes, content_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """XAPO has one row per building card; ownership is per parcel. Keep the lowest card."""
        import json as _json

        payload = _json.loads(body.decode("utf-8"))
        if payload.get("error"):
            raise ValueError("ArcGIS error: {0}".format(payload["error"]))
        best: Dict[str, "tuple[int, Dict[str, Any]]"] = {}
        for feature in payload.get("features", []):
            attrs = feature.get("attributes") or {}
            rec = self.to_record(attrs, feature.get("geometry"))
            if rec is None:
                continue
            card = int(attrs.get("cardno") or 1)
            if rec["pin"] not in best or card < best[rec["pin"]][0]:
                best[rec["pin"]] = (card, rec)
        return [rec for _, rec in best.values()]

    def to_record(self, a: Dict[str, Any], geometry: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        pid = strip(a.get("pid"))
        if not pid:
            return None
        owner = compose_owner(a.get("ownerlastname"), a.get("ownerfirstname"))
        co_owner, care_of = split_routing(a.get("cownerlastname"), a.get("cownerfirstname"))
        owner_name = " & ".join(p for p in (owner, co_owner) if p) or None
        rec = {
            "county": self.county,
            "pin": pid,
            "nc_pin": strip(a.get("nc_pin")),
            "owner_name": owner_name,
            "care_of_name": care_of,
            "owner_last_raw": strip(a.get("ownerlastname")),
            "owner_first_raw": strip(a.get("ownerfirstname")),
            "situs_address": situs(a),
            "owner_mailing_address": mailing(a),
            "land_use": strip(a.get("descpropertyuse")) or strip(a.get("landusecode")),
            "property_use_code": strip(a.get("propertyusecode")),
            "assessed_value": a.get("totalvalue"),
            "land_value": a.get("landvalue"),
            "building_value": a.get("netbldgvalue"),
            "last_sale_date": epoch_ms_to_date(a.get("dateofsale")),
            "last_sale_price": a.get("price"),
            "deed_book": strip(a.get("deedbook")),
            "deed_page": strip(a.get("deedpage")),
            "deed_type": strip(a.get("typeofdeed")),
            "year_built": a.get("yearbuilt"),
            "heated_area": a.get("heatedarea"),
            "bedrooms": a.get("bedrooms"),
            "full_baths": a.get("fullbaths"),
            "account_type": strip(a.get("accounttype")),
            "vacant_or_improved": strip(a.get("vacantorimproved")),
            "municipality": strip(a.get("municipality")),
            "total_acres": a.get("totalac"),
            "legal_description": strip(a.get("parlegaldesc")),
        }
        if geometry:
            from ..sources.arcgis import ring_centroid

            c = ring_centroid(geometry)
            if c:
                rec.update(c)
        return rec


class CodeEnforcementLiveConnector(ArcGisLayerConnector):
    spec = CODE_ENFORCEMENT_LIVE
    layer_url = GIS + "/HNS/CodeEnforcementCasesAll/MapServer/0"
    page_size = 2000
    incremental_field = "DateCreated"

    def to_record(self, a: Dict[str, Any], geometry: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        case = strip(a.get("CaseNumber"))
        if not case:
            return None
        return {
            "county": self.county,
            "case_number": case,
            "parcel_pin": strip(a.get("ParcelId")),
            "violation_type": strip(a.get("CaseType")),
            "property_address": strip(a.get("FullAddress")),
            "origin": strip(a.get("CaseOrigin")),
            "opened_date": epoch_ms_to_date(a.get("DateCreated")),
            "closed_date": epoch_ms_to_date(a.get("DateClosed")),
            "status": (strip(a.get("CaseStatus")) or "").upper() or None,
            "conclusion": strip(a.get("Conclusion")),
            "finding_of_fact_ordered": int(a.get("FOFOrdered") or 0),
            "council_district": strip(a.get("CouncilDistrict")),
            "request_311": strip(a.get("ReqNum311")),
        }


class LienConnector(ArcGisLayerConnector):
    spec = LIEN
    layer_url = GIS + "/ODP/FMSLienData/MapServer/0"
    order_by = "ObjectID"
    page_size = 2000

    def to_record(self, a: Dict[str, Any], geometry: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        lien_no = strip(a.get("LienNo"))
        if not lien_no:
            return None
        status = strip(a.get("Lien_Status")) or ""
        code, _, label = status.partition(" - ")
        inv = strip(a.get("Invoice_Date"))
        invoice_date = None
        if inv and re.fullmatch(r"\d{2}-\d{2}-\d{4}", inv):
            invoice_date = "{0}-{1}-{2}".format(inv[6:], inv[0:2], inv[3:5])
        return {
            "county": self.county,
            "lien_number": lien_no,
            "status": code.strip() or None,
            "status_label": label.strip() or status or None,
            "customer_name": strip(a.get("Customer_Name")),
            "property_address": strip(a.get("Property_Address")),
            "invoice_number": strip(a.get("InvoiceNo")),
            "invoice_date": invoice_date,
            "parcel_pin": strip(a.get("ParcelID")),
        }


class VacantLandConnector(ArcGisLayerConnector):
    spec = VACANT_LAND
    layer_url = GIS + "/PLN/VacantLand/MapServer/0"
    page_size = 2000

    def to_record(self, a: Dict[str, Any], geometry: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        pid = strip(a.get("pid"))
        if not pid:
            return None
        return {
            "county": self.county,
            "pin": pid,
            "land_use": strip(a.get("descpropertyuse")),
            "total_acres": a.get("totalac"),
            "full_address": strip(a.get("FULL_ADDRESS")),
        }


class AddressPointConnector(ArcGisLayerConnector):
    spec = ADDRESS_POINT
    layer_url = GIS + "/CountyData/MasterAddress/MapServer/0"
    page_size = 5000
    return_geometry = True

    def to_record(self, a: Dict[str, Any], geometry: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        aid = a.get("AddressID")
        full = strip(a.get("FullAddress"))
        if aid in (None, "") or not full:
            return None
        g = geometry or {}
        return {
            "county": self.county,
            "address_id": str(aid),
            "full_address": full,
            "zip": strip(a.get("ZipCode")),
            "parcel_pin": strip(a.get("TaxParcelID")) or strip(a.get("ParcelID")),
            "status": strip(a.get("StatusCode")),
            "lat": g.get("y"),
            "lon": g.get("x"),
        }


LIVE_CONNECTORS = [ParcelXapoConnector, CodeEnforcementLiveConnector, LienConnector, VacantLandConnector, AddressPointConnector]


def connectors(endpoints: Optional[Dict[str, str]] = None, county: str = "MECKLENBURG") -> List[ArcGisLayerConnector]:
    endpoints = endpoints or {}
    return [cls(county, endpoints.get(cls.spec.name)) for cls in LIVE_CONNECTORS]


def fixture_endpoints() -> Dict[str, str]:
    """Endpoint overrides that replay the captured pages under fixtures/mecklenburg/live."""
    return {cls.spec.name: "fixture://{0}".format(cls.spec.name) for cls in LIVE_CONNECTORS}


registry.register_county("MECKLENBURG", connectors, profile="live")
