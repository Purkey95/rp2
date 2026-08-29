"""Feature-layer client tests using an injected opener -- no network access.

Paging is the part worth pinning: ArcGIS does not guarantee a stable order
across pages without an explicit ``orderByFields``, and omitting it silently
duplicates and drops records on a 428k-record layer.
"""

import json
import sys
import unittest
import urllib.error
import urllib.parse
from os.path import dirname, join
from typing import Dict, List

sys.path.insert(0, join(dirname(dirname(__file__))))

from monitorclt.parcel.source import ArcGisError, FeatureLayerClient  # noqa: E402


class FakeService:
    """Serves a fixed record list with ArcGIS paging semantics."""

    def __init__(self, record_count: int, page_size: int = 3) -> None:
        self.records = [{"camapid": "P" + str(i)} for i in range(record_count)]
        self.page_size = page_size
        self.requests: List[Dict[str, List[str]]] = []

    def __call__(self, url: str, timeout: int) -> bytes:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.requests.append(query)
        if query.get("returnCountOnly") == ["true"]:
            return json.dumps({"count": len(self.records)}).encode()
        offset = int(query.get("resultOffset", ["0"])[0])
        wanted = int(query.get("resultRecordCount", [str(self.page_size)])[0])
        page = self.records[offset : offset + min(wanted, self.page_size)]
        return json.dumps(
            {
                "features": [{"attributes": record} for record in page],
                "exceededTransferLimit": offset + len(page) < len(self.records),
            }
        ).encode()


class TestFeatureLayerClient(unittest.TestCase):
    def test_count(self) -> None:
        service = FakeService(17)
        self.assertEqual(FeatureLayerClient(opener=service).count(), 17)

    def test_pages_through_every_record_exactly_once(self) -> None:
        service = FakeService(17, page_size=3)
        client = FeatureLayerClient(page_size=3, opener=service)
        keys = [attributes["camapid"] for attributes in client.iter_features()]
        self.assertEqual(len(keys), 17)
        self.assertEqual(len(set(keys)), 17)
        self.assertEqual(keys, ["P" + str(i) for i in range(17)])

    def test_paging_always_sends_an_order_by(self) -> None:
        service = FakeService(7, page_size=3)
        list(FeatureLayerClient(page_size=3, opener=service).iter_features())
        page_requests = [r for r in service.requests if "resultOffset" in r]
        self.assertTrue(page_requests)
        for request in page_requests:
            self.assertEqual(request["orderByFields"], ["objectid"])

    def test_limit_stops_early_without_extra_requests(self) -> None:
        service = FakeService(100, page_size=10)
        client = FeatureLayerClient(page_size=10, opener=service)
        self.assertEqual(len(list(client.iter_features(limit=4))), 4)
        self.assertEqual(len(service.requests), 1)

    def test_empty_result_terminates(self) -> None:
        client = FeatureLayerClient(opener=FakeService(0))
        self.assertEqual(list(client.iter_features()), [])

    def test_service_error_is_raised_not_retried(self) -> None:
        calls = []

        def failing(url: str, timeout: int) -> bytes:
            calls.append(url)
            return json.dumps({"error": {"code": 400, "message": "Invalid where clause"}}).encode()

        with self.assertRaises(ArcGisError):
            FeatureLayerClient(opener=failing).count()
        self.assertEqual(len(calls), 1)

    def test_transient_failure_is_retried_then_raises(self) -> None:
        calls = []

        def flaky(url: str, timeout: int) -> bytes:
            calls.append(url)
            raise urllib.error.URLError("connection reset")

        slept = []
        client = FeatureLayerClient(opener=flaky, max_retries=2, sleep=slept.append)
        with self.assertRaises(ArcGisError):
            client.count()
        self.assertEqual(len(calls), 2)
        self.assertEqual(slept, [1, 2])  # exponential backoff between attempts

    def test_requested_fields_are_explicit_not_wildcard(self) -> None:
        service = FakeService(1, page_size=1)
        list(FeatureLayerClient(page_size=1, opener=service).iter_features())
        out_fields = service.requests[0]["outFields"][0]
        self.assertIn("camapid", out_fields)
        self.assertNotIn("*", out_fields)


if __name__ == "__main__":
    unittest.main()
