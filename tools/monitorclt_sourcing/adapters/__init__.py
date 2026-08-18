"""Source adapters. Register new platforms here; add jurisdictions in the registry."""

from .arcgis import ArcgisAdapter
from .socrata import SocrataAdapter
from .json_api import JsonApiAdapter
from .csv_export import CsvExportAdapter
from .pdf_list import PdfListAdapter

ADAPTERS = {
    "arcgis": ArcgisAdapter,
    "socrata": SocrataAdapter,
    "json_api": JsonApiAdapter,
    "csv_export": CsvExportAdapter,
    "pdf": PdfListAdapter,
}


def get_adapter(platform, fetch_json=None):
    if platform not in ADAPTERS:
        raise ValueError(f"unknown platform '{platform}'; known: {sorted(ADAPTERS)}")
    return ADAPTERS[platform](fetch_json=fetch_json)
