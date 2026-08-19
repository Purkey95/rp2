"""Postgres wiring for the Owner Distress Score.

Lets the score run against the LIVE tables on the MonitorCLT host instead of CSV
fixtures: read `signals` (written by the sourcing adapters) + `parcels` (written
by the enrichment join), score, and upsert into a `leads` table the CRM/digest
reads. psycopg is imported lazily so the module (and the CSV path) work without a
database or the driver installed.

Install on the host:  pip install "psycopg[binary]"
Run:  python3 score.py --dsn "postgresql://user:pass@host/monitorclt" --year 2026
"""


def _connect(dsn):
    import psycopg  # lazy: keep score.py runnable from CSV without the driver
    return psycopg.connect(dsn)


def load_signals_db(dsn):
    """Active signals only (resolved = problem cleared). Mirrors load_signals()."""
    from psycopg.rows import dict_row
    with _connect(dsn) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            SELECT signal_type, bucket, apn, source_url
            FROM signals
            WHERE bucket = 'active' AND apn IS NOT NULL AND apn <> ''
        """)
        return cur.fetchall()


def load_parcels_db(dsn):
    """Parcel/owner rows for the join. Adjust column names to the live schema."""
    from psycopg.rows import dict_row
    with _connect(dsn) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            SELECT apn, owner_name AS owner,
                   situs_address AS situs_street, mail_street, mail_state,
                   situs_state AS property_state, sale_year,
                   assessed_value, mortgage_balance, vacant
            FROM parcels
        """)
        return cur.fetchall()


def write_leads_db(dsn, results):
    """Idempotent upsert of scored leads. Recompute-and-upsert on each run so a
    lead's score rises as its problems stack. Requires schema.sql applied."""
    from psycopg.types.json import Json
    rows = [(
        r["apn"], r["owner"], r["situs_address"], r["seller_opportunity_score"], r["confidence"],
        r["band"], r["dimensions_firing"], r["portfolio_size"], Json(r["components"]), Json(r["signals"]),
    ) for r in results]
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO leads (apn, owner, situs_address, seller_opportunity_score, confidence, band,
                               dimensions_firing, portfolio_size, components, signals, scored_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (apn) DO UPDATE SET
                owner = EXCLUDED.owner,
                situs_address = EXCLUDED.situs_address,
                seller_opportunity_score = EXCLUDED.seller_opportunity_score,
                confidence = EXCLUDED.confidence,
                band = EXCLUDED.band,
                dimensions_firing = EXCLUDED.dimensions_firing,
                portfolio_size = EXCLUDED.portfolio_size,
                components = EXCLUDED.components,
                signals = EXCLUDED.signals,
                scored_at = now()
        """, rows)
        conn.commit()
        return len(rows)
