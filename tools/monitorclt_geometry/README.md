# MonitorCLT Parcel-Geometry Wedges

The taxonomy's coverage-by-arbitrage view showed **relationship** and
**development** at 0 built — the two most differentiated, hardest-to-copy
arbitrages. These are the one slice of each that needs **no external data**, just
the parcel layer we already hold.

## Signals

- **`relationship_value_access`** *(relationship arbitrage)* — "Who would this
  parcel be worth substantially more to?" A parcel with **no road frontage** whose
  access crosses a specific neighbor is worth far more to *that neighbor* than to
  the open market — a natural buyer already exists, so a deal is unusually doable.
  Emitted on the landlocked parcel (our motivated seller), naming the controlling
  neighbor(s); a **sole** controller is the strongest leverage.
- **`hidden_density_zoning_mismatch`** *(development arbitrage)* — "What is this
  property capable of becoming?" Zoning-implied max units minus what's actually
  built. A parcel zoned for 22 units with one house on it is redevelopment upside
  the parcel record doesn't advertise. Max units come from an explicit
  `zoning_max_units` field, or `lot_area_acres × units_per_acre[zoning]`.

## Run

```bash
python3 geometry.py --parcels parcels.csv     # or parcels.jsonl
python3 test_geometry.py
```

Expected parcel fields: `apn`, `road_frontage`, `neighbors` (list or comma
string), `zoning`, `lot_area_acres` (or `lot_area_sqft`), `existing_units`. Both
signals are derived inferences from geometry/zoning, so the confidence model marks
them lower-reliability — **a lift, not hard evidence**.

## Refinements (next)

- Compute `neighbors` and `road_frontage` from actual parcel polygons (shared-edge
  adjacency, frontage-on-ROW test) instead of expecting them pre-computed — the
  Regrid geometry we hold supports this on the host.
- `assemblage_adjacency_value`: same adjacency graph, keyed on an *active assemblage
  buyer* (owner acquiring contiguous parcels) rather than access control.
