"""Run the probate -> real property cross-reference. Step 1 of the flow.

A thin wrapper: the matcher is crossref.py in tools/monitorclt_probate, unchanged
and still pure stdlib. Nothing about the scoring lives here, because a rule you
can only read inside a workflow engine is a rule nobody reviews.

The one thing this step adds is where the rules come from. `match_rules.json` is
policy -- the weights that decide who ends up on a lead list -- so in Windmill it
is a Variable, not a file baked into the image: changing it is then an audited
act by a named user rather than a redeploy nobody sees.
"""

# The explicit relative import is what makes Windmill ship crossref.py to the
# worker alongside this script; load_rules is genuinely used below.
from f.monitorclt.crossref import crossref, load_rules

import json

import wmill


def main(
    estates: list,
    parcels: list,
    deeds: list = None,
    rules_variable: str = "f/monitorclt/probate_match_rules",
):
    """Cross-reference estate cases against parcels and deeds.

    estates / parcels / deeds are lists of records in the shape crossref.py
    documents. rules_variable is a Windmill Variable holding match_rules.json;
    leave the default unless you are testing an alternative weighting.
    """
    raw = wmill.get_variable(rules_variable)
    rules = json.loads(raw) if raw else load_rules(None)

    result = crossref(estates, parcels, deeds or [], rules)

    counts = {"confirmed": 0, "pending": 0, "rejected": 0}
    for link in result["matches"]:
        counts[link["status"]] = counts.get(link["status"], 0) + 1

    # Surfaced on the run page so a glance at the flow tells you whether the
    # inputs were what you expected -- a county feed that silently returns three
    # parcels looks exactly like a quiet week otherwise.
    wmill.set_progress(100)
    return {
        "result": result,
        "counts": counts,
        "inputs": {
            "estate_cases": len(estates),
            "parcels": len(parcels),
            "deeds": len(deeds or []),
        },
        "input_gaps": len(result.get("unmatched_estate_parcels", [])),
        "skipped_estates": len(result.get("skipped_estates", [])),
    }
