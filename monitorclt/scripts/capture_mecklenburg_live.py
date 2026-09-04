#!/usr/bin/env python3
"""Capture a small, real fixture set for the Mecklenburg live sources into
fixtures-private/ (git-ignored). Run scripts/pseudonymize_fixtures.py afterwards to
refresh the committed fixtures.

    python3 scripts/capture_mecklenburg_live.py fixtures-private/mecklenburg/live

ArcGIS layers are queried by POST (long IN lists); the Register of Deeds is driven
through the same connector the daily run uses, paced at one request per second.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from monitorclt.counties import mecklenburg_live as L  # noqa: E402
from monitorclt.sources.transport import HttpTransport  # noqa: E402

BASE = L.GIS
UA = "MonitorCLT/2.1 (+public records monitor; fixture capture)"


def post(url: str, params: dict) -> dict:
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=120) as r:  # nosec B310
        return json.loads(r.read().decode("utf-8"))


def save(out: str, name: str, obj: dict) -> None:
    path = os.path.join(out, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
    print(name, len(obj.get("features", [])))


def main(out: str) -> None:
    common = {"f": "json", "returnGeometry": "false", "outFields": "*"}
    xapo = BASE + "/Accela/Accela/MapServer/16/query"
    pages = [
        post(xapo, dict(common, where="1=1", orderByFields="OBJECTID", resultRecordCount=120)),
        post(
            xapo,
            dict(
                common,
                where="ownerlastname LIKE 'ESTATE OF%' OR ownerlastname LIKE '%HEIRS%' OR ownerfirstname LIKE '%HEIRS%' OR ownerfirstname LIKE '%ESTATE%'",
                orderByFields="OBJECTID",
                resultRecordCount=60,
            ),
        ),
        post(
            xapo,
            dict(
                common,
                where="ownerfirstname LIKE '%TRUST%' OR ownerfirstname LIKE '%TTEE%' OR ownerlastname LIKE '%TRUSTEE%'",
                orderByFields="OBJECTID",
                resultRecordCount=40,
            ),
        ),
        post(
            xapo,
            dict(
                common,
                where="dateofsale >= TIMESTAMP '{0} 00:00:00'".format((dt.date.today() - dt.timedelta(days=35)).isoformat()),
                orderByFields="OBJECTID",
                resultRecordCount=40,
            ),
        ),
    ]
    seen: set = set()
    for i, p in enumerate(pages):
        p["features"] = [f for f in p["features"] if f["attributes"].get("pid") not in seen or i == 0]
        seen |= {f["attributes"].get("pid") for f in p["features"]}
        p["exceededTransferLimit"] = i < len(pages) - 1
        save(out, "parcel/%d.json" % i, p)
        time.sleep(0.5)
    pids = sorted(x for x in seen if x)
    in_list = ",".join("'%s'" % p for p in pids)
    ce = BASE + "/HNS/CodeEnforcementCasesAll/MapServer/0/query"
    c0 = post(
        ce,
        dict(
            common,
            where="DateCreated >= TIMESTAMP '{0} 00:00:00'".format((dt.date.today() - dt.timedelta(days=20)).isoformat()),
            orderByFields="OBJECTID",
            resultRecordCount=80,
        ),
    )
    c1 = post(ce, dict(common, where="ParcelId IN (%s)" % in_list, orderByFields="OBJECTID", resultRecordCount=300))
    c0["exceededTransferLimit"], c1["exceededTransferLimit"] = True, False
    save(out, "code_enforcement/0.json", c0)
    save(out, "code_enforcement/1.json", c1)
    lien = BASE + "/ODP/FMSLienData/MapServer/0/query"
    l0 = post(lien, dict(common, where="1=1", orderByFields="ObjectID", resultRecordCount=60))
    l1 = post(lien, dict(common, where="ParcelID IN (%s)" % ",".join("'%-15s'" % p for p in pids), orderByFields="ObjectID", resultRecordCount=200))
    l0["exceededTransferLimit"], l1["exceededTransferLimit"] = True, False
    save(out, "lien/0.json", l0)
    save(out, "lien/1.json", l1)
    v0 = post(BASE + "/PLN/VacantLand/MapServer/0/query", dict(common, where="1=1", orderByFields="OBJECTID", resultRecordCount=40))
    v0["exceededTransferLimit"] = False
    save(out, "vacant_land/0.json", v0)
    a0 = post(
        BASE + "/CountyData/MasterAddress/MapServer/0/query",
        dict(common, where="TaxParcelID IN (%s)" % in_list, orderByFields="OBJECTID", resultRecordCount=600, returnGeometry="true", outSR="4326"),
    )
    a0["exceededTransferLimit"] = False
    save(out, "address_point/0.json", a0)

    deeds = L.MecklenburgDeedConnector("MECKLENBURG")
    deeds.window_days = 2
    deeds.max_pages = 3
    transport = HttpTransport(user_agent=UA, cookies=True, min_interval_s=1.0)
    for i, fetched in enumerate(deeds.fetch(transport, None)):
        path = os.path.join(out, "deed", "%d.html" % i)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(fetched.body)
        print("deed/%d.html" % i, len(fetched.body))
    print("captured", len(pids), "parcels into", out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "fixtures-private/mecklenburg/live")
