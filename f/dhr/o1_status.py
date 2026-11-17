# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "psycopg[binary]==3.3.9",
#     "wmill==1.875.0",
# ]
# ///
"""Where O1 (corpus preparation) stands, read from the database.

For each dataset: the version loaded, how many reviews it has, where they were published,
how many the Home Office DHR Library has case details for, and whether that version has
been grouped by section. Then the newest case metadata snapshot. The O1 app shows this, and it is the last step of the O1 pipeline
(f/dhr/o1_pipeline), so every pipeline run ends with a summary of what it produced.
"""

import os

from .datasets import DATASETS
from .db import apply_migrations, connect
from .version import GIT_COMMIT

VERSIONS_SQL = """
SELECT v.dataset, v.revision, v.repo_id, v.loaded_at, v.git_commit,
       count(d.doc_id), COALESCE(sum(d.n_pages), 0), COALESCE(sum(d.n_blocks), 0)
FROM corpus.current_versions AS v
LEFT JOIN corpus.documents AS d ON d.revision = v.revision
GROUP BY v.dataset, v.revision, v.repo_id, v.loaded_at, v.git_commit
"""
VERSION_COLUMNS = (
    "revision",
    "repo_id",
    "loaded_at",
    "git_commit",
    "n_reviews",
    "n_pages",
    "n_blocks",
)

# One row per dataset: how its reviews (as its own metadata lists them) break down.
COVERAGE_SQL = """
WITH latest AS (SELECT snapshot_id FROM evaluation.library_latest),
     detailed AS (
         SELECT DISTINCT a.library_id
         FROM evaluation.library_attributes AS a JOIN latest USING (snapshot_id)
     )
SELECT d.dataset,
       count(*),
       count(*) FILTER (WHERE d.loaded),
       count(*) FILTER (WHERE d.source = 'Home Office website'),
       count(*) FILTER (WHERE d.source = 'HALT'),
       count(*) FILTER (WHERE d.source = 'Elsewhere'),
       count(*) FILTER (WHERE d.source IS NULL),
       count(r.library_id),
       count(*) FILTER (WHERE d.library_id IN (SELECT library_id FROM detailed))
FROM evaluation.document_library_ids AS d
LEFT JOIN evaluation.library_reviews AS r
    ON r.library_id = d.library_id AND r.snapshot_id = (SELECT snapshot_id FROM latest)
GROUP BY d.dataset
"""
COVERAGE_COLUMNS = (
    "n_listed",
    "n_loaded",
    "n_home_office",
    "n_halt",
    "n_elsewhere",
    "n_no_link",
    "n_in_library",
    "n_with_details",
)

# The grouping by section of each dataset's newest version (none if it hasn't been grouped).
SECTIONS_SQL = """
SELECT v.dataset, g.headings, g.grouped_at, g.git_commit, g.n_documents, g.n_sections,
       g.n_blocks, g.n_skipped,
       (SELECT count(*) FROM corpus.sections AS s
        WHERE s.revision = g.revision AND s.last_page > s.first_page)
FROM corpus.current_versions AS v
JOIN corpus.grouped_versions AS g ON g.revision = v.revision
"""
SECTIONS_COLUMNS = (
    "headings",
    "grouped_at",
    "git_commit",
    "n_reviews",
    "n_sections",
    "n_blocks",
    "n_skipped",
    "n_across_pages",
)

SNAPSHOT_SQL = """
SELECT s.snapshot_id, s.collected_at, s.git_commit, s.n_reviews, s.n_filters,
       (SELECT count(*) FROM evaluation.library_attributes AS a
        WHERE a.snapshot_id = s.snapshot_id),
       (SELECT count(DISTINCT a.library_id) FROM evaluation.library_attributes AS a
        WHERE a.snapshot_id = s.snapshot_id)
FROM evaluation.library_latest AS s
"""
SNAPSHOT_COLUMNS = (
    "snapshot_id",
    "collected_at",
    "git_commit",
    "n_reviews",
    "n_filters",
    "n_details",
    "n_reviews_with_details",
)


def as_dict(columns: tuple[str, ...], row) -> dict:
    """A database row as {column: value}, with dates as text (so it can be sent as JSON)."""
    return {
        name: value.isoformat() if hasattr(value, "isoformat") else value
        for name, value in zip(columns, row, strict=True)
    }


def describe(
    versions: dict, coverage: dict, snapshot: dict | None, sections: dict | None = None
) -> dict:
    """Puts the query results together, one entry per dataset in DATASETS order."""
    datasets = []
    for name, settings in DATASETS.items():
        datasets.append(
            {
                "name": name,
                "repo_id": settings["repo_id"],
                "expected_reviews": settings["reviews"],
                "strict": settings["strict"],
                "headings": settings["headings"],
                "version": versions.get(name),
                "coverage": coverage.get(name, dict.fromkeys(COVERAGE_COLUMNS, 0)),
                "sections": (sections or {}).get(name),
            }
        )
    return {
        "datasets": datasets,
        "case_metadata": snapshot,
        "code_version": GIT_COMMIT,
        "windmill_url": os.environ.get("WM_BASE_URL"),
        "workspace": os.environ.get("WM_WORKSPACE"),
    }


def main():
    with connect() as conn:
        apply_migrations(conn)  # so the tables exist even before any pipeline has run
        versions = {row[0]: as_dict(VERSION_COLUMNS, row[1:]) for row in conn.execute(VERSIONS_SQL)}
        coverage = {
            row[0]: as_dict(COVERAGE_COLUMNS, row[1:]) for row in conn.execute(COVERAGE_SQL)
        }
        sections = {
            row[0]: as_dict(SECTIONS_COLUMNS, row[1:]) for row in conn.execute(SECTIONS_SQL)
        }
        row = conn.execute(SNAPSHOT_SQL).fetchone()
    snapshot = as_dict(SNAPSHOT_COLUMNS, row) if row else None
    return describe(versions, coverage, snapshot, sections)
