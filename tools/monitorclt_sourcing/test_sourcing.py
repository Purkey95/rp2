"""Pin the five governance rules against fixtures — the anti-fabrication guarantees.

Run: python3 test_sourcing.py
"""

import json
import os

from registry import load_registry, validate_entry
from adapters import get_adapter
from adapters.base import normalize_rows

HERE = os.path.dirname(os.path.abspath(__file__))


def load_entry(rid):
    for s in load_registry(os.path.join(HERE, "jurisdictions.sample.json")):
        if s["registry_id"] == rid:
            return s
    raise KeyError(rid)


def rows(entry, fixture):
    with open(os.path.join(HERE, "fixtures", fixture), encoding="utf-8") as f:
        data = json.load(f)
    adapter = get_adapter(entry["platform"])
    if entry["platform"] == "arcgis":
        return [adapter._flatten(feat) for feat in data["features"]]
    return list(data)


def check(label, got, want):
    ok = got == want
    print(f"[{'ok ' if ok else 'FAIL'}] {label}: {got!r}")
    return ok


def main():
    ok = True

    # ---- ArcGIS permits: 3 emit, 1 unmapped-status, 1 blank-status ----
    permits = load_entry("meck-building-permits")
    signals, quarantines, report = normalize_rows(permits, rows(permits, "arcgis_permits.json"))

    ok &= check("permits emitted", report.emitted, 3)
    ok &= check("permits data_quality", report.data_quality, "pass")
    ok &= check("permits unmapped surfaced",
                "On Hold Pending Appeal" in report.unmapped_statuses, True)
    ok &= check("permits blank-status excluded", report.excluded_blank_status, 1)

    # Rule 1: every emitted signal carries a source_url.
    ok &= check("all signals have source_url", all(s.source_url for s in signals), True)
    # Rule 1: template fills when the record_url column is empty (row 2).
    dem = next(s for s in signals if s.signal_type == "demolition_permit")
    ok &= check("template-built url", dem.source_url,
                "https://permits.mecknc.gov/permit/DEM-2026-000733")
    # Rule 2: classification only from type_map.
    ok &= check("classified from type_map",
                sorted({s.signal_type for s in signals}),
                ["building_permit", "demolition_permit"])
    # Rule 3: point geometry -> precise; all sample permits carry geometry.
    ok &= check("geo precision point", all(s.geo_precision == "point" for s in signals), True)
    # confidence verified (record precision) for all three.
    ok &= check("confidence verified", all(s.confidence == "verified" for s in signals), True)

    # ---- Socrata code violations: quarantine the row with no resolvable url ----
    cv = load_entry("charlotte-code-violations")
    csig, cq, creport = normalize_rows(cv, rows(cv, "socrata_code_violations.json"))
    ok &= check("code-violations emitted", creport.emitted, 2)
    ok &= check("no_source_url quarantine",
                any(q.reason == "no_source_url" for q in cq), True)
    # Rule 2 default_type applies when type maps but let's confirm mapped type wins.
    ok &= check("vacancy classified",
                any(s.signal_type == "vacancy" for s in csig), True)

    # ---- Idempotency: same input -> same signal_ids ----
    s2, _, _ = normalize_rows(permits, rows(permits, "arcgis_permits.json"))
    ok &= check("stable ids (idempotent)",
                [s.signal_id for s in signals] == [s.signal_id for s in s2], True)

    # ---- Statusless source uses default_bucket (tax-sale/demolition list) ----
    listing_entry = {
        "registry_id": "x", "platform": "arcgis", "source_name": "Tax Sale List",
        "service_url": "https://h/FeatureServer/0", "dataset_url": "https://h/FeatureServer/0",
        "default_bucket": "active", "default_type": "tax_sale",
        "column_map": {"native_id": "id", "apn": "locator", "situs_address": "addr"},
        "status_to_bucket": {},
    }
    lrows = [{"id": "TS-1", "locator": "12-345", "addr": "1 Main St"},
             {"id": "TS-2", "locator": "67-890", "addr": "2 Oak Ave"}]
    lsig, lq, lrep = normalize_rows(listing_entry, lrows)
    ok &= check("statusless default_bucket emits", lrep.emitted, 2)
    ok &= check("statusless bucket=active", all(s.bucket == "active" for s in lsig), True)
    ok &= check("statusless type", all(s.signal_type == "tax_sale" for s in lsig), True)

    # ---- dotted-path field access (Tyler CSS nests Address.FullAddress) ----
    from adapters.base import _get as _bget
    ok &= check("dotted _get nested", _bget({"Address": {"FullAddress": "1 Main St"}}, "Address.FullAddress"), "1 Main St")
    ok &= check("dotted _get missing", _bget({"Address": {}}, "Address.PostalCode"), "")
    ok &= check("flat _get still works", _bget({"flat": "x"}, "flat"), "x")

    # ---- JSON-API portal adapter (Tyler-CSS-shaped), paginated, offline ----
    from adapters.json_api import JsonApiAdapter
    css_entry = {
        "registry_id": "demo-css", "platform": "json_api", "source_name": "Demo County Permits (CSS)",
        "request": {"url": "https://css.example/api/search", "method": "POST", "body": {},
                    "page_param": "PageNumber", "page_in": "body", "page_start": 1,
                    "page_size_param": "PageSize", "page_size": 2},
        "records_path": "Result.EntityResults",
        "record_url_template": "https://css.example/permit/{CaseNumber}",
        "column_map": {"native_id": "CaseNumber", "status": "StatusCode", "type": "CaseType",
                       "apn": "ParcelNumber", "situs_address": "Address"},
        "status_to_bucket": {"Issued": "active", "Finaled": "resolved"},
        "type_map": {"Building": "building_permit", "Demolition": "demolition_permit"},
    }
    pages = {
        1: {"Result": {"EntityResults": [
            {"CaseNumber": "BLDR-26-001", "StatusCode": "Issued", "CaseType": "Building",
             "ParcelNumber": "0710-45", "Address": "5 Main St"},
            {"CaseNumber": "DEM-26-002", "StatusCode": "Finaled", "CaseType": "Demolition",
             "ParcelNumber": "0710-46", "Address": "7 Oak Ave"}]}},
        2: {"Result": {"EntityResults": []}},  # short page -> stop
    }

    def fake_css(spec):
        return pages.get(spec["body"].get("PageNumber"), {"Result": {"EntityResults": []}})

    adapter = JsonApiAdapter(fetch_json=fake_css)
    jsignals, jq, jrep = adapter.run(css_entry)
    ok &= check("json_api emitted (paginated)", jrep.emitted, 2)
    ok &= check("json_api record url from template",
                jsignals[0].source_url, "https://css.example/permit/BLDR-26-001")
    ok &= check("json_api classified", sorted(s.signal_type for s in jsignals),
                ["building_permit", "demolition_permit"])
    ok &= check("json_api buckets", sorted(s.bucket for s in jsignals), ["active", "resolved"])

    # ---- CSV-export adapter (DevNet-Wedge-shaped), enum + dedup, offline ----
    from adapters.csv_export import CsvExportAdapter
    csv_entry = {
        "registry_id": "demo-csv", "platform": "csv_export", "source_name": "Demo Tax CSV",
        "request": {"url": "https://wedge.example/search/csv", "query": {"pageSize": 100},
                    "enum_param": "q", "enum_terms": ["a", "b"], "dedup_field": "Number"},
        "record_url_template": "https://wedge.example/parcel/view/{Number}/{Year}",
        "column_map": {"native_id": "Number", "status": "Payment Status", "type": "Type",
                       "apn": "Number", "situs_address": "Address"},
        "status_to_bucket": {"Unpaid": "active", "Paid": "resolved"},
        "default_type": "tax_delinquency",
    }
    csv_a = ("Year,Type,Number,Name,Address,Payment Status\n"
             "2026,Parcel,01012001A,\"SMITH, N\",0 FISH RD MARSHVILLE NC 28103,Unpaid\n"
             "2025,Parcel,01057018,\"DOE, J\",608 STAFFORD ST MONROE NC 28110,Paid\n")
    csv_b = ("Year,Type,Number,Name,Address,Payment Status\n"
             "2026,Parcel,01012001A,\"SMITH, N\",0 FISH RD MARSHVILLE NC 28103,Unpaid\n"  # dup of a
             "2026,Parcel,09990001,\"ROE, R\",1 OAK LN WAXHAW NC 28173,Unpaid\n")
    csv_pages = {"a": csv_a, "b": csv_b}

    def fake_csv(url):
        # crude: pick the fixture by the q= term present in the URL
        return csv_pages["b"] if "q=b" in url else csv_pages["a"]

    csv_adapter = CsvExportAdapter(fetch_json=fake_csv)
    csig, cq2, crep2 = csv_adapter.run(csv_entry)
    ok &= check("csv emitted (deduped across terms)", crep2.emitted, 3)  # 2 from a + 1 new from b
    ok &= check("csv delinquent bucket", sum(1 for s in csig if s.bucket == "active"), 2)
    ok &= check("csv record url from template",
                any(s.source_url == "https://wedge.example/parcel/view/01012001A/2026" for s in csig), True)

    # ---- PDF-list adapter (delinquent-tax-advertisement-shaped), offline ----
    from adapters.pdf_list import PdfListAdapter
    pdf_entry = {
        "registry_id": "demo-pdf", "platform": "pdf", "source_name": "Demo Delinquent Tax Ad",
        "row_regex": r"^(?P<owner>\S.*?\S)\s{2,}(?P<description>\S.*?\S)\s{2,}(?P<amount>[\d,]+\.\d{2})\s*$",
        "dataset_url": "https://county.example/delinquent-ad",
        "column_map": {"native_id": "_rowline", "situs_address": "description"},
        "default_bucket": "active", "default_type": "tax_delinquency",
    }
    pdf_text = (
        "         NOTICE OF UNPAID 2025 REAL ESTATE TAXES\n"
        "OWNER NAME                          DESCRIPTION            AMOUNT DUE\n"
        "104-A WAXHAW HOLDINGS LLC          104 WAXHAW PARK DR       2248.21\n"
        "ABEE, MOLLY CHRISTINA              2411 POTTER DOWNS DR     1862.64\n"
        "  (page 1 of 42)\n"
        "ZULUAGA, GUILLERMO                 1811 CONFEDERATE ST       505.41\n"
    )
    pdf_adapter = PdfListAdapter(fetch_json=lambda e: pdf_text)
    psig, pq, prep = pdf_adapter.run(pdf_entry)
    ok &= check("pdf rows parsed (headers skipped)", prep.emitted, 3)
    ok &= check("pdf amount captured in raw", psig[0].raw.get("amount"), "2248.21")
    ok &= check("pdf situs mapped", psig[1].situs_address, "2411 POTTER DOWNS DR")
    ok &= check("pdf all delinquent (default_bucket)", all(s.bucket == "active" for s in psig), True)

    # ---- browser_api adapter (mapping layer; rows injected, no real browser) ----
    from adapters.browser_api import BrowserApiAdapter
    br_entry = {
        "registry_id": "demo-browser", "platform": "browser_api", "source_name": "Demo Tax Sale (browser)",
        "browser": {"url": "https://county.example/tax-sale", "mode": "table", "table_selector": "table"},
        "record_url_template": "https://county.example/parcel/{Locator}",
        "column_map": {"native_id": "Locator", "apn": "Locator", "situs_address": "Address"},
        "default_bucket": "active", "default_type": "tax_sale",
    }
    scraped = [
        {"Locator": "12F-230877", "Owner": "DOE J", "Address": "1 MAIN ST", "Amount": "500.00"},
        {"Locator": "13G-110044", "Owner": "ROE R", "Address": "2 OAK AVE", "Amount": "750.00"},
    ]
    br_adapter = BrowserApiAdapter(fetch_json=lambda e: scraped)
    bsig, bq, brep = br_adapter.run(br_entry)
    ok &= check("browser_api rows normalized", brep.emitted, 2)
    ok &= check("browser_api record url", bsig[0].source_url, "https://county.example/parcel/12F-230877")
    ok &= check("browser_api default type", all(s.signal_type == "tax_sale" for s in bsig), True)

    # ---- Registry validation catches an unsourceable entry ----
    bad = {"registry_id": "x", "platform": "socrata", "source_name": "X",
           "domain": "d", "dataset_id": "i",
           "column_map": {"status": "s"}, "status_to_bucket": {"Open": "active"}}
    ok &= check("validation flags no-url entry",
                any("nothing can be sourced" in p for p in validate_entry(bad)), True)

    print("\n" + ("ALL PASSED" if ok else "SOME TESTS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
