"""Release the confirmed lead list. Step 4, and only after approval.

Reads probate.v_estate_property and nothing else. The view is confirmed-only, and
0003_rls.sql gives the outreach role no way around it, so "a pending candidate is
a question, not a lead" holds even if this script is wrong.

Connect as a role holding probate_outreach, not probate_loader. There is no
reason for the step that hands work to people to be able to write to a system of
record, and using the narrower role means a mistake here cannot become a mistake
in the data.
"""

from typing import TypedDict

import psycopg


class postgresql(TypedDict):
    host: str
    port: int
    user: str
    dbname: str
    sslmode: str
    password: str


COLUMNS = [
    "county",
    "file_number",
    "decedent_name",
    "date_of_death",
    "filing_date",
    "case_status",
    "personal_rep_name",
    "pr_mailing_address",
    "pin",
    "situs_address",
    "owner_name",
    "land_use",
    "assessed_value",
    "match_tier",
    "score",
    "evidence",
    "flags",
]


def main(database: postgresql, filed_since: str = None, county: str = None):
    """The approved lead list, optionally narrowed to a county or a filing window.

    Each row is an estate that appears to hold real property, plus the evidence
    that says so -- carried along on purpose, so that whoever picks the lead up
    can see what it rests on rather than inheriting a bare assertion.
    """
    where, params = [], []
    if filed_since:
        where.append("filing_date >= %s")
        params.append(filed_since)
    if county:
        where.append("county = %s")
        params.append(county)

    sql = "SELECT {0} FROM probate.v_estate_property".format(", ".join(COLUMNS))
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY filing_date DESC NULLS LAST, county, file_number"

    connection = psycopg.connect(
        host=database["host"],
        port=database["port"],
        user=database["user"],
        password=database["password"],
        dbname=database["dbname"],
        sslmode=database.get("sslmode", "prefer"),
    )
    try:
        cursor = connection.cursor()
        cursor.execute(sql, params)
        leads = [dict(zip(COLUMNS, row)) for row in cursor.fetchall()]
        cursor.close()
    finally:
        connection.close()

    for lead in leads:
        lead["assessed_value"] = float(lead["assessed_value"] or 0)
        lead["score"] = float(lead["score"])
        for key in ("date_of_death", "filing_date"):
            lead[key] = lead[key].isoformat() if lead[key] else None

    return {
        "leads": leads,
        "count": len(leads),
        "contact": "personal representative or estate attorney only",
    }
