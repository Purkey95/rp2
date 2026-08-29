"""Shared text hygiene for county record strings.

Mecklenburg CAMA data mixes ``None`` and ``""`` for absent values, carries
trailing spaces and doubled internal spaces (``'JAMISON  JOHN CASEY '``), and
uses punctuation inconsistently between the situs and mailing versions of the
same address (``'6307 CORY-BRET LN'`` vs ``'6307 CORY BRET LN'``).
"""

from __future__ import annotations

import re
from typing import List, Optional

_WHITESPACE = re.compile(r"\s+")
# Keep alphanumerics and spaces. Hyphens and slashes become spaces rather than
# being deleted, so CORY-BRET and CORY BRET collapse to the same tokens.
_PUNCT_TO_SPACE = re.compile(r"[^A-Z0-9]+")


def clean(value: Optional[str]) -> str:
    """Uppercase, collapse whitespace, and treat None/empty uniformly."""
    if value is None:
        return ""
    return _WHITESPACE.sub(" ", value.strip().upper())


def tokens(value: Optional[str]) -> List[str]:
    """Split into alphanumeric word-boundary tokens.

    Word-boundary tokenization is not a stylistic choice here: substring
    matching on this data is actively wrong. ``'VETAL DONALD III'`` contains
    "ETAL", ``'METALS FREEDOM INC'`` contains "ETAL", ``'GRACELIFE CHURCH'``
    contains "LIFE", and 1,119 parcels contain "REAL ESTATE" against only ~57
    genuine "... ESTATE" owner markers. A substring matcher for estate
    signals would be wrong far more often than right.
    """
    if value is None:
        return []
    return [t for t in _PUNCT_TO_SPACE.sub(" ", value.strip().upper()).split(" ") if t]


def blank_to_none(value: Optional[str]) -> Optional[str]:
    """Normalize the ``None``/``""``/``"   "`` trio to a single absent value."""
    cleaned = clean(value)
    return cleaned if cleaned else None
