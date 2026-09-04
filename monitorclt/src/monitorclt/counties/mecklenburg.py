"""Mecklenburg County, NC: the six sources MonitorCLT watches, declared.

Endpoints default to recorded fixtures so the whole pipeline runs offline and the
contract tests have something to hold the parsers to. A deployment points each
connector at its live endpoint via `endpoints={...}`; the parsers stay the same
because raw capture keeps the bytes and the fixtures are exactly those bytes.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from .. import events as ev
from ..sources.base import Connector, Fetched, SourceSpec, parse_csv, parse_jsonl, registry
from ..sources.html_tables import find_table
from ..sources.transport import Transport

COUNTY = "MECKLENBURG"

ESTATE_CASE = SourceSpec(
    name="estate_case",
    mode="incremental",
    key_fields=("file_number",),
    effective_field="filing_date",
    name_format="first_last",
    mention_roles={"decedent_name": "decedent", "personal_rep_name": "personal_rep"},
    address_fields={"pr_mailing_address": "court_filing"},
    required_fields=("file_number", "decedent_name"),
    event_rules=[
        ev.on_create(ev.ESTATE_OPENED, "filing_date", ("decedent_name", "personal_rep_name")),
        ev.on_status("case_status", ("CLOSED", "DISPOSED"), ev.ESTATE_STATUS_CHANGED, ev.ESTATE_CLOSED),
    ],
    retention_days=3650,
    description="Clerk of Superior Court, estates division: decedent, file number, personal representative.",
)

PARCEL = SourceSpec(
    name="parcel",
    mode="snapshot",
    key_fields=("pin",),
    effective_field=None,
    name_format="last_first",
    mention_roles={"owner_name": "owner"},
    address_fields={"situs_address": "situs", "owner_mailing_address": "tax_mailing"},
    parcel_fields=("pin",),
    required_fields=("pin", "owner_name"),
    event_rules=[
        ev.on_field_change("owner_name", ev.OWNER_CHANGED),
        ev.on_field_change("owner_mailing_address", ev.MAILING_ADDRESS_CHANGED),
        ev.on_value_change("assessed_value", ev.ASSESSED_VALUE_CHANGED, min_pct=0.02),
    ],
    description="County assessor / GIS parcel roll: owner string, situs, tax mailing, land use, value.",
)

DEED = SourceSpec(
    name="deed",
    mode="incremental",
    key_fields=("instrument_number",),
    effective_field="recorded_date",
    name_format="last_first",
    mention_roles={"grantor_name": "grantor", "grantee_name": "grantee"},
    address_fields={},
    parcel_fields=("parcel_pin",),
    required_fields=("instrument_number", "recorded_date"),
    event_rules=[ev.on_create(ev.DEED_RECORDED, "recorded_date", ("instrument_type", "grantor_name", "grantee_name", "parcel_pin"))],
    description="Register of Deeds index: grantor, grantee, instrument type, PIN as printed.",
)

FORECLOSURE = SourceSpec(
    name="foreclosure",
    mode="incremental",
    key_fields=("file_number",),
    effective_field="filing_date",
    name_format="first_last",
    mention_roles={"owner_name": "debtor", "trustee_name": "trustee", "lender_name": "lender"},
    address_fields={"property_address": "situs"},
    parcel_fields=("parcel_pin",),
    required_fields=("file_number", "filing_date"),
    event_rules=[
        ev.on_create(ev.FORECLOSURE_FILED, "filing_date", ("owner_name", "lender_name", "property_address", "parcel_pin")),
        ev.on_field_change("hearing_date", ev.FORECLOSURE_HEARING_SET, "hearing_date"),
        ev.on_status("status", ("DISMISSED", "WITHDRAWN", "SOLD"), ev.FORECLOSURE_STATUS_CHANGED, ev.FORECLOSURE_STATUS_CHANGED),
    ],
    description="Clerk of Superior Court special proceedings: substitute-trustee foreclosure filings.",
)

TAX_DELINQUENCY = SourceSpec(
    name="tax_delinquency",
    mode="snapshot",
    key_fields=("pin", "tax_year"),
    effective_field=None,
    name_format="last_first",
    mention_roles={"owner_name": "owner"},
    address_fields={"situs_address": "situs"},
    parcel_fields=("pin",),
    required_fields=("pin", "tax_year"),
    event_rules=[ev.on_create(ev.TAX_DELINQUENT, None, ("tax_year", "amount_due", "years_delinquent"))],
    description="Tax collector delinquent list: PIN, tax year, amount due. Rows leaving the list clear.",
)

CODE_ENFORCEMENT = SourceSpec(
    name="code_enforcement",
    mode="incremental",
    key_fields=("case_number",),
    effective_field="opened_date",
    name_format="last_first",
    mention_roles={"owner_name": "owner"},
    address_fields={"property_address": "situs"},
    parcel_fields=("parcel_pin",),
    required_fields=("case_number", "opened_date"),
    event_rules=[
        ev.on_create(ev.CODE_CASE_OPENED, "opened_date", ("violation_type", "property_address", "parcel_pin")),
        ev.on_status("status", ("CLOSED", "COMPLIED", "RESOLVED"), ev.CODE_CASE_OPENED + "_status", ev.CODE_CASE_CLOSED, "closed_date"),
    ],
    description="Code enforcement / housing cases: violation type, address, status.",
)

BUSINESS_ENTITY = SourceSpec(
    name="business_entity",
    mode="incremental",
    key_fields=("sos_id",),
    effective_field="formed_date",
    name_format="first_last",
    mention_roles={"entity_name": "entity", "registered_agent_name": "registered_agent", "manager_names": "manager"},
    address_fields={"principal_address": "principal", "agent_address": "agent"},
    required_fields=("sos_id", "entity_name"),
    event_rules=[
        ev.on_create(ev.ENTITY_REGISTERED, "formed_date", ("entity_name", "entity_type", "status")),
        ev.on_status("status", ("DISSOLVED", "ADMIN DISSOLVED", "REVOKED", "WITHDRAWN"), ev.ENTITY_STATUS_CHANGED, ev.ENTITY_DISSOLVED),
    ],
    description="NC Secretary of State business registry: entity, status, registered agent, managers. Turns LLC owners into people.",
)

DEFAULT_ENDPOINTS: Dict[str, str] = {
    "estate_case": "fixture://estate_cases.html",
    "parcel": "fixture://parcels.jsonl",
    "deed": "fixture://deeds.jsonl",
    "foreclosure": "fixture://foreclosures.jsonl",
    "tax_delinquency": "fixture://tax_delinquency.csv",
    "code_enforcement": "fixture://code_enforcement.jsonl",
    "business_entity": "fixture://business_entities.jsonl",
}


class _Base(Connector):
    spec: SourceSpec

    def __init__(self, county: str = COUNTY, endpoint: Optional[str] = None) -> None:
        super().__init__(county)
        self.endpoint = endpoint or DEFAULT_ENDPOINTS[self.spec.name]

    def fetch(self, transport: Transport, watermark: Optional[str]) -> Iterable[Fetched]:
        params = {"since": watermark} if watermark and self.spec.mode == "incremental" else None
        body, ctype = transport.get(self.endpoint, params)
        yield Fetched(body=body, url=self.endpoint, content_type=ctype)

    def parse(self, body: bytes, content_type: Optional[str] = None) -> List[Dict[str, Any]]:
        ctype = (content_type or "").lower()
        if "csv" in ctype:
            return parse_csv(body)
        return parse_jsonl(body)

    def clean(self, record: Dict[str, Any]) -> Dict[str, Any]:
        rec = super().clean(record)
        rec.setdefault("county", self.county)
        return rec


class EstateCaseConnector(_Base):
    spec = ESTATE_CASE
    COLUMNS = {
        "File Number": "file_number",
        "Decedent": "decedent_name",
        "Date of Death": "date_of_death",
        "Filed": "filing_date",
        "Status": "case_status",
        "Personal Representative": "personal_rep_name",
        "PR Mailing Address": "pr_mailing_address",
    }

    def parse(self, body: bytes, content_type: Optional[str] = None) -> List[Dict[str, Any]]:
        if "html" in (content_type or "").lower() or body.lstrip()[:1] == b"<":
            rows = find_table(body, ["File Number", "Decedent"])
            return [{self.COLUMNS.get(k, k): (v or None) for k, v in row.items()} for row in rows]
        return super().parse(body, content_type)


class ParcelConnector(_Base):
    spec = PARCEL


class DeedConnector(_Base):
    spec = DEED

    def clean(self, record: Dict[str, Any]) -> Dict[str, Any]:
        rec = super().clean(record)
        if not rec.get("instrument_number") and rec.get("book") and rec.get("page"):
            rec["instrument_number"] = "{0}/{1}".format(rec["book"], rec["page"])
        return rec


class ForeclosureConnector(_Base):
    spec = FORECLOSURE


class TaxDelinquencyConnector(_Base):
    spec = TAX_DELINQUENCY

    def clean(self, record: Dict[str, Any]) -> Dict[str, Any]:
        rec = super().clean(record)
        for f in ("amount_due",):
            if isinstance(rec.get(f), str):
                try:
                    rec[f] = float(rec[f].replace("$", "").replace(",", ""))
                except ValueError:
                    pass
        for f in ("years_delinquent", "tax_year"):
            if isinstance(rec.get(f), str) and rec[f].isdigit():
                rec[f] = int(rec[f])
        return rec


class CodeEnforcementConnector(_Base):
    spec = CODE_ENFORCEMENT


class BusinessEntityConnector(_Base):
    spec = BUSINESS_ENTITY

    def clean(self, record: Dict[str, Any]) -> Dict[str, Any]:
        rec = super().clean(record)
        managers = rec.get("manager_names")
        if isinstance(managers, list):
            rec["manager_names"] = " & ".join(str(x) for x in managers)
        return rec


CONNECTORS = [
    EstateCaseConnector,
    ParcelConnector,
    DeedConnector,
    ForeclosureConnector,
    TaxDelinquencyConnector,
    CodeEnforcementConnector,
    BusinessEntityConnector,
]


def connectors(endpoints: Optional[Dict[str, str]] = None) -> List[Connector]:
    endpoints = endpoints or {}
    return [cls(COUNTY, endpoints.get(cls.spec.name)) for cls in CONNECTORS]


registry.register_county(COUNTY, connectors)
