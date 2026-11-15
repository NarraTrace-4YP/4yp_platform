# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "psycopg[binary]==3.3.9",
#     "wmill==1.875.0",
# ]
# ///
"""Our reviews, with what the DHR Library lists for each. Used by the case viewer app.

Reads the newest complete case-metadata snapshot (evaluation.case_metadata), so it shows
nothing until collect_case_metadata has run in full at least once.
"""

from .db import apply_migrations, connect

REVIEWS_SQL = """
SELECT m.doc_id, m.library_id, m.title, m.csp, m.csp_region, m.date_of_death, m.uploaded,
       (SELECT count(*) FROM evaluation.case_attributes AS a WHERE a.doc_id = m.doc_id)
FROM evaluation.case_metadata AS m
ORDER BY m.doc_id
"""
COLUMNS = (
    "doc_id",
    "library_id",
    "title",
    "csp",
    "csp_region",
    "date_of_death",
    "uploaded",
    "n_details",
)


def main():
    with connect() as conn:
        apply_migrations(conn)  # so the tables exist even before any pipeline has run
        snapshot = conn.execute(
            "SELECT snapshot_id, collected_at FROM evaluation.library_latest"
        ).fetchone()
        rows = conn.execute(REVIEWS_SQL).fetchall()
    return {
        "snapshot_id": snapshot[0] if snapshot else None,
        "collected_at": snapshot[1].isoformat() if snapshot else None,
        "reviews": [dict(zip(COLUMNS, row, strict=True)) for row in rows],
    }
