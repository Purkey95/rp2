"""Fetch the sales history into the store."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Iterator, Optional

from ..parcel.source import FeatureLayerClient
from ..parcel.store import ParcelStore
from .model import Sale
from .store import upsert_sales

MECKLENBURG_SALES_LAYER = (
    "https://meckgis.mecklenburgcountync.gov/server/rest/services"
    "/TaxParcelSales/MapServer/0"
)

SALES_FIELDS = (
    "propertyid", "parcelid", "saledate", "saleprice", "grantor", "grantee",
    "salesvalidity", "naldesc", "soldasvacantflag",
)


def load_sales(
    store: ParcelStore,
    client: Optional[FeatureLayerClient] = None,
    *,
    where: str = "saledate >= DATE '1980-01-01'",
    limit: Optional[int] = None,
    progress: Optional[Callable[[int], None]] = None,
) -> int:
    """Fetch sales and write them to ``store``. Returns rows written."""
    sales_client = client if client is not None else FeatureLayerClient(MECKLENBURG_SALES_LAYER)
    observed_at = datetime.now(timezone.utc)
    run_id = store.start_run(sales_client.layer_url, where)

    def sales() -> Iterator[Sale]:
        count = 0
        for attributes in sales_client.iter_features(
            where=where, fields=SALES_FIELDS, limit=limit
        ):
            count += 1
            if progress is not None and count % 50000 == 0:
                progress(count)
            sale = Sale.from_arcgis(attributes, observed_at=observed_at)
            if sale is not None:
                yield sale

    written = upsert_sales(store, sales(), run_id=run_id)
    store.finish_run(run_id, written)
    return written
