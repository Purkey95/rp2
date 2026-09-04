"""A calibrated, explainable link model: logistic regression over named evidence.

Why this and not a hand-tuned additive score: the output is a probability you can set
a business threshold on, it improves as reviewers label, and each weight is still one
number per named piece of evidence -- the explanation is the model.

Training is MAP estimation with an L2 penalty toward the *seed* weights (the v1
rules translated into log-odds), so with a handful of labels the model stays where
the domain knowledge put it, and with hundreds the data takes over. Pure python.
"""

from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..store import Store, dumps, loads

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RULES_PATH = os.path.join(HERE, "rules.json")


def load_rules(path: Optional[str] = None) -> Dict[str, Any]:
    with open(path or DEFAULT_RULES_PATH, encoding="utf-8") as f:
        return json.load(f)


def sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


class LinkModel:
    def __init__(self, weights: Dict[str, float], intercept: float, version_id: Optional[int] = None, kind: str = "estate_case->parcel") -> None:
        self.weights = dict(weights)
        self.intercept = float(intercept)
        self.version_id = version_id
        self.kind = kind

    @classmethod
    def seed(cls, rules: Dict[str, Any], kind: str = "estate_case->parcel") -> "LinkModel":
        return cls(rules["seed_weights"], rules.get("seed_intercept", 0.0), None, kind)

    def logit(self, features: Dict[str, float]) -> float:
        return self.intercept + sum(self.weights.get(k, 0.0) * v for k, v in features.items())

    def predict(self, features: Dict[str, float]) -> float:
        return round(sigmoid(self.logit(features)), 4)

    def contributions(self, features: Dict[str, float]) -> List[Tuple[str, float]]:
        """Per-feature log-odds contributions, largest magnitude first: the explanation."""
        out = [(k, self.weights.get(k, 0.0) * v) for k, v in features.items() if v and self.weights.get(k)]
        return sorted(out, key=lambda kv: -abs(kv[1]))

    # --------------------------------------------------------- training ----

    def train(self, rows: Sequence[Tuple[Dict[str, float], int]], seed: "LinkModel", l2: float = 1.0, lr: float = 0.1, epochs: int = 400) -> Dict[str, Any]:
        """MAP logistic regression, penalized toward `seed`. Returns training metrics."""
        if not rows:
            return {"n": 0, "loss": None}
        names = sorted(set(seed.weights) | {k for f, _ in rows for k in f})
        w = {k: self.weights.get(k, seed.weights.get(k, 0.0)) for k in names}
        b = self.intercept
        n = len(rows)
        loss = 0.0
        for _ in range(epochs):
            grad = {k: 0.0 for k in names}
            gb = 0.0
            loss = 0.0
            for f, y in rows:
                p = sigmoid(b + sum(w[k] * f.get(k, 0.0) for k in names))
                err = p - y
                for k in names:
                    if f.get(k):
                        grad[k] += err * f[k]
                gb += err
                loss -= y * math.log(max(p, 1e-12)) + (1 - y) * math.log(max(1 - p, 1e-12))
            for k in names:
                grad[k] = grad[k] / n + l2 * (w[k] - seed.weights.get(k, 0.0)) / n
                w[k] -= lr * grad[k]
            gb = gb / n + l2 * (b - seed.intercept) / n
            b -= lr * gb
            loss += 0.5 * l2 * (sum((w[k] - seed.weights.get(k, 0.0)) ** 2 for k in names) + (b - seed.intercept) ** 2)
        self.weights = {k: round(v, 4) for k, v in w.items()}
        self.intercept = round(b, 4)
        return {"n": n, "loss": round(loss / n, 4), "positives": sum(y for _, y in rows)}

    # ----------------------------------------------------------- storage ---

    def save(self, store: Store, n_labels: int, metrics: Dict[str, Any], activate: bool = True) -> int:
        if activate:
            store.execute("UPDATE model_version SET active = 0 WHERE kind = ?", (self.kind,))
        self.version_id = store.insert(
            "model_version",
            {
                "kind": self.kind,
                "trained_at": store.now(),
                "n_labels": n_labels,
                "weights": dumps(self.weights),
                "intercept": self.intercept,
                "metrics": dumps(metrics),
                "active": 1 if activate else 0,
            },
        )
        store.commit()
        return self.version_id

    @classmethod
    def active(cls, store: Store, rules: Dict[str, Any], kind: str = "estate_case->parcel") -> "LinkModel":
        row = store.one("SELECT * FROM model_version WHERE kind = ? AND active = 1 ORDER BY id DESC LIMIT 1", (kind,))
        if row is None:
            return cls.seed(rules, kind)
        return cls(loads(row["weights"], {}), float(row["intercept"]), int(row["id"]), kind)

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "version_id": self.version_id, "intercept": self.intercept, "weights": self.weights}


def reliability(pairs: Sequence[Tuple[float, int]], bins: int = 5) -> Dict[str, Any]:
    """Calibration table: within each predicted-probability bin, how often was it a match?"""
    table = []
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        inside = [(p, y) for p, y in pairs if lo <= p < hi or (i == bins - 1 and p == 1.0)]
        if not inside:
            table.append({"bin": "{0:.1f}-{1:.1f}".format(lo, hi), "n": 0, "predicted": None, "observed": None})
            continue
        table.append(
            {
                "bin": "{0:.1f}-{1:.1f}".format(lo, hi),
                "n": len(inside),
                "predicted": round(sum(p for p, _ in inside) / len(inside), 3),
                "observed": round(sum(y for _, y in inside) / len(inside), 3),
            }
        )
    brier = round(sum((p - y) ** 2 for p, y in pairs) / len(pairs), 4) if pairs else None
    return {"bins": table, "brier": brier}
