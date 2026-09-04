"""Entity resolution: features -> calibrated probability -> hard gates -> disposition.

One resolver for every source pair. A link kind says which mentions are subjects and
which are candidates; features.py computes the evidence; model.py turns evidence into
a probability that is calibrated against reviewer labels; gates.py holds the rules
that no probability may override; resolver.py writes entity_match rows.
"""

from .resolver import ESTATE_TO_PARCEL, LinkKind, resolve  # noqa: F401
