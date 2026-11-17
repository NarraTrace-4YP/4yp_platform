# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "psycopg[binary]==3.3.9",
#     "wmill==1.875.0",
# ]
# ///
"""What "Group blocks by section" (O1 step 3) produced, for the O1 app's Sections tab.

Reads the newest grouped version of a dataset, at three levels of detail:

- no doc_id: how the grouping went, and every review with how many sections and blocks it
  has, plus any reviews left out because their headings didn't fit together;
- a doc_id: that review's sections in reading order (each with its level, parent, pages
  and blocks), and the blocks that come before its first heading;
- a doc_id and a section_id: that section's blocks, its subsections included, in reading
  order (corpus.section_blocks), so the app can show its text.

Pages count from 0, as in the block ids; the app shows them from 1.
"""

from typing import Literal

from .datasets import check_dataset
from .db import apply_migrations, connect

GROUPED_SQL = """
SELECT g.revision, g.headings, g.grouped_at, g.git_commit, g.n_documents, g.n_sections,
       g.n_blocks, g.n_skipped, g.skipped
FROM corpus.current_versions AS v
JOIN corpus.grouped_versions AS g USING (revision)
WHERE v.dataset = %s
"""

# Every review of the version, grouped or not, with what grouping made of it.
REVIEWS_SQL = """
WITH s AS (
    SELECT doc_id, count(*) AS n_sections, max(level) AS depth,
           count(*) FILTER (WHERE last_page > first_page) AS n_across_pages
    FROM corpus.sections WHERE revision = %(revision)s GROUP BY doc_id
), b AS (
    SELECT doc_id, count(*) AS n_blocks, count(*) FILTER (WHERE section_id IS NULL) AS n_before
    FROM corpus.blocks WHERE revision = %(revision)s GROUP BY doc_id
)
SELECT d.doc_id, d.n_pages, COALESCE(s.n_sections, 0), COALESCE(s.depth, 0),
       COALESCE(s.n_across_pages, 0), COALESCE(b.n_blocks, 0), COALESCE(b.n_before, 0)
FROM corpus.documents AS d
LEFT JOIN s USING (doc_id)
LEFT JOIN b USING (doc_id)
WHERE d.revision = %(revision)s
ORDER BY d.doc_id
"""
REVIEW_COLUMNS = (
    "doc_id",
    "n_pages",
    "n_sections",
    "depth",
    "n_across_pages",
    "n_blocks",
    "n_before",
)

SECTIONS_SQL = """
SELECT section_id, parent_id, level, heading, position, last_position, first_page,
       last_page, n_blocks
FROM corpus.sections
WHERE revision = %s AND doc_id = %s
ORDER BY position
"""
SECTION_COLUMNS = (
    "section_id",
    "parent_id",
    "level",
    "heading",
    "position",
    "last_position",
    "first_page",
    "last_page",
    "n_blocks",
)

# The blocks before a review's first heading (a title page, a contents list, ...).
BEFORE_SQL = """
SELECT position, page, block_type, NULL, NULL, NULL, text
FROM corpus.blocks
WHERE revision = %s AND doc_id = %s AND section_id IS NULL
ORDER BY position
"""

SECTION_BLOCKS_SQL = """
SELECT position, page, block_type, section_id, level, heading, text
FROM corpus.section_blocks(%s, %s, %s)
"""
BLOCK_COLUMNS = ("position", "page", "block_type", "section_id", "level", "heading", "text")


def as_dicts(rows, columns) -> list[dict]:
    return [dict(zip(columns, row, strict=True)) for row in rows]


def outline(rows) -> list[dict]:
    """A review's sections in reading order. n_inside counts every block inside a section
    (its subsections' headings and blocks too), n_blocks only the ones directly in it."""
    sections = as_dicts(rows, SECTION_COLUMNS)
    for s in sections:
        s["n_inside"] = s["last_position"] - s["position"]
    return sections


def split_skipped(problems: list[str]) -> list[dict]:
    """group_sections records each left-out review as "<doc_id>: <why>"."""
    result = []
    for problem in problems or []:
        doc_id, _, why = problem.partition(": ")
        result.append({"doc_id": doc_id, "problem": why or problem})
    return result


def summary(row) -> dict | None:
    if row is None:
        return None
    revision, headings, grouped_at, git_commit, n_reviews, n_sections, n_blocks, n_skipped, _ = row
    return {
        "revision": revision,
        "headings": headings,
        "grouped_at": grouped_at.isoformat(),
        "git_commit": git_commit,
        "n_reviews": n_reviews,
        "n_sections": n_sections,
        "n_blocks": n_blocks,
        "n_skipped": n_skipped,
    }


def main(
    dataset: Literal["DHRC-100", "DHRC-RAW"] = "DHRC-100",
    doc_id: str = "",
    section_id: str = "",
):
    check_dataset(dataset)
    with connect() as conn:
        apply_migrations(conn)  # so the tables exist even before any pipeline has run
        grouped = conn.execute(GROUPED_SQL, (dataset,)).fetchone()
        if grouped is None:
            return {"dataset": dataset, "grouped": None, "reviews": [], "skipped": []}
        revision = grouped[0]
        if section_id:
            rows = conn.execute(SECTION_BLOCKS_SQL, (dataset, doc_id, section_id)).fetchall()
            return {
                "doc_id": doc_id,
                "section_id": section_id,
                "blocks": as_dicts(rows, BLOCK_COLUMNS),
            }
        if doc_id:
            sections = conn.execute(SECTIONS_SQL, (revision, doc_id)).fetchall()
            before = conn.execute(BEFORE_SQL, (revision, doc_id)).fetchall()
            return {
                "doc_id": doc_id,
                "sections": outline(sections),
                "before": as_dicts(before, BLOCK_COLUMNS),
            }
        reviews = conn.execute(REVIEWS_SQL, {"revision": revision}).fetchall()
    return {
        "dataset": dataset,
        "grouped": summary(grouped),
        "reviews": as_dicts(reviews, REVIEW_COLUMNS),
        "skipped": split_skipped(grouped[8]),
    }
