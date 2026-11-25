# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "psycopg[binary]==3.3.9",
#     "wmill==1.875.0",
# ]
# ///
"""O1 stage 2, app: case details (Cases tab).

Split filter paths into sections, attributes, and values. Keep deeper path
segments in the value so the case viewer does not lose library detail.
"""

from typing import Literal

from ...datasets import check_dataset
from ...db import apply_migrations, connect

# The order the library's search page shows its sections in.
SECTION_ORDER = [
    "Most filtered for DHRs",
    "About the victim",
    "About the perpetrator",
    "About the death",
    "Relationships",
    "Filter by theme",
]
SECTION_NAMES = {"Most filtered for DHRs": "Categories", "Filter by theme": "Themes"}

DETAILS_SQL = """
SELECT group_label, label
FROM evaluation.case_attributes
WHERE dataset = %s AND doc_id = %s
ORDER BY group_label, label
"""
REVIEW_SQL = """
SELECT d.doc_id, d.library_id,
       COALESCE(r.title, d.dataset_title, d.doc_id),
       COALESCE(r.csp, d.dataset_csp),
       COALESCE(r.csp_region, d.dataset_csp_region),
       COALESCE(r.date_of_death, d.dataset_date_of_death),
       r.uploaded,
       r.library_id IS NOT NULL,
       d.source_url,
       d.loaded,
       d.source
FROM evaluation.document_library_ids AS d
LEFT JOIN evaluation.library_reviews AS r ON r.library_id = d.library_id
    AND r.snapshot_id = (SELECT snapshot_id FROM evaluation.library_latest)
WHERE d.dataset = %s AND d.doc_id = %s
"""
REVIEW_COLUMNS = (
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
)


def split_detail(group_label: str, label: str) -> tuple[str, str, str]:
    """(section, attribute, value) for one detail, as the case viewer shows it."""
    parts = group_label.split(" / ", 2)
    section = parts[0]
    if len(parts) == 1:
        # A section with no headings of its own: themes say who they're about.
        attribute, _, value = label.partition(" - ")
        return (section, attribute, value) if value else (section, "", label)
    attribute = parts[1]
    value = f"{parts[2]}: {label}" if len(parts) == 3 else label
    return section, attribute, value


def group_details(rows: list[tuple[str, str]]) -> list[dict]:
    """[(group_label, label)] -> sections in page order, each with its attributes and values."""
    sections: dict[str, dict[str, list[str]]] = {}
    for group_label, label in rows:
        section, attribute, value = split_detail(group_label, label)
        sections.setdefault(section, {}).setdefault(attribute, []).append(value)

    def position(section: str) -> tuple[int, str]:
        index = SECTION_ORDER.index(section) if section in SECTION_ORDER else len(SECTION_ORDER)
        return index, section

    return [
        {
            "section": SECTION_NAMES.get(section, section),
            "attributes": [
                {"attribute": attribute, "values": sorted(values)}
                for attribute, values in sorted(sections[section].items())
            ],
        }
        for section in sorted(sections, key=position)
    ]


def main(dataset: Literal["DHRC-100", "DHRC-RAW"] = "DHRC-100", doc_id: str = ""):
    """dataset: DHRC-100 or DHRC-RAW. doc_id: the review's file name without .json."""
    check_dataset(dataset)
    with connect() as conn:
        apply_migrations(conn)  # so the tables exist even before any pipeline has run
        review = conn.execute(REVIEW_SQL, (dataset, doc_id)).fetchone()
        if review is None:
            raise ValueError(f"No review called {doc_id!r} in the latest {dataset} version")
        rows = conn.execute(DETAILS_SQL, (dataset, doc_id)).fetchall()
    return {**dict(zip(REVIEW_COLUMNS, review, strict=True)), "sections": group_details(rows)}
