"""Wires the feature-layer client to the parcel store."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Iterator, Optional

from .model import Parcel
from .source import FeatureLayerClient
from .store import ParcelStore


def load_parcels(
    store: ParcelStore,
    client: Optional[FeatureLayerClient] = None,
    *,
    where: str = "1=1",
    limit: Optional[int] = None,
    progress: Optional[Callable[[int], None]] = None,
) -> int:
    """Fetch parcels and write them to ``store``. Returns records written.

    The whole run shares one ``observed_at`` timestamp so a load is a single
    coherent observation rather than a smear across however long the fetch
    took.
    """
    feature_client = client if client is not None else FeatureLayerClient()
    observed_at = datetime.now(timezone.utc)
    run_id = store.start_run(feature_client.layer_url, where)

    def parcels() -> Iterator[Parcel]:
        count = 0
        for attributes in feature_client.iter_features(where=where, limit=limit):
            count += 1
            if progress is not None and count % 5000 == 0:
                progress(count)
            yield Parcel.from_arcgis(
                attributes, observed_at=observed_at, source=feature_client.layer_url
            )

    written = store.upsert(parcels(), run_id=run_id)
    store.finish_run(run_id, written)
    return written
