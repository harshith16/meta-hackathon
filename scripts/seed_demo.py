"""Seed the fictional sample company for the demo.

PLACEHOLDER names: the founder picks real company/worker/manager names
later. Rename the three constants below and re-run; write_fact dedups
on content, so re-running after a rename adds the renamed facts (old
placeholder rows can be cleared by company id).

All data is fictional sample-company data. Never real company data.
Run: PYTHONPATH=. python scripts/seed_demo.py
"""

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import get_conn
from app.store import ingest_document, write_fact

COMPANY_ID = "sample-company"
COMPANY_NAME = "Meridian Field Services"  # PLACEHOLDER, founder picks later
WORKER_NAME = "Priya"  # PLACEHOLDER
MANAGER_NAME = "Rahul"  # PLACEHOLDER

TRAVEL_DOC = """Travel Policy — {company}

Employees on approved field trips are reimbursed for travel up to a
per-trip limit. Bookings above the limit need manager approval before
travel. Claims must be filed within 30 days of return.
"""

LEAVE_DOC = """Leave Policy — {company}

Employees receive 20 days of paid annual leave per year. Leave beyond
5 consecutive days needs manager approval. Unused leave lapses at
year end.
"""


def main():
    with get_conn() as conn:
        ingest_document(
            conn,
            COMPANY_ID,
            "doc-travel-policy",
            "Travel Policy",
            TRAVEL_DOC.format(company=COMPANY_NAME),
        )
        ingest_document(
            conn,
            COMPANY_ID,
            "doc-leave-policy",
            "Leave Policy",
            LEAVE_DOC.format(company=COMPANY_NAME),
        )
        write_fact(
            conn,
            COMPANY_ID,
            "Travel limit is 8000 rupees per trip.",
            ["employee", "manager"],
            effective_date=date(2026, 1, 1),
            source="document",
            document_id=None,
            triples=[
                {
                    "source": "travel_policy",
                    "relationship": "limit",
                    "destination": "8000",
                }
            ],
        )
        write_fact(
            conn,
            COMPANY_ID,
            "Employees receive 20 days of paid annual leave per year.",
            ["employee", "manager"],
            effective_date=date(2026, 1, 1),
            source="document",
            triples=[
                {
                    "source": "leave_policy",
                    "relationship": "annual_days",
                    "destination": "20",
                }
            ],
        )
        write_fact(
            conn,
            COMPANY_ID,
            "Reimbursement claims must be filed within 30 days of return.",
            ["employee", "manager"],
            effective_date=date(2026, 1, 1),
            source="document",
            triples=[
                {
                    "source": "reimbursement_policy",
                    "relationship": "claim_window_days",
                    "destination": "30",
                }
            ],
        )
        write_fact(
            conn,
            COMPANY_ID,
            "Refunds are issued up to 5000 rupees within 14 days of purchase.",
            ["employee", "manager"],
            effective_date=date(2026, 1, 1),
            source="document",
            triples=[
                {
                    "source": "refund_policy",
                    "relationship": "limit",
                    "destination": "5000",
                }
            ],
        )
        write_fact(
            conn,
            COMPANY_ID,
            f"{MANAGER_NAME} approves travel bookings above the limit.",
            ["employee", "manager"],
            effective_date=date(2026, 1, 1),
            source="document",
            triples=[
                {
                    "source": "travel_policy",
                    "relationship": "approver",
                    "destination": MANAGER_NAME.lower(),
                }
            ],
        )
        write_fact(
            conn,
            COMPANY_ID,
            f"{MANAGER_NAME} is the manager for field worker {WORKER_NAME}.",
            ["manager"],
            effective_date=date(2026, 1, 1),
            source="document",
            triples=[
                {
                    "source": "org_chart",
                    "relationship": "manages",
                    "destination": WORKER_NAME.lower(),
                }
            ],
        )
    print(f"seeded {COMPANY_ID} ({COMPANY_NAME})")


if __name__ == "__main__":
    main()
