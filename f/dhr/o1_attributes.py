# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "psycopg[binary]==3.3.9",
#     "wmill==1.875.0",
# ]
# ///
"""How often each case detail appears: in the whole DHR Library, and in each dataset.

Answers questions like "what share of DHRC-100's victims were female, compared with the
library as a whole?", which shows how representative each dataset is. Counts come from the
newest complete case-metadata snapshot. Used by the O1 app's Attributes tab.

Each count is a number of reviews. A review that wasn't returned for a value is not
counted, which can mean "no" or "not recorded", so shares are out of the reviews that
have at least one case detail.
"""

from .case_viewer_details import SECTION_NAMES, SECTION_ORDER, split_detail
from .datasets import DATASETS
from .db import apply_migrations, connect

COUNTS_SQL = """
WITH latest AS (SELECT snapshot_id FROM evaluation.library_latest),
     ours AS (
         SELECT DISTINCT dataset, doc_id, library_id
         FROM evaluation.document_library_ids
         WHERE library_id IS NOT NULL
     )
SELECT f.group_label, f.label, f.n_results, o.dataset, count(DISTINCT o.doc_id)
FROM evaluation.library_filters AS f
JOIN latest USING (snapshot_id)
LEFT JOIN evaluation.library_attributes AS a
    ON a.snapshot_id = f.snapshot_id AND a.field = f.field AND a.value = f.value
LEFT JOIN ours AS o ON o.library_id = a.library_id
GROUP BY f.group_label, f.label, f.n_results, o.dataset
"""

# How many reviews have at least one case detail: the library's, then each dataset's.
TOTALS_SQL = """
WITH latest AS (SELECT snapshot_id FROM evaluation.library_latest),
     detailed AS (
         SELECT DISTINCT a.library_id
         FROM evaluation.library_attributes AS a JOIN latest USING (snapshot_id)
     )
SELECT NULL, count(*) FROM detailed
UNION ALL
SELECT d.dataset, count(*)
FROM evaluation.document_library_ids AS d
WHERE d.library_id IN (SELECT library_id FROM detailed)
GROUP BY d.dataset
"""

SNAPSHOT_SQL = "SELECT snapshot_id, collected_at FROM evaluation.library_latest"


def group_counts(rows) -> list[dict]:
    """[(group_label, label, library count, dataset or None, dataset count)] -> attributes
    in the library's page order, each with its values, most common first."""
    attributes: dict[tuple[str, str], dict[str, dict]] = {}
    for group_label, label, n_library, dataset, n_dataset in rows:
        section, attribute, value = split_detail(group_label, label)
        values = attributes.setdefault((section, attribute), {})
        entry = values.setdefault(
            value, {"value": value, "library": n_library, "datasets": dict.fromkeys(DATASETS, 0)}
        )
        if dataset in entry["datasets"]:
            entry["datasets"][dataset] = n_dataset

    def position(key: tuple[str, str]) -> tuple[int, str, str]:
        section, attribute = key
        index = SECTION_ORDER.index(section) if section in SECTION_ORDER else len(SECTION_ORDER)
        return index, section, attribute

    return [
        {
            "section": SECTION_NAMES.get(section, section),
            "attribute": attribute,
            "values": sorted(values.values(), key=lambda v: (-v["library"], v["value"])),
        }
        for (section, attribute), values in sorted(attributes.items(), key=lambda i: position(i[0]))
    ]


def main():
    with connect() as conn:
        apply_migrations(conn)  # so the tables exist even before any pipeline has run
        snapshot = conn.execute(SNAPSHOT_SQL).fetchone()
        rows = conn.execute(COUNTS_SQL).fetchall()
        totals = dict(conn.execute(TOTALS_SQL).fetchall())
    return {
        "snapshot_id": snapshot[0] if snapshot else None,
        "collected_at": snapshot[1].isoformat() if snapshot else None,
        "totals": {
            "library": totals.get(None, 0),
            "datasets": {name: totals.get(name, 0) for name in DATASETS},
        },
        "attributes": group_counts(rows),
    }
