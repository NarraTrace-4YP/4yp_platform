# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "psycopg[binary]==3.3.9",
#     "wmill==1.875.0",
# ]
# ///
"""O1 stage 2, app: case detail counts (Attributes tab).

Group library filter labels into the same sections used by the case viewer.
Return dataset and library totals for comparing attribute coverage in the app.
"""

from ...datasets import DATASETS
from ...db import apply_migrations, connect
from .get_case_details import SECTION_NAMES, SECTION_ORDER, split_detail

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

REVIEW_SECTION = "About the review"

# The fields every review's listing shows, counted the same way as the case details above:
# (group_label, label, library count, dataset or None, dataset count).
REVIEW_FIELDS_SQL = """
WITH latest AS (SELECT snapshot_id FROM evaluation.library_latest),
     fields AS (
         SELECT r.library_id, f.attribute, f.value
         FROM evaluation.library_reviews AS r
         JOIN latest USING (snapshot_id)
         CROSS JOIN LATERAL (VALUES
             ('Community Safety Partnership (CSP)', r.csp),
             ('CSP region', r.csp_region),
             ('Year of death', substring(r.date_of_death FROM '[0-9]{4}')),
             ('Year uploaded', substring(r.uploaded FROM '[0-9]{4}'))
         ) AS f (attribute, value)
         WHERE f.value <> ''
     ),
     library AS (
         SELECT attribute, value, count(DISTINCT library_id) AS n FROM fields GROUP BY 1, 2
     ),
     ours AS (
         SELECT DISTINCT dataset, doc_id, library_id
         FROM evaluation.document_library_ids
         WHERE library_id IS NOT NULL
     )
SELECT 'About the review / ' || f.attribute, f.value, l.n, o.dataset, count(DISTINCT o.doc_id)
FROM fields AS f
JOIN library AS l USING (attribute, value)
LEFT JOIN ours AS o ON o.library_id = f.library_id
GROUP BY 1, 2, 3, 4
"""

# How many reviews the library lists, then how many of each dataset's reviews it lists.
REVIEW_TOTALS_SQL = """
WITH latest AS (SELECT snapshot_id FROM evaluation.library_latest),
     listed AS (
         SELECT r.library_id
         FROM evaluation.library_reviews AS r JOIN latest USING (snapshot_id)
     )
SELECT NULL, count(*) FROM listed
UNION ALL
SELECT d.dataset, count(DISTINCT d.doc_id)
FROM evaluation.document_library_ids AS d
WHERE d.library_id IN (SELECT library_id FROM listed)
GROUP BY d.dataset
"""

SNAPSHOT_SQL = "SELECT snapshot_id, collected_at FROM evaluation.library_latest"

# The library page's order, with the fields from each review's listing after "About the death".
_AFTER = SECTION_ORDER.index("About the death") + 1
ORDER = [*SECTION_ORDER[:_AFTER], REVIEW_SECTION, *SECTION_ORDER[_AFTER:]]


def group_counts(rows) -> list[dict]:
    """[(group_label, label, library count, dataset or None, dataset count)] -> attributes
    in the library's page order, each with its values: most common first, or oldest first
    for years."""
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
        index = ORDER.index(section) if section in ORDER else len(ORDER)
        return index, section, attribute

    def in_order(attribute: str, values: dict) -> list[dict]:
        if attribute.startswith("Year"):
            return sorted(values.values(), key=lambda v: v["value"])
        return sorted(values.values(), key=lambda v: (-v["library"], v["value"]))

    return [
        {
            "section": SECTION_NAMES.get(section, section),
            "attribute": attribute,
            "values": in_order(attribute, values),
        }
        for (section, attribute), values in sorted(attributes.items(), key=lambda i: position(i[0]))
    ]


def as_totals(counts: dict) -> dict:
    """{None: library count, dataset: count} -> {"library": n, "datasets": {name: n}}."""
    return {
        "library": counts.get(None, 0),
        "datasets": {name: counts.get(name, 0) for name in DATASETS},
    }


def main():
    with connect() as conn:
        apply_migrations(conn)  # so the tables exist even before any pipeline has run
        snapshot = conn.execute(SNAPSHOT_SQL).fetchone()
        rows = conn.execute(COUNTS_SQL).fetchall() + conn.execute(REVIEW_FIELDS_SQL).fetchall()
        totals = dict(conn.execute(TOTALS_SQL).fetchall())
        listed = dict(conn.execute(REVIEW_TOTALS_SQL).fetchall())
    attributes = group_counts(rows)
    for attribute in attributes:
        if attribute["section"] == REVIEW_SECTION:
            attribute["totals"] = as_totals(listed)  # every listed review has these fields
    return {
        "snapshot_id": snapshot[0] if snapshot else None,
        "collected_at": snapshot[1].isoformat() if snapshot else None,
        "totals": as_totals(totals),
        "attributes": attributes,
    }
