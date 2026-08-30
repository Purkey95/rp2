"""Signal definitions, evaluated against reconstructed historical state.

Every signal here is computable from the sales chain alone. Signals that
depend on current-only CAMA fields are listed in :data:`NOT_BACKTESTABLE` and
deliberately excluded rather than approximated -- an approximated signal would
leak the future into the test.
"""

from __future__ import annotations

from typing import Callable, Dict, Tuple

from ..normalize.owner import OwnerType
from .reconstruct import OwnerState

Signal = Callable[[OwnerState], bool]


def _tenure_at_least(years: float) -> Signal:
    def predicate(state: OwnerState) -> bool:
        return state.tenure_years >= years
    return predicate


def _individual_with_tenure(years: float) -> Signal:
    def predicate(state: OwnerState) -> bool:
        return state.is_individual and state.tenure_years >= years
    return predicate


SIGNALS: Dict[str, Signal] = {
    "tenure_20y": _tenure_at_least(20),
    "tenure_30y": _tenure_at_least(30),
    "tenure_40y": _tenure_at_least(40),
    "individual_owner": lambda s: s.is_individual,
    "company_owner": lambda s: s.owner_type == OwnerType.COMPANY,
    "trust_owner": lambda s: s.owner_type == OwnerType.TRUST,
    "decedent_marked_owner": lambda s: s.indicates_decedent,
    # The core premise behind the obituary pipeline: a long-held,
    # individually-owned property is the one likely to pass through an estate.
    "individual_tenure_20y": _individual_with_tenure(20),
    "individual_tenure_30y": _individual_with_tenure(30),
    "individual_tenure_40y": _individual_with_tenure(40),
    # Control: the opposite of a transition signal. If this scores like the
    # others, the metric is measuring churn rather than distress.
    "recently_bought_under_3y": lambda s: s.tenure_years < 3,
}

#: Signals that cannot be backtested from the data currently held, and why.
#: Listed explicitly so their absence is a stated limitation, not an omission.
NOT_BACKTESTABLE: Tuple[Tuple[str, str], ...] = (
    (
        "absentee / out-of-state owner",
        "mailing address exists only in the current CAMA snapshot; there is no "
        "history, so as-of-date values are unrecoverable. Validate forward from "
        "the first snapshot instead.",
    ),
    (
        "assessed value / equity",
        "CAMA carries current assessed values only; historical revaluations are "
        "not in this layer.",
    ),
    (
        "decedent marking at a past date",
        "the ESTATE/HEIRS owner string is a current-snapshot field. The "
        "grantee-derived version used above is a partial substitute.",
    ),
    (
        "tax delinquency, code violations, vacancy",
        "not yet ingested; these come from separate county sources.",
    ),
)
