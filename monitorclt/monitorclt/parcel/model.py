"""The parcel record produced by enrichment."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional, Tuple

from ..normalize.address import NormalizedAddress, normalize_address, same_address
from ..normalize.owner import OwnerType, ParsedOwner, parse_owner

#: Mailing-address cities treated as local for absentee purposes.
LOCAL_MAIL_CITIES = frozenset(
    {"CHARLOTTE", "CORNELIUS", "DAVIDSON", "HUNTERSVILLE", "MATTHEWS", "MINT HILL", "PINEVILLE"}
)


def _epoch_millis_to_date(value: Optional[Any]) -> Optional[date]:
    """ArcGIS returns dates as epoch milliseconds; 0 and None both mean absent."""
    if value is None:
        return None
    try:
        millis = int(value)
    except (TypeError, ValueError):
        return None
    if millis == 0:
        return None
    return datetime.fromtimestamp(millis / 1000.0, tz=timezone.utc).date()


def _to_float(value: Optional[Any]) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class Parcel:
    """One parcel-owner record, normalized and enriched.

    ``parcel_key`` is ``camapid``, not ``pid``. In the Mecklenburg CAMA
    ownership layer ``pid`` is *not* unique — 428,504 records carry only
    396,310 distinct ``pid`` values, because condominium units in a building
    share a parcel id. ``camapid`` and ``propertyid`` are both fully distinct.
    Keying on ``pid`` silently collapses ~32,000 condo units.
    """

    parcel_key: str
    pid: Optional[str]
    property_id: Optional[int]

    owner_raw: str
    owner: ParsedOwner

    situs: NormalizedAddress
    situs_raw: Optional[str]
    mailing: NormalizedAddress

    land_value: Optional[float]
    building_value: Optional[float]
    total_value: Optional[float]

    last_sale_date: Optional[date]
    last_sale_price: Optional[float]
    deed_book: Optional[str]
    deed_page: Optional[str]

    property_use: Optional[str]
    acres: Optional[float]
    municipality: Optional[str]

    observed_at: datetime
    source: str

    # --- Derived enrichment ------------------------------------------------

    @property
    def is_absentee(self) -> bool:
        """Owner's mailing address differs from the property address.

        Both sides are normalized first, which matters: the same parcel can
        carry situs ``6307 CORY-BRET LN`` and mailing ``6307 CORY BRET LN``.
        """
        if self.situs.is_empty or self.mailing.is_empty:
            return False
        return not same_address(self.situs, self.mailing)

    @property
    def is_out_of_state(self) -> bool:
        """Mailing address in a US state other than NC.

        Strictly about US states, so an international address is *not*
        out-of-state -- see :attr:`is_international`.
        """
        state = self.mailing.state
        return state is not None and state != "NC"

    @property
    def is_international(self) -> bool:
        """Mailing address that cannot be placed in a US state.

        174 parcels countywide mail to addresses like ``3280 BLOOR ST W``
        (Toronto) or ``PARC DU CHATEAU`` with the state and city columns empty.
        They are neither in-state nor out-of-state, and treating an absent
        state as local would silently count them as owner-occupied.
        """
        return bool(self.mailing.key) and self.mailing.state is None

    @property
    def is_out_of_area(self) -> bool:
        """Mailing address outside the Mecklenburg municipalities.

        Requires *positive* evidence of being local: an unrecognized or absent
        city counts as out of area rather than defaulting to local.
        """
        if self.is_out_of_state or self.is_international:
            return True
        city = self.mailing.city
        return city is not None and city not in LOCAL_MAIL_CITIES

    @property
    def ownership_years(self) -> Optional[float]:
        if self.last_sale_date is None:
            return None
        delta = self.observed_at.date() - self.last_sale_date
        return round(delta.days / 365.25, 1)

    @property
    def indicates_decedent(self) -> bool:
        """The owner string itself marks the owner as deceased.

        An ESTATE, HEIRS or LIFE ESTATE owner is an estate signal that needs no
        obituary and no name matching. Rare, though: roughly 95 parcels
        countywide, so a backlog sweep rather than a pipeline.
        """
        return self.owner.indicates_decedent

    @classmethod
    def from_arcgis(cls, attributes: Dict[str, Any], *, observed_at: datetime, source: str) -> "Parcel":
        """Build a Parcel from one ArcGIS feature's attribute dict."""
        get = attributes.get
        situs_raw = get("situsaddress1")
        return cls(
            parcel_key=str(get("camapid") or "").strip(),
            pid=(str(get("pid")).strip() or None) if get("pid") is not None else None,
            property_id=get("propertyid"),
            owner_raw=str(get("full_owner_name") or "").strip(),
            owner=parse_owner(
                get("full_owner_name"),
                surname=get("nme_ownerlastname"),
                given=get("nme_ownerfirstname"),
                secondary_surname=get("secownerlastname"),
                secondary_given=get("secownerfirstname"),
            ),
            situs=normalize_address(situs_raw),
            situs_raw=situs_raw,
            mailing=normalize_address(
                get("txt_mailaddr1"), city=get("txt_city"), state=get("txt_state")
            ),
            land_value=_to_float(get("amt_landvalue")),
            building_value=_to_float(get("amt_netbldgvalue")),
            total_value=_to_float(get("amt_totalvalue")),
            last_sale_date=_epoch_millis_to_date(get("dte_dateofsale")),
            last_sale_price=_to_float(get("amt_price")),
            deed_book=get("txt_deedbook") or None,
            deed_page=get("txt_deedpage") or None,
            property_use=get("txt_propertyuse_desc") or None,
            acres=_to_float(get("num_totalac")),
            municipality=get("municipality_desc") or None,
            observed_at=observed_at,
            source=source,
        )

    def evidence_summary(self) -> Tuple[str, ...]:
        """Human-readable facts about this parcel, for the ranked-list output.

        Deliberately a list of facts rather than a score — see
        ``second-brain/wiki/property-signal-scoring-and-calibration.md``.
        """
        facts = []
        if self.indicates_decedent:
            facts.append("owner record marked " + self.owner.owner_type)
        if self.owner.care_of:
            facts.append("care of " + self.owner.care_of)
        if self.is_out_of_state:
            facts.append("owner mails to " + str(self.mailing.state))
        elif self.is_international:
            facts.append("owner mails outside the US")
        elif self.is_out_of_area:
            facts.append("owner mails outside county (" + str(self.mailing.city) + ")")
        elif self.is_absentee:
            facts.append("mailing address differs from property")
        years = self.ownership_years
        if years is not None and years >= 20:
            facts.append("owned " + str(years) + " years")
        if self.owner.owner_type in (OwnerType.COMPANY, OwnerType.TRUST):
            facts.append("owner is a " + self.owner.owner_type.lower())
        if self.total_value:
            facts.append("assessed $" + format(int(self.total_value), ","))
        return tuple(facts)
