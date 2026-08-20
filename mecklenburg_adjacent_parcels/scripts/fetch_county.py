"""Fetch all Mecklenburg County (and affiliate) owned parcels with geometry."""
import json, sys
from arc import query

LAYER = "https://meckgis.mecklenburgcountync.gov/server/rest/services/TaxParcel_Camaownershipvalues/FeatureServer/0"

WHERE = (
    "full_owner_name LIKE 'MECKLENBURG COUNTY%' "
    "OR full_owner_name LIKE 'COUNTY OF MECKLENBURG%' "
    "OR full_owner_name LIKE 'MECKLENBURG CO %' "
    "OR full_owner_name LIKE '%MECK CO%' "
    "OR full_owner_name LIKE '%MECKLENBURG BOARD OF EDUCATION%' "
    "OR full_owner_name LIKE '%MECKLENBURG-BOARD OF EDUCATION%' "
    "OR full_owner_name LIKE '%BOARD OF EDUCATION%' "
    "OR full_owner_name LIKE '%MECKLENBURG HOSPITAL AUTHORITY%' "
    "OR full_owner_name LIKE '%LIBRARY OF CHARLOTTE%' "
    "OR full_owner_name LIKE '%MECKLENBURG PUBLIC LIBRARY%' "
    "OR full_owner_name LIKE '%LANDMARK%COMMIS%' "
    "OR full_owner_name LIKE '%MECKLENBURG EMS%' "
    "OR full_owner_name LIKE '%MECKLENBURG UTILIT%'"
)

FIELDS = ("objectid,pid,full_owner_name,txt_mailaddr1,txt_city,txt_state,situsaddress1,"
          "txt_propertyuse_desc,num_totalac,amt_totalvalue,municipality_desc,txt_legaldesc")


def main(out="county_parcels.json"):
    feats, off = [], 0
    while True:
        d = query(LAYER, where=WHERE, outFields=FIELDS, returnGeometry="true", outSR=2264,
                  resultOffset=off, resultRecordCount=500)
        got = d.get("features", [])
        feats.extend(got)
        print(f"  fetched {len(feats)}", file=sys.stderr)
        if len(got) < 500:
            break
        off += 500
    json.dump({"features": feats}, open(out, "w"))
    print(f"total county-side parcels: {len(feats)}")


if __name__ == "__main__":
    main()
