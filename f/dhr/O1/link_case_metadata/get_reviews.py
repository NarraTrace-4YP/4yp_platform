# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "psycopg[binary]==3.3.9",
#     "wmill==1.875.0",
# ]
# ///
"""List loaded reviews and filter facets for the O1 case viewer.

Join review provenance with linked DHR Library metadata. Keep facet counts and
review facts in the response shape consumed by the React app.
"""

import re
from typing import Literal

from ...datasets import check_dataset
from ...db import apply_migrations, connect
from .get_case_details import SECTION_NAMES, SECTION_ORDER, split_detail

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

# Every case detail the library can hold (every filter option searched), and which of them
# each review in the dataset has.
CATALOGUE_SQL = """
SELECT group_label, label FROM evaluation.library_filters
WHERE snapshot_id = (SELECT snapshot_id FROM evaluation.library_latest)
"""
DETAILS_SQL = """
SELECT doc_id, group_label, label FROM evaluation.case_attributes WHERE dataset = %s
"""

REVIEW_SECTION = "About the review"
# The facts about each review itself, in the order the explorer shows them.
REVIEW_ATTRIBUTES = (
    "Published on",
    "In the DHR Library",
    "Loaded",
    "CSP region",
    "Community Safety Partnership (CSP)",
    "Year of death",
    "Year uploaded",
)


def year(text: str | None) -> str | None:
    """The first four-digit year in some text ("May 2023", "2023-05"), or None."""
    match = re.search(r"\b(\d{4})\b", text or "")
    return match.group(1) if match else None


def review_facts(review: dict) -> list[tuple[str, str]]:
    """The review's own facts as (attribute, value) pairs for "About the review"."""
    facts = [
        ("Published on", review["source"] or "No link"),
        ("In the DHR Library", "Yes" if review["in_library"] else "No"),
        ("Loaded", "Yes" if review["loaded"] else "Skipped when loading"),
        ("CSP region", review["csp_region"]),
        ("Community Safety Partnership (CSP)", review["csp"]),
        ("Year of death", year(review["date_of_death"])),
        ("Year uploaded", year(review["uploaded"])),
    ]
    return [(attribute, value) for attribute, value in facts if value]


def build_facets(
    catalogue: list[tuple[str, str]], details: list[tuple[str, str, str]], reviews: list[dict]
) -> list[dict]:
    """Every attribute and value to filter by, with ids, and each review's "tags" (the ids
    of the values it has, added to each review dict). catalogue: [(group_label, label)];
    details: [(doc_id, group_label, label)]."""
    values: dict[tuple[str, str], set[str]] = {}
    for group_label, label in catalogue:
        section, attribute, value = split_detail(group_label, label)
        values.setdefault((section, attribute), set()).add(value)
    review_tags: dict[str, list[tuple[str, str, str]]] = {r["doc_id"]: [] for r in reviews}
    for doc_id, group_label, label in details:
        section, attribute, value = split_detail(group_label, label)
        values.setdefault((section, attribute), set()).add(value)  # in case the catalogue lacks it
        if doc_id in review_tags:
            review_tags[doc_id].append((section, attribute, value))
    for review in reviews:
        for attribute, value in review_facts(review):
            values.setdefault((REVIEW_SECTION, attribute), set()).add(value)
            review_tags[review["doc_id"]].append((REVIEW_SECTION, attribute, value))

    order = [REVIEW_SECTION, *SECTION_ORDER]

    def position(key: tuple[str, str]) -> tuple:
        section, attribute = key
        index = order.index(section) if section in order else len(order)
        if section == REVIEW_SECTION:
            return index, REVIEW_ATTRIBUTES.index(attribute), attribute
        return index, 0, attribute

    facets, ids = [], {}
    for key in sorted(values, key=position):
        section, attribute = key
        entries = []
        for value in sorted(values[key]):
            ids[(section, attribute, value)] = len(ids)
            entries.append({"id": ids[(section, attribute, value)], "value": value})
        facets.append(
            {
                "section": SECTION_NAMES.get(section, section),
                "attribute": attribute,
                "values": entries,
            }
        )
    for review in reviews:
        review["tags"] = sorted({ids[tag] for tag in review_tags[review["doc_id"]]})
    return facets


def main(dataset: Literal["DHRC-100", "DHRC-RAW"] = "DHRC-100"):
    """dataset: DHRC-100 or DHRC-RAW."""
    check_dataset(dataset)
    with connect() as conn:
        apply_migrations(conn)  # so the tables exist even before any pipeline has run
        snapshot = conn.execute(
            "SELECT snapshot_id, collected_at FROM evaluation.library_latest"
        ).fetchone()
        rows = conn.execute(REVIEWS_SQL, (dataset,)).fetchall()
        catalogue = conn.execute(CATALOGUE_SQL).fetchall()
        details = conn.execute(DETAILS_SQL, (dataset,)).fetchall()
    reviews = [dict(zip(COLUMNS, row, strict=True)) for row in rows]
    facets = build_facets(catalogue, details, reviews)
    return {
        "dataset": dataset,
        "snapshot_id": snapshot[0] if snapshot else None,
        "collected_at": snapshot[1].isoformat() if snapshot else None,
        "facets": facets,
        "reviews": reviews,
    }
