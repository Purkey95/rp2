"""Postgres persistence for the pipeline offer sheet.

Upserts full offer-sheet rows (score + confidence + why-now + valuation + offer
band + market stance) into the `leads` table so the CRM, daily digest, and
mail/SMS lanes read one canonical queue (the `offer_queue` view). psycopg is
imported lazily so the pipeline runs from files without a database or the driver.

Apply on the host, in order:
    psql < ../monitorclt_score/schema.sql      # base leads table
    psql < schema.sql                          # + valuation/why-now/market columns
Run:
    python3 pipeline.py ... --dsn "postgresql://user:pass@host/monitorclt"
"""


def _connect(dsn):
    import psycopg  # lazy: pipeline stays runnable from files without the driver
    return psycopg.connect(dsn)


def _offer_fields(row):
    """Flatten one offer-sheet row into the columns the leads table stores."""
    val = row.get("valuation") or {}
    valued = val.get("status") == "valued"
    ob = (val.get("offer_band") or {}) if valued else {}
    return {
        "estimated_value": val.get("estimated_value") if valued else None,
        "offer_low": ob.get("as_is_low"),
        "offer_high": ob.get("as_is_high"),
        "offer_market_high": ob.get("market_adjusted_high"),
        "why_now": row.get("why_now"),
        "market_stance": row.get("market_stance"),
    }


def write_offer_sheet_db(dsn, rows):
    """Idempotent upsert of offer-sheet rows into `leads`. Requires the score
    schema plus this module's schema.sql applied. Returns rows written."""
    from psycopg.types.json import Json
    payload = []
    for r in rows:
        f = _offer_fields(r)
        payload.append((
            r["apn"], r.get("owner", ""), r.get("situs_address", ""),
            r["seller_opportunity_score"], r["confidence"], r["band"],
            r.get("dimensions_firing", 0),
            f["why_now"], f["estimated_value"], f["offer_low"], f["offer_high"],
            f["offer_market_high"], f["market_stance"],
            Json(r.get("valuation") or {}), Json(r.get("top_signals") or []),
        ))
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO leads (apn, owner, situs_address, seller_opportunity_score, confidence, band,
                               dimensions_firing, why_now, estimated_value, offer_low, offer_high,
                               offer_market_high, market_stance, valuation, top_signals,
                               scored_at, offer_ready_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(),
                    CASE WHEN %s IS NOT NULL THEN now() ELSE NULL END)
            ON CONFLICT (apn) DO UPDATE SET
                owner = EXCLUDED.owner,
                situs_address = EXCLUDED.situs_address,
                seller_opportunity_score = EXCLUDED.seller_opportunity_score,
                confidence = EXCLUDED.confidence,
                band = EXCLUDED.band,
                dimensions_firing = EXCLUDED.dimensions_firing,
                why_now = EXCLUDED.why_now,
                estimated_value = EXCLUDED.estimated_value,
                offer_low = EXCLUDED.offer_low,
                offer_high = EXCLUDED.offer_high,
                offer_market_high = EXCLUDED.offer_market_high,
                market_stance = EXCLUDED.market_stance,
                valuation = EXCLUDED.valuation,
                top_signals = EXCLUDED.top_signals,
                scored_at = now(),
                offer_ready_at = COALESCE(EXCLUDED.offer_ready_at, leads.offer_ready_at)
        """, [p + (p[8],) for p in payload])  # trailing arg drives offer_ready_at (estimated_value)
        conn.commit()
        return len(payload)
