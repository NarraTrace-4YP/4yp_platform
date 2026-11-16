# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "psycopg[binary]==3.3.9",
#     "wmill==1.875.0",
# ]
# ///
"""One dataset's reviews, with what the DHR Library lists for each. Used by the case viewer.

Reads the newest complete case-metadata snapshot. A review the library doesn't have (some
DHRC-RAW reviews were published elsewhere) is still listed, with its dataset's own title,
CSP and date of death, and no case details.
"""

from typing import Literal

from .datasets import check_dataset
from .db import apply_migrations, connect

REVIEWS_SQL = """
SELECT d.doc_id, d.library_id,
       COALESCE(r.title, d.dataset_title, d.doc_id),
       COALESCE(r.csp, d.dataset_csp),
       COALESCE(r.csp_region, d.dataset_csp_region),
       COALESCE(r.date_of_death, d.dataset_date_of_death),
       r.uploaded,
       r.library_id IS NOT NULL,
       d.source_url,
       d.loaded,
       d.source,
       (SELECT count(*) FROM evaluation.case_attributes AS a
        WHERE a.doc_id = d.doc_id AND a.dataset = d.dataset)
FROM evaluation.document_library_ids AS d
LEFT JOIN evaluation.library_reviews AS r ON r.library_id = d.library_id
    AND r.snapshot_id = (SELECT snapshot_id FROM evaluation.library_latest)
WHERE d.dataset = %s
ORDER BY d.doc_id
"""
COLUMNS = (
    "doc_id",
    "library_id",
    "title",
    "csp",
    "csp_region",
    "date_of_death",
    "uploaded",
    "in_library",
    "source_url",
    "loaded",
    "source",
    "n_details",
)


def main(dataset: Literal["DHRC-100", "DHRC-RAW"] = "DHRC-100"):
    """dataset: DHRC-100 or DHRC-RAW."""
    check_dataset(dataset)
    with connect() as conn:
        apply_migrations(conn)  # so the tables exist even before any pipeline has run
        snapshot = conn.execute(
            "SELECT snapshot_id, collected_at FROM evaluation.library_latest"
        ).fetchone()
        rows = conn.execute(REVIEWS_SQL, (dataset,)).fetchall()
    return {
        "dataset": dataset,
        "snapshot_id": snapshot[0] if snapshot else None,
        "collected_at": snapshot[1].isoformat() if snapshot else None,
        "reviews": [dict(zip(COLUMNS, row, strict=True)) for row in rows],
    }
