"""The entity graph: parcels, persons, and the mentions that connect records to them.

Every name occurrence in every source record becomes a mention (with its role and
the address that accompanied it). Resolution works on mentions, so adding a source
means declaring which fields name people -- not writing a new matcher.

A person row exists only after a link is confirmed. Until then, mentions stand alone.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .normalize.addresses import parse_address
from .normalize.names import Name, block_keys, split_parties
from .normalize.pins import county_norm, parcel_id, pin_norm
from .sources.base import SourceSpec
from .store import Store, dumps, loads

# --------------------------------------------------------------- parcels ----


def upsert_parcel(store: Store, county: str, payload: Dict[str, Any]) -> str:
    """Create or refresh the canonical parcel from an assessor row."""
    pid = parcel_id(county, payload.get("pin"))
    situs = parse_address(payload.get("situs_address"))
    mailing = parse_address(payload.get("owner_mailing_address"))
    now = store.now()
    values = {
        "county": county_norm(county),
        "pin": str(payload.get("pin") or ""),
        "pin_norm": pin_norm(payload.get("pin")),
        "situs_norm": situs.normalized or None,
        "situs_components": dumps(situs.to_dict()),
        "zip": situs.zip or None,
        "lat": _float(payload.get("lat") or payload.get("latitude")),
        "lon": _float(payload.get("lon") or payload.get("longitude")),
        "land_use": payload.get("land_use"),
        "assessed_value": _float(payload.get("assessed_value")),
        "owner_string": payload.get("owner_name"),
        "owner_mailing_norm": mailing.normalized or None,
        "last_sale_date": payload.get("last_sale_date"),
        "last_seen": now,
        "retired_at": None,
    }
    existing = store.one("SELECT id FROM parcel WHERE id = ?", (pid,))
    if existing:
        store.update("parcel", values, "id = ?", (pid,))
    else:
        values.update({"id": pid, "first_seen": now})
        store.insert("parcel", values)
    return pid


def retire_parcel(store: Store, county: str, pin: str) -> Optional[str]:
    pid = parcel_id(county, pin)
    if store.update("parcel", {"retired_at": store.now()}, "id = ? AND retired_at IS NULL", (pid,)):
        return pid
    return None


def get_parcel(store: Store, pid: str) -> Optional[Dict[str, Any]]:
    row = store.one("SELECT * FROM parcel WHERE id = ?", (pid,))
    if row:
        row["situs_components"] = loads(row["situs_components"], {})
    return row


def _float(v: Any) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", "").replace("$", ""))
    except ValueError:
        return None


# -------------------------------------------------------------- mentions ----


def index_mentions(store: Store, spec: SourceSpec, county: str, natural_key: str, version_id: int, payload: Dict[str, Any]) -> List[int]:
    """Replace the current mentions for a record with those parsed from its newest version."""
    store.execute(
        "UPDATE mention SET current = 0 WHERE source = ? AND county = ? AND natural_key = ? AND current = 1",
        (spec.name, county, natural_key),
    )
    # The address that travels with a mention is the one the role implies; fall back to any address field.
    address_by_kind: Dict[str, str] = {}
    for field_name, kind in spec.address_fields.items():
        value = payload.get(field_name)
        if value:
            address_by_kind[kind] = parse_address(value).normalized
    pid = None
    for field_name in spec.parcel_fields:
        if payload.get(field_name):
            pid = parcel_id(payload.get("county") or county, payload[field_name])
            break
    ids: List[int] = []
    for field_name, role in spec.mention_roles.items():
        raw = payload.get(field_name)
        if not raw:
            continue
        for position, party in enumerate(split_parties(str(raw), spec.name_format)):
            if not party.key_fl.strip("|") and not party.is_organization:
                continue
            mention_id = store.insert(
                "mention",
                {
                    "source": spec.name,
                    "county": county_norm(county),
                    "natural_key": natural_key,
                    "record_version_id": version_id,
                    "role": role,
                    "position": position,
                    "raw_name": str(raw),
                    "parsed": dumps(party.to_dict()),
                    "key_fl": party.key_fl,
                    "is_organization": 1 if party.is_organization else 0,
                    "address_norm": _address_for_role(role, address_by_kind),
                    "parcel_id": pid,
                    "person_id": None,
                    "current": 1,
                },
            )
            write_blocks(store, mention_id, party)
            ids.append(mention_id)
    return ids


def _address_for_role(role: str, by_kind: Dict[str, str]) -> Optional[str]:
    preferred = {
        "owner": ("tax_mailing", "situs"),
        "decedent": ("court_filing",),
        "personal_rep": ("court_filing",),
        "grantor": ("situs",),
        "grantee": ("situs",),
        "debtor": ("situs",),
    }.get(role, ())
    for kind in preferred:
        if kind in by_kind:
            return by_kind[kind]
    return next(iter(by_kind.values()), None)


def mention_name(row: Dict[str, Any]) -> Name:
    p = loads(row["parsed"], {})
    return Name(
        raw=p.get("raw", ""),
        clean=p.get("clean", ""),
        first=p.get("first", ""),
        middle=p.get("middle", ""),
        last=p.get("last", ""),
        suffix=p.get("suffix", ""),
        is_organization=bool(p.get("is_organization")),
        markers=list(p.get("markers", [])),
        trust=bool(p.get("trust")),
        order_uncertain=bool(p.get("order_uncertain")),
    )


def write_blocks(store: Store, mention_id: int, name: Name) -> List[str]:
    keys = block_keys(name)
    if name.is_organization and name.clean:
        keys = ["ORG|" + name.clean]
    store.executemany("INSERT OR IGNORE INTO mention_block (mention_id, key) VALUES (?, ?)", [(mention_id, k) for k in keys])
    return keys


def rebuild_blocks(store: Store) -> int:
    """Recompute block keys for every current mention (after a rules or nickname-table change)."""
    store.execute("DELETE FROM mention_block")
    n = 0
    for row in store.query("SELECT id, parsed FROM mention WHERE current = 1"):
        write_blocks(store, int(row["id"]), mention_name(row))
        n += 1
    store.commit()
    return n


def candidates_by_blocks(store: Store, keys: List[str], source: str, role: str) -> List[Dict[str, Any]]:
    """Current mentions of source/role sharing any block key; each row carries the keys it was found by."""
    if not keys:
        return []
    rows = store.query(
        "SELECT m.*, GROUP_CONCAT(b.key, ' ') AS found_by FROM mention m JOIN mention_block b ON b.mention_id = m.id "
        "WHERE b.key IN ({0}) AND m.source = ? AND m.role = ? AND m.current = 1 GROUP BY m.id ORDER BY m.id".format(", ".join("?" for _ in keys)),
        list(keys) + [source, role],
    )
    for r in rows:
        r["found_by"] = sorted((r["found_by"] or "").split())
    return rows


def mentions_by_key(store: Store, key_fl: str, source: Optional[str] = None, role: Optional[str] = None) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM mention WHERE key_fl = ? AND current = 1"
    params: List[Any] = [key_fl]
    if source:
        sql += " AND source = ?"
        params.append(source)
    if role:
        sql += " AND role = ?"
        params.append(role)
    return store.query(sql + " ORDER BY id", params)


def mentions_for_record(store: Store, source: str, county: str, natural_key: str, role: Optional[str] = None) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM mention WHERE source = ? AND county = ? AND natural_key = ? AND current = 1"
    params: List[Any] = [source, county_norm(county), natural_key]
    if role:
        sql += " AND role = ?"
        params.append(role)
    return store.query(sql + " ORDER BY position", params)


def name_frequency(store: Store, source: str, role: str) -> Dict[str, int]:
    """How many distinct records each FIRST|LAST appears on -- the 'common name' signal."""
    rows = store.query(
        "SELECT key_fl, COUNT(DISTINCT natural_key) AS n FROM mention WHERE source = ? AND role = ? AND current = 1 AND is_organization = 0 GROUP BY key_fl",
        (source, role),
    )
    return {r["key_fl"]: int(r["n"]) for r in rows}


# --------------------------------------------------------------- persons ----


def ensure_person(store: Store, name: Name, source: str, address_norm: Optional[str] = None, address_kind: Optional[str] = None) -> int:
    row = store.one("SELECT id FROM person WHERE normalized_name = ? AND is_organization = ?", (name.normalized, 1 if name.is_organization else 0))
    if row:
        pid = int(row["id"])
    else:
        pid = store.insert(
            "person",
            {
                "normalized_name": name.normalized,
                "display_name": name.raw or name.normalized,
                "is_organization": 1 if name.is_organization else 0,
                "created_at": store.now(),
            },
        )
    store.execute("INSERT OR IGNORE INTO person_alias (person_id, alias_normalized, source) VALUES (?, ?, ?)", (pid, name.normalized, source))
    if address_norm:
        store.execute(
            "INSERT OR IGNORE INTO person_address (person_id, address_norm, address_kind, observed_at) VALUES (?, ?, ?, ?)",
            (pid, address_norm, address_kind or "unknown", store.now()),
        )
    return pid


def attach_mention(store: Store, mention_id: int, person_id: int) -> None:
    store.update("mention", {"person_id": person_id}, "id = ?", (mention_id,))
