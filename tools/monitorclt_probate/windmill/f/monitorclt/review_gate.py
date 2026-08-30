"""Hold the flow until a human has cleared the review queue. Step 3.

This is the step the whole Windmill setup exists for. The README's workflow says
"verify before acting" and lists what verification means -- chain of title,
liens, heirs, the representative's authority -- and then the pipeline hands over
a lead list. A schedule with no gate in it turns that paragraph into a
suggestion. A suspend step turns it into a state the flow cannot leave without a
named person clicking resume.

The module carrying this script has `suspend` set in flow.yaml, so the flow stops
here until an approval arrives or the timeout expires. Nothing downstream --
including publish_leads -- runs in the meantime.

Note what the approval request deliberately does not contain: no decedent names,
no addresses, no PINs. An approval notification lands in a Slack channel or an
inbox, which is not an access-controlled surface, and the queue itself is one
click away behind a login for exactly the people who should see it.
"""

from typing import TypedDict

import psycopg
import wmill


class postgresql(TypedDict):
    host: str
    port: int
    user: str
    dbname: str
    sslmode: str
    password: str


def main(database: postgresql, load_summary: dict, approver: str = None):
    """Summarise what is waiting and produce the resume/cancel links."""
    connection = psycopg.connect(
        host=database["host"],
        port=database["port"],
        user=database["user"],
        password=database["password"],
        dbname=database["dbname"],
        sslmode=database.get("sslmode", "prefer"),
    )
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT count(*), coalesce(max(score), 0) FROM probate.v_review_queue")
        pending, top_score = cursor.fetchone()
        cursor.execute("SELECT count(*) FROM probate.v_estate_property")
        (confirmed_leads,) = cursor.fetchone()
        # Candidates the matcher confirmed on its own since the last approval:
        # these reach outreach without anybody having looked at them, so the
        # number is the one an approver should actually be weighing.
        cursor.execute(
            "SELECT count(*) FROM probate.entity_match"
            " WHERE status = 'confirmed' AND reviewer IS NULL"
        )
        (auto_confirmed,) = cursor.fetchone()
        cursor.close()
    finally:
        connection.close()

    urls = wmill.get_resume_urls(approver)

    return {
        "pending_review": pending,
        "highest_pending_score": float(top_score),
        "auto_confirmed_unreviewed": auto_confirmed,
        "confirmed_leads_total": confirmed_leads,
        "this_run": {
            "confirmed": load_summary.get("confirmed"),
            "pending": load_summary.get("pending"),
            "rejected": load_summary.get("rejected"),
            "rules_version": load_summary.get("rules_version"),
        },
        "clear_the_queue_in": "probate.v_review_queue_detail",
        "decide_with": "SELECT probate.record_review(<match_id>, 'confirmed'|'rejected', '<note>')",
        "before_approving": [
            "Clear or accept the pending queue -- resuming does not decide it.",
            "Spot-check the auto-confirmed matches: they reach outreach unreviewed.",
            "Confirmed is a records match, not a conclusion: chain of title, liens,",
            "heirs, and the representative's authority still have to be verified.",
            "Contact the personal representative or estate attorney -- nobody else.",
        ],
        "resume": urls["resume"],
        "cancel": urls["cancel"],
    }
