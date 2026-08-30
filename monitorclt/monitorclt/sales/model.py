"""One recorded property transfer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, Optional

from ..normalize.owner import DECEDENT_TYPES, OwnerType, parse_owner
from ..normalize.text import blank_to_none
from ..parcel.model import _epoch_millis_to_date, _to_float

#: ``salesvalidity`` is blank on a qualified arms-length sale; every other code
#: carries a disqualifying reason in ``naldesc`` (price under $3,000, multi-parcel
#: conveyance, transfer between relatives, forced sale, and so on). Roughly 38%
#: of recent sales qualify.
ARMS_LENGTH_VALIDITY_CODES = frozenset({None, "", " "})

#: ``naldesc`` value marking a foreclosure or auction disposition.
FORCED_SALE_DESCRIPTION = "FORCED SALE OR AUCTION"


@dataclass(frozen=True)
class Sale:
    """A transfer, with grantor and grantee classified by the owner parser.

    ``grantor`` is the seller, so a grantor classifying as ESTATE/HEIRS/
    LIFE_ESTATE is a *completed* estate transition -- the outcome label the
    backtest predicts, not a predictor.
    """

    property_id: int
    parcel_id: Optional[str]
    sale_date: date
    sale_price: Optional[float]
    grantor: Optional[str]
    grantee: Optional[str]
    grantor_type: str
    grantee_type: str
    sales_validity: Optional[str]
    nal_description: Optional[str]
    sold_as_vacant: bool
    observed_at: datetime

    @property
    def is_arms_length(self) -> bool:
        return self.sales_validity in ARMS_LENGTH_VALIDITY_CODES

    @property
    def is_estate_sale(self) -> bool:
        """Seller was an estate, heirs, or a life estate."""
        return self.grantor_type in DECEDENT_TYPES

    @property
    def is_forced_sale(self) -> bool:
        return (self.nal_description or "").strip().upper() == FORCED_SALE_DESCRIPTION

    @classmethod
    def from_arcgis(cls, attributes: Dict[str, Any], *, observed_at: datetime) -> Optional["Sale"]:
        """Build a Sale, or None when it lacks a usable key or date."""
        property_id = attributes.get("propertyid")
        sale_date = _epoch_millis_to_date(attributes.get("saledate"))
        if property_id is None or sale_date is None:
            return None
        grantor = blank_to_none(attributes.get("grantor"))
        grantee = blank_to_none(attributes.get("grantee"))
        return cls(
            property_id=int(property_id),
            parcel_id=blank_to_none(attributes.get("parcelid")),
            sale_date=sale_date,
            sale_price=_to_float(attributes.get("saleprice")),
            grantor=grantor,
            grantee=grantee,
            grantor_type=parse_owner(grantor).owner_type if grantor else OwnerType.UNKNOWN,
            grantee_type=parse_owner(grantee).owner_type if grantee else OwnerType.UNKNOWN,
            sales_validity=attributes.get("salesvalidity"),
            nal_description=blank_to_none(attributes.get("naldesc")),
            sold_as_vacant=str(attributes.get("soldasvacantflag") or "").strip().upper() == "YES",
            observed_at=observed_at,
        )
