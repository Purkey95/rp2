"""Sales table, added to the same SQLite database as the parcel index."""

from __future__ import annotations

from typing import Iterable, List

from ..parcel.store import ParcelStore
from .model import Sale

SALES_SCHEMA = """
CREATE TABLE IF NOT EXISTS sales (
    property_id     INTEGER NOT NULL,
    parcel_id       TEXT,
    sale_date       TEXT NOT NULL,
    sale_price      REAL,
    grantor         TEXT,
    grantee         TEXT,
    grantor_type    TEXT,
    grantee_type    TEXT,
    sales_validity  TEXT,
    nal_description TEXT,
    sold_as_vacant  INTEGER NOT NULL DEFAULT 0,
    is_arms_length  INTEGER NOT NULL DEFAULT 0,
    is_estate_sale  INTEGER NOT NULL DEFAULT 0,
    is_forced_sale  INTEGER NOT NULL DEFAULT 0,
    observed_at     TEXT NOT NULL,
    run_id          INTEGER,
    PRIMARY KEY (property_id, sale_date, grantor, grantee)
);

CREATE INDEX IF NOT EXISTS ix_sales_property ON sales (property_id, sale_date);
CREATE INDEX IF NOT EXISTS ix_sales_date     ON sales (sale_date);
CREATE INDEX IF NOT EXISTS ix_sales_estate   ON sales (is_estate_sale);
"""

_COLUMNS = (
    "property_id", "parcel_id", "sale_date", "sale_price", "grantor", "grantee",
    "grantor_type", "grantee_type", "sales_validity", "nal_description",
    "sold_as_vacant", "is_arms_length", "is_estate_sale", "is_forced_sale",
    "observed_at", "run_id",
)


def ensure_sales_schema(store: ParcelStore) -> None:
    store.connection.executescript(SALES_SCHEMA)
    store.connection.commit()


def upsert_sales(store: ParcelStore, sales: Iterable[Sale], run_id: int = None) -> int:
    """Insert or replace sales. Returns rows written."""
    ensure_sales_schema(store)
    statement = (
        "INSERT OR REPLACE INTO sales (" + ", ".join(_COLUMNS) + ") VALUES ("
        + ", ".join("?" for _ in _COLUMNS) + ")"
    )
    written = 0
    for sale in sales:
        values: List[object] = [
            sale.property_id,
            sale.parcel_id,
            sale.sale_date.isoformat(),
            sale.sale_price,
            sale.grantor,
            sale.grantee,
            sale.grantor_type,
            sale.grantee_type,
            sale.sales_validity,
            sale.nal_description,
            1 if sale.sold_as_vacant else 0,
            1 if sale.is_arms_length else 0,
            1 if sale.is_estate_sale else 0,
            1 if sale.is_forced_sale else 0,
            sale.observed_at.isoformat(),
            run_id,
        ]
        store.connection.execute(statement, values)
        written += 1
        if written % 20000 == 0:
            store.connection.commit()
    store.connection.commit()
    return written
