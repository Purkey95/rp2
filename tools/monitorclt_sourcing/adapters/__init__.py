"""Source adapters. Register new platforms here; add jurisdictions in the registry."""

from .arcgis import ArcgisAdapter
from .socrata import SocrataAdapter
from .json_api import JsonApiAdapter

ADAPTERS = {
    "arcgis": ArcgisAdapter,
    "socrata": SocrataAdapter,
    "json_api": JsonApiAdapter,
}


def get_adapter(platform, fetch_json=None):
    if platform not in ADAPTERS:
        raise ValueError(f"unknown platform '{platform}'; known: {sorted(ADAPTERS)}")
    return ADAPTERS[platform](fetch_json=fetch_json)
