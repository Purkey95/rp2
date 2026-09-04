"""The resolver: one loop for every source pair.

A LinkKind names the subject mentions (e.g. estate_case/decedent) and the candidate
mentions (parcel/owner). Blocking is on FIRST|LAST -- nothing weaker enters, which
is the v1 rule kept on purpose. Each candidate gets features, a calibrated
probability, gates, and a disposition, and is written to entity_match. A row a
reviewer has already decided keeps its status; only the rationale is refreshed.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .. import entities, geocode, history
from ..events import MATCH_CONFIRMED
from ..normalize.geo import as_point
from ..normalize.names import Name, block_keys
from ..normalize.pins import county_norm, parcel_id
from ..store import Store, dumps, loads
from . import features as F
from . import gates as G
from .model import LinkModel, load_rules

TOOL_VERSION = "2.0"


@dataclass(frozen=True)
class LinkKind:
    name: str
    subject_source: str
    subject_role: str
    candidate_source: str
    candidate_role: str
    related_role: Optional[str] = None  # a role on the subject record whose parties corroborate (personal rep)
    subject_date_field: Optional[str] = "date_of_death"


ESTATE_TO_PARCEL = LinkKind("estate_case->parcel", "estate_case", "decedent", "parcel", "owner", related_role="personal_rep")


class _Preload:
    """Everything the loop needs, read once. Per-candidate SQL is what made v1-style loops slow on a full roll."""

    def __init__(self, store: Store, kind: LinkKind) -> None:
        self.records: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        for src in (kind.subject_source, kind.candidate_source):
            for rec in history.current_records(store, src):
                self.records[(src, rec["county"], rec["natural_key"])] = rec["payload"]
        self.parcels: Dict[str, Dict[str, Any]] = {}
        if kind.candidate_source == "parcel":
            for row in store.query("SELECT * FROM parcel"):
                self.parcels[row["id"]] = row
        self.party_keys: Dict[Tuple[str, str, str], List[str]] = defaultdict(list)
        self.related: Dict[Tuple[str, str], List[str]] = defaultdict(list)
        rows = store.query(
            "SELECT source, county, natural_key, role, key_fl FROM mention WHERE current = 1 AND ((source = ? AND role IN (?, 'care_of')) OR (source = ? AND role = ?)) ORDER BY position",
            (kind.candidate_source, kind.candidate_role, kind.subject_source, kind.related_role or ""),
        )
        for r in rows:
            if r["source"] == kind.candidate_source:
                self.party_keys[(r["source"], r["county"], r["natural_key"])].append(r["key_fl"])
            elif r["key_fl"].strip("|"):
                self.related[(r["county"], r["natural_key"])].append(r["key_fl"])

    def record(self, source: str, county: str, key: str) -> Dict[str, Any]:
        return self.records.get((source, county, key), {})


def _deed_index(store: Store) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, List[Dict[str, Any]]]]:
    """key_fl -> deed payloads where that name is grantor / grantee."""
    grantor: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    grantee: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    rows = store.query(
        "SELECT m.key_fl, m.role, r.payload FROM mention m JOIN record_version r ON r.id = m.record_version_id "
        "WHERE m.source = 'deed' AND m.current = 1 AND m.is_organization = 0 AND m.role IN ('grantor', 'grantee')"
    )
    for r in rows:
        (grantor if r["role"] == "grantor" else grantee)[r["key_fl"]].append(loads(r["payload"], {}))
    return grantor, grantee


def _deeds_on_parcel(deeds: List[Dict[str, Any]], pid: str) -> List[Dict[str, Any]]:
    out = []
    for d in deeds:
        if d.get("parcel_pin") and parcel_id(d.get("county"), d["parcel_pin"]) == pid:
            out.append(d)
    return out


def resolve(
    store: Store,
    kind: LinkKind = ESTATE_TO_PARCEL,
    model: Optional[LinkModel] = None,
    rules: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
    geocoder: Optional[geocode.Geocoder] = None,
) -> Dict[str, Any]:
    rules = rules or load_rules()
    model = model or LinkModel.active(store, rules, kind.name)
    run_id = store.insert(
        "match_run",
        {
            "started_at": store.now(),
            "tool_version": TOOL_VERSION,
            "rules_version": rules.get("version", "?"),
            "model_version_id": model.version_id,
            "params": dumps(params or {}),
        },
    )
    subjects = store.query("SELECT * FROM mention WHERE source = ? AND role = ? AND current = 1 ORDER BY id", (kind.subject_source, kind.subject_role))
    frequency = entities.name_frequency(store, kind.candidate_source, kind.candidate_role)
    grantor_idx, grantee_idx = _deed_index(store)
    pre = _Preload(store, kind)
    threshold_common = int(rules["thresholds"].get("common_name_records", 8))
    proximity = float(rules["thresholds"].get("proximity_m", 75))
    audit_rate = float(rules.get("review", {}).get("audit_sample_rate", 0.0))
    double_rate = float(rules.get("review", {}).get("double_review_rate", 0.0))

    counts = {"subjects": 0, "skipped": 0, "blocked_out": 0, "candidates": 0, "confirmed": 0, "pending": 0, "rejected": 0, "kept_reviewed": 0, "widened": 0}
    skipped: List[Dict[str, Any]] = []
    blocked: List[str] = []
    match_ids: List[int] = []

    for subj in subjects:
        counts["subjects"] += 1
        s_name = entities.mention_name(subj)
        left_id = "{0}/{1}".format(subj["county"], subj["natural_key"])
        if not s_name.is_person:
            counts["skipped"] += 1
            skipped.append({"left_id": left_id, "name": subj["raw_name"], "reason": "not parseable into first + last (or an organization)"})
            continue
        s_payload = pre.record(kind.subject_source, subj["county"], subj["natural_key"])
        related_keys = pre.related.get((subj["county"], subj["natural_key"]), []) if kind.related_role else []
        subject_point = geocode.lookup(store, subj["address_norm"], geocoder)

        candidates = entities.candidates_by_blocks(store, block_keys(s_name), kind.candidate_source, kind.candidate_role)
        candidates = [c for c in candidates if F.names_related(s_name, entities.mention_name(c))]
        if not candidates:
            counts["blocked_out"] += 1
            blocked.append(left_id)
            continue

        # Several parties on one record can be candidates (co-owners with the same surname);
        # score each and keep the best per (subject, record) so a weaker party never
        # overwrites a stronger one.
        best: Dict[str, Tuple[float, Dict[str, Any], Dict[str, float], Dict[str, bool], str, List[str]]] = {}
        for cand in candidates:
            c_name = entities.mention_name(cand)
            if not F.names_related(s_name, c_name):
                continue  # a shared block key is an invitation, not a relation
            counts["candidates"] += 1
            if cand["key_fl"] != s_name.key_fl:
                counts["widened"] += 1  # reached through a nickname, initial or phonetic key
            c_payload = pre.record(kind.candidate_source, cand["county"], cand["natural_key"])
            right_id = cand["parcel_id"] or "{0}/{1}".format(cand["county"], cand["natural_key"])
            party_keys = pre.party_keys.get((kind.candidate_source, cand["county"], cand["natural_key"]), [])
            parcel = pre.parcels.get(right_id) if cand["parcel_id"] else None
            ctx = F.PairContext(
                subject=s_name,
                candidate=c_name,
                subject_record=s_payload,
                candidate_record=c_payload,
                subject_county=county_norm(subj["county"]),
                candidate_county=county_norm(cand["county"]),
                subject_address=subj["address_norm"],
                candidate_mailing=(parcel or {}).get("owner_mailing_norm") or cand["address_norm"],
                candidate_situs=(parcel or {}).get("situs_norm"),
                related_keys=related_keys,
                candidate_party_keys=party_keys,
                deeds_as_grantor=_deeds_on_parcel(grantor_idx.get(s_name.key_fl, []), right_id),
                deeds_as_grantee=_deeds_on_parcel(grantee_idx.get(s_name.key_fl, []), right_id),
                name_frequency=frequency.get(s_name.key_fl, 0),
                common_name_threshold=threshold_common,
                found_by=cand["found_by"],
                subject_point=subject_point,
                candidate_point=as_point((parcel or {}).get("lat"), (parcel or {}).get("lon")),
                proximity_m=proximity,
            )
            feats = F.compute(ctx)
            prob = model.predict(feats)
            gates = G.evaluate(feats, rules)
            status, flags = G.disposition(prob, gates, feats, rules)
            flags = sorted(set(flags + F.flags_for(ctx, feats) + _sampling_flags(left_id, right_id, status, audit_rate, double_rate)))
            if right_id not in best or prob > best[right_id][0]:
                best[right_id] = (prob, cand, feats, gates, status, flags)

        for right_id, (prob, cand, feats, gates, status, flags) in best.items():
            match_id, final_status, kept = _upsert_match(store, run_id, kind, subj, cand, left_id, right_id, feats, prob, gates, status, flags, rules)
            match_ids.append(match_id)
            counts[final_status] += 1
            if kept:
                counts["kept_reviewed"] += 1
            if final_status == "confirmed" and not kept:
                _emit_confirmed(store, match_id, left_id, right_id, cand["parcel_id"], prob, feats)

    store.update(
        "match_run",
        {"finished_at": store.now(), "subjects": counts["subjects"], "blocked_out": counts["blocked_out"], "candidates": counts["candidates"]},
        "id = ?",
        (run_id,),
    )
    store.commit()
    return {
        "run_id": run_id,
        "kind": kind.name,
        "model_version_id": model.version_id,
        "counts": counts,
        "skipped": skipped,
        "blocked_out": blocked,
        "match_ids": match_ids,
    }


def _sampling_flags(left_id: str, right_id: str, status: str, audit_rate: float, double_rate: float) -> List[str]:
    """Deterministic sampling by pair hash, so the same pair is always in or out of a sample.

    audit_sample: a rules-confirmed match a reviewer should look at anyway, to measure
    auto-confirm precision without selection bias. double_review: a pending match that
    two reviewers should decide independently, to measure label agreement.
    """
    import hashlib

    h = int(hashlib.sha1((left_id + "|" + right_id).encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF  # nosec B324 - sampling, not security
    out = []
    if status == "confirmed" and h < audit_rate:
        out.append("audit_sample")
    if status == "pending" and (1 - h) < double_rate:
        out.append("double_review")
    return out


def _upsert_match(
    store: Store,
    run_id: int,
    kind: LinkKind,
    subj: Dict[str, Any],
    cand: Dict[str, Any],
    left_id: str,
    right_id: str,
    feats: Dict[str, float],
    prob: float,
    gates: Dict[str, bool],
    status: str,
    flags: List[str],
    rules: Dict[str, Any],
) -> Tuple[int, str, bool]:
    now = store.now()
    values = {
        "run_id": run_id,
        "kind": kind.name,
        "left_mention_id": subj["id"],
        "right_mention_id": cand["id"],
        "match_tier": G.tier(feats, rules),
        "probability": prob,
        "features": dumps(feats),
        "evidence": dumps(F.evidence_list(feats)),
        "flags": dumps(flags),
        "gates": dumps(gates),
        "updated_at": now,
    }
    existing = store.one(
        "SELECT id, status, decided_by FROM entity_match WHERE left_source = ? AND left_id = ? AND right_source = ? AND right_id = ?",
        (kind.subject_source, left_id, kind.candidate_source, right_id),
    )
    if existing and existing["decided_by"] == "reviewer":
        store.update("entity_match", values, "id = ?", (existing["id"],))
        return int(existing["id"]), existing["status"], True
    values.update({"status": status, "decided_by": "rules"})
    if existing:
        store.update("entity_match", values, "id = ?", (existing["id"],))
        match_id = int(existing["id"])
        if status == "confirmed" and existing["status"] == "confirmed":
            return match_id, status, True  # already confirmed earlier; no new event
    else:
        values.update({"left_source": kind.subject_source, "left_id": left_id, "right_source": kind.candidate_source, "right_id": right_id, "created_at": now})
        match_id = store.insert("entity_match", values)
    if status == "confirmed":
        materialize_person(store, match_id)
    return match_id, status, False


def materialize_person(store: Store, match_id: int) -> Optional[int]:
    """A person exists once a link is confirmed: create it and attach both mentions."""
    m = store.one("SELECT * FROM entity_match WHERE id = ?", (match_id,))
    if m is None:
        return None
    left = store.one("SELECT * FROM mention WHERE id = ?", (m["left_mention_id"],))
    right = store.one("SELECT * FROM mention WHERE id = ?", (m["right_mention_id"],))
    if left is None:
        return None
    name: Name = entities.mention_name(left)
    person_id = (
        left.get("person_id") or (right or {}).get("person_id") or entities.ensure_person(store, name, left["source"], left["address_norm"], "court_filing")
    )
    entities.attach_mention(store, int(left["id"]), int(person_id))
    if right:
        r_name = entities.mention_name(right)
        store.execute(
            "INSERT OR IGNORE INTO person_alias (person_id, alias_normalized, source) VALUES (?, ?, ?)", (person_id, r_name.normalized, right["source"])
        )
        if right["address_norm"]:
            store.execute(
                "INSERT OR IGNORE INTO person_address (person_id, address_norm, address_kind, observed_at) VALUES (?, ?, ?, ?)",
                (person_id, right["address_norm"], "tax_mailing", store.now()),
            )
        entities.attach_mention(store, int(right["id"]), int(person_id))
    store.update("entity_match", {"person_id": person_id}, "id = ?", (match_id,))
    return int(person_id)


def _emit_confirmed(store: Store, match_id: int, left_id: str, right_id: str, pid: Optional[str], prob: float, feats: Dict[str, float]) -> int:
    return store.insert(
        "event",
        {
            "kind": MATCH_CONFIRMED,
            "source": "resolver",
            "county": right_id.split("/", 1)[0],
            "natural_key": str(match_id),
            "parcel_id": pid,
            "observed_at": store.now(),
            "payload": dumps({"match_id": match_id, "left_id": left_id, "right_id": right_id, "probability": prob, "evidence": F.evidence_list(feats)}),
        },
    )


def get_match(store: Store, match_id: int) -> Optional[Dict[str, Any]]:
    row = store.one("SELECT * FROM entity_match WHERE id = ?", (match_id,))
    return hydrate(row) if row else None


def hydrate(row: Dict[str, Any]) -> Dict[str, Any]:
    for k in ("features", "gates"):
        row[k] = loads(row[k], {})
    for k in ("evidence", "flags"):
        row[k] = loads(row[k], [])
    return row


def matches(store: Store, status: Optional[str] = None, kind: Optional[str] = None, left_id: Optional[str] = None, limit: int = 1000) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM entity_match WHERE 1 = 1"
    params: List[Any] = []
    if status:
        sql += " AND status = ?"
        params.append(status)
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    if left_id:
        sql += " AND left_id = ?"
        params.append(left_id)
    sql += " ORDER BY left_id, probability DESC, right_id LIMIT ?"
    params.append(limit)
    return [hydrate(r) for r in store.query(sql, params)]
