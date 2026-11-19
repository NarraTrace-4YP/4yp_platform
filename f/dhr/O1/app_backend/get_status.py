# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "psycopg[binary]==3.3.9",
#     "wmill==1.875.0",
# ]
# ///
"""Report O1 corpus coverage and the latest outputs of each implemented stage.

Read dataset versions, metadata coverage, grouped sections, and template-matching
summaries for the overview app. The pipeline also runs this script as its final
step so its result describes the stored state after all requested stages.
"""

import os

from ...datasets import DATASETS
from ...db import apply_migrations, connect
from ...version import GIT_COMMIT

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

# The template candidates of each dataset's newest grouped version, one row per guidance
# they were found against (none if not found yet).
CANDIDATES_SQL = """
SELECT v.dataset, c.guidance, c.matched_at, c.git_commit, c.model, c.k, c.n_sections,
       c.n_candidates,
       (SELECT count(DISTINCT (t.doc_id, t.section_id)) FROM corpus.template_candidates AS t
        WHERE t.revision = c.revision AND t.heading_rank <= c.k
          AND t.heading_score >= c.min_heading_score
          AND t.template IN (SELECT template FROM corpus.templates WHERE guidance = c.guidance))
FROM corpus.current_versions AS v
JOIN corpus.candidate_versions AS c ON c.revision = v.revision
ORDER BY v.dataset, c.guidance
"""
CANDIDATES_COLUMNS = (
    "guidance",
    "matched_at",
    "git_commit",
    "model",
    "k",
    "n_sections",
    "n_candidates",
    "n_with_heading_match",
)

# The template choices (O1 stage 5) of each dataset's newest grouped version, one row per
# guidance they were made against (none if not made yet).
CHOICES_SQL = """
SELECT v.dataset, c.guidance, c.reviews, c.chosen_at, c.git_commit, c.model, c.prompt_version,
       c.n_reviews, c.n_reviews_matched, c.n_sections, c.n_matched, c.n_unmatched, c.n_no_answer
FROM corpus.current_versions AS v
JOIN corpus.choice_versions AS c ON c.revision = v.revision
ORDER BY v.dataset, c.guidance
"""
CHOICES_COLUMNS = (
    "guidance",
    "reviews",
    "chosen_at",
    "git_commit",
    "model",
    "prompt_version",
    "n_reviews",
    "n_reviews_matched",  # reviews given a template
    "n_sections",
    "n_matched",
    "n_unmatched",
    "n_no_answer",
)

# Each guidance document whose templates are stored (each is stored by the first candidates
# run against it).
TEMPLATES_SQL = """
SELECT g.guidance, g.title, g.source_url, g.loaded_at, count(DISTINCT t.template), count(ts.*)
FROM corpus.guidance_documents AS g
JOIN corpus.templates AS t ON t.guidance = g.guidance
JOIN corpus.template_sections AS ts ON ts.template = t.template
GROUP BY g.guidance, g.title, g.source_url, g.loaded_at
ORDER BY g.guidance DESC
"""
TEMPLATES_COLUMNS = ("guidance", "title", "source_url", "loaded_at", "n_templates", "n_sections")

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
    """Map a database row to named columns and serialise values for the app response."""
    return {
        name: value.isoformat() if hasattr(value, "isoformat") else value
        for name, value in zip(columns, row, strict=True)
    }


def describe(
    versions: dict,
    coverage: dict,
    snapshot: dict | None,
    sections: dict | None = None,
    candidates: dict | None = None,
    templates: list | None = None,
    choices: dict | None = None,
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
                "candidates": (candidates or {}).get(name, []),
                "choices": (choices or {}).get(name, []),
            }
        )
    return {
        "datasets": datasets,
        "case_metadata": snapshot,
        "templates": templates or [],
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
        candidates: dict[str, list] = {}
        for row in conn.execute(CANDIDATES_SQL):
            candidates.setdefault(row[0], []).append(as_dict(CANDIDATES_COLUMNS, row[1:]))
        templates = [as_dict(TEMPLATES_COLUMNS, row) for row in conn.execute(TEMPLATES_SQL)]
        choices: dict[str, list] = {}
        for row in conn.execute(CHOICES_SQL):
            choices.setdefault(row[0], []).append(as_dict(CHOICES_COLUMNS, row[1:]))
        row = conn.execute(SNAPSHOT_SQL).fetchone()
    snapshot = as_dict(SNAPSHOT_COLUMNS, row) if row else None
    return describe(versions, coverage, snapshot, sections, candidates, templates, choices)
